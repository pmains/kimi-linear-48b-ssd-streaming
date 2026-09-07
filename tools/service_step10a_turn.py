#!/usr/bin/env python3
"""Step 10A - instrumented real-path turn runner (retained, 2026-09-04).

Runs ONE headless agent turn through the REAL OpenClaw path
(openclaw agent --agent kimi --model llama-server/kimi-linear-48b, no
--deliver, fresh session key) while capturing the evidence needed to
attribute wall time to phases:

  - client.json: rc, wall_s, reply extraction (same as service_step09_turn.py)
  - final doc (CLI stdout JSON): durationMs, agentMeta {contextTokens,
    contextTokensSource, usage, lastCallUsage, promptTokens},
    systemPromptReport (chars: system/project/tools/skills, bootstrap limits),
    error {kind, message}, executionTrace {attempts, fallbackUsed},
    replayInvalid, livenessState
  - llama-server log window: all lines appended to the server log during the
    turn (slot launch / print_timing prompt-eval+eval ms + token counts /
    release) -> prefill vs decode per server task
  - gateway log window: all lines appended to the gateway log during the turn
    (model-fetch start/response with elapsedMs) -> client-side per-call
    dispatch/response timing
  - /slots snapshots before/after (cache state covariates)
  - record.json: parsed per-turn summary

Usage:
  service_step10a_turn.py <outdir> <label> <promptfile> <session-key>
      --agent <id> --timeout <sec>
      --llama-log <path> --gateway-log <path>
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

OUTDIR = sys.argv[1]
LABEL = sys.argv[2]
PROMPT_FILE = sys.argv[3]
SESSION_KEY = sys.argv[4]
AGENT = "kimi"
TIMEOUT = 1200
LLAMA_LOG = "/tmp/kimi-llama-server.launchd.log"
GATEWAY_LOG = os.path.expanduser("~/Library/Logs/openclaw/gateway.log")
SLOTS_URL = "http://127.0.0.1:18080/slots"
args = sys.argv[5:]
if "--agent" in args:
    AGENT = args[args.index("--agent") + 1]
if "--timeout" in args:
    TIMEOUT = int(args[args.index("--timeout") + 1])
if "--llama-log" in args:
    LLAMA_LOG = args[args.index("--llama-log") + 1]
if "--gateway-log" in args:
    GATEWAY_LOG = args[args.index("--gateway-log") + 1]

REPLY_KEYS = ("reply", "response", "output", "text", "content", "message", "result", "final")


def _walk(obj):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                found.append((k, v))
            else:
                found.extend(_walk(v))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(_walk(v))
    return found


def extract_reply(stdout):
    """Best-effort reply extraction. Returns (reply, method)."""
    try:
        doc = json.loads(stdout)
        cands = _walk(doc)
        for key in REPLY_KEYS:
            for k, v in cands:
                if k == key and v.strip():
                    return v.strip(), "json:key=" + key
        if cands:
            best = max((v for _, v in cands if len(v.strip()) > 1), key=len, default="")
            if best:
                return best.strip(), "json:longest"
    except (ValueError, TypeError):
        pass
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                doc = json.loads(line)
                cands = _walk(doc)
                for key in REPLY_KEYS:
                    for k, v in cands:
                        if k == key and v.strip():
                            return v.strip(), "ndjson:key=" + key
            except (ValueError, TypeError):
                continue
    body = []
    for ln in stdout.splitlines():
        if ln.startswith("outputs:") or ln.startswith("result:") or ln.startswith("reply:"):
            body = []
        elif ln.strip():
            body.append(ln)
    if body:
        return "\n".join(body).strip(), "tail"
    tail = "\n".join(ln for ln in stdout.splitlines() if ln.strip()).strip()
    return (tail, "raw") if tail else ("", "empty")


def slots_snapshot():
    try:
        with urllib.request.urlopen(SLOTS_URL, timeout=5) as r:
            d = json.load(r)
        s = d[0]
        return {"is_processing": s.get("is_processing"),
                "n_prompt_tokens": s.get("n_prompt_tokens"),
                "n_prompt_tokens_cache": s.get("n_prompt_tokens_cache"),
                "id_task": s.get("id_task")}
    except Exception as e:
        return {"error": str(e)}


def log_window(path, start_off):
    """Return (lines_added, new_offset). Handles rotation (shrunk file)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], 0
    if size < start_off:
        start_off = 0  # rotated/truncated
    with open(path, "rb") as f:
        f.seek(start_off)
        data = f.read(size - start_off)
    return data.decode("utf-8", "replace"), size


os.makedirs(OUTDIR, exist_ok=True)

slots_before = slots_snapshot()
llama_off = os.path.getsize(LLAMA_LOG) if os.path.exists(LLAMA_LOG) else 0
gw_off = os.path.getsize(GATEWAY_LOG) if os.path.exists(GATEWAY_LOG) else 0

cmd = [
    "openclaw", "agent",
    "--agent", AGENT,
    "--model", "llama-server/kimi-linear-48b",
    "--session-key", SESSION_KEY,
    "--message-file", PROMPT_FILE,
    "--json",
    "--timeout", str(TIMEOUT),
]

t0 = time.monotonic()
t0_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout_lines = []
assert proc.stdout is not None
for line in proc.stdout:
    now = time.monotonic() - t0
    stdout_lines.append((now, line))
proc.wait()
t1 = time.monotonic()
err = proc.stderr.read() if proc.stderr else ""
rc = proc.returncode
wall = t1 - t0
t1_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

llama_window, llama_off2 = log_window(LLAMA_LOG, llama_off)
gw_window, gw_off2 = log_window(GATEWAY_LOG, gw_off)
slots_after = slots_snapshot()

stdout = "".join(ln for _, ln in stdout_lines)
reply, method = extract_reply(stdout)

with open(os.path.join(OUTDIR, LABEL + ".out"), "w", encoding="utf-8") as f:
    f.write(stdout)
with open(os.path.join(OUTDIR, LABEL + ".err"), "w", encoding="utf-8") as f:
    f.write(err)
with open(os.path.join(OUTDIR, LABEL + ".reply.txt"), "w", encoding="utf-8") as f:
    f.write(reply + "\n" if reply else "")
with open(os.path.join(OUTDIR, LABEL + ".llama.window.log"), "w", encoding="utf-8") as f:
    f.write(llama_window)
with open(os.path.join(OUTDIR, LABEL + ".gateway.window.log"), "w", encoding="utf-8") as f:
    f.write(gw_window)
with open(os.path.join(OUTDIR, LABEL + ".slots.json"), "w") as f:
    json.dump({"before": slots_before, "after": slots_after}, f, indent=1)

summary = {
    "label": LABEL,
    "agent": AGENT,
    "session_key": SESSION_KEY,
    "rc": rc,
    "wall_s": round(wall, 4),
    "t0_utc": t0_utc,
    "t1_utc": t1_utc,
    "n_lines": len(stdout_lines),
    "extraction": method,
    "reply_chars": len(reply),
    "timed_out": wall >= TIMEOUT - 1,
    "slots_before": slots_before,
    "slots_after": slots_after,
    "llama_window_chars": len(llama_window),
    "gateway_window_chars": len(gw_window),
}
with open(os.path.join(OUTDIR, LABEL + ".client.json"), "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2)

# ---- parse the final CLI doc for phase metadata ----
doc = None
try:
    doc = json.loads(stdout)
except Exception:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                doc = json.loads(line)
                break
            except Exception:
                continue
record = {"summary": summary}
if doc:
    res = doc.get("result", {}) if isinstance(doc, dict) else {}
    meta = res.get("meta", {}) if isinstance(res, dict) else {}
    agent_meta = meta.get("agentMeta", {})
    record["doc_status"] = doc.get("status")
    record["doc_summary"] = doc.get("summary")
    record["duration_ms"] = meta.get("durationMs")
    record["agentMeta"] = {
        "contextTokens": agent_meta.get("contextTokens"),
        "contextTokensSource": agent_meta.get("contextTokensSource"),
        "promptTokens": agent_meta.get("promptTokens"),
        "usage": agent_meta.get("usage"),
        "lastCallUsage": agent_meta.get("lastCallUsage"),
    }
    spr = meta.get("systemPromptReport", {})
    record["systemPromptReport"] = {
        "systemPromptChars": (spr.get("systemPrompt") or {}).get("chars"),
        "projectContextChars": (spr.get("systemPrompt") or {}).get("projectContextChars"),
        "toolsSchemaChars": (spr.get("tools") or {}).get("schemaChars"),
        "toolsListChars": (spr.get("tools") or {}).get("listChars"),
        "skillsPromptChars": (spr.get("skills") or {}).get("promptChars"),
        "bootstrapMaxChars": spr.get("bootstrapMaxChars"),
        "bootstrapTotalMaxChars": spr.get("bootstrapTotalMaxChars"),
    }
    record["error"] = meta.get("error")
    record["replayInvalid"] = meta.get("replayInvalid")
    record["livenessState"] = meta.get("livenessState")
    record["executionTrace"] = meta.get("executionTrace")
    record["finalPromptText"] = meta.get("finalPromptText")

with open(os.path.join(OUTDIR, LABEL + ".record.json"), "w", encoding="utf-8") as f:
    json.dump(record, f, indent=1, default=str)

print(json.dumps(summary))
