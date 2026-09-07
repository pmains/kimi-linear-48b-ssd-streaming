#!/usr/bin/env python3
"""Step 9 — agent-path timed turn runner (retained driver).

Spawns one headless agent turn against the LIVE gateway agent under test:

    openclaw agent --agent <agent> --session-key <key> \
                   --message-file <promptfile> --json --timeout <N>

(no --deliver: nothing is sent to any channel). Captures:
  - full stdout, timestamped:        <outdir>/<label>.timed.out
  - full stderr:                     <outdir>/<label>.err
  - extracted reply text:            <outdir>/<label>.reply.txt
  - client-side summary:             <outdir>/<label>.client.json
    { rc, wall_s, n_lines, extraction, reply_chars }

Reply extraction is best-effort: if stdout parses as a single JSON document
(or per-line JSON) the runner walks it for the most likely reply field(s);
otherwise it falls back to content after the CLI's last header line. The raw
output is always preserved, so scoring never depends on extraction quality.

Usage: service_step09_turn.py <outdir> <label> <promptfile> <session-key>
                              [--agent <id>] [--timeout <sec>]
"""
import json
import os
import subprocess
import sys
import time

OUTDIR = sys.argv[1]
LABEL = sys.argv[2]
PROMPT_FILE = sys.argv[3]
SESSION_KEY = sys.argv[4]
AGENT = "kimi"
TIMEOUT = 1200
args = sys.argv[5:]
if "--agent" in args:
    AGENT = args[args.index("--agent") + 1]
if "--timeout" in args:
    TIMEOUT = int(args[args.index("--timeout") + 1])

REPLY_KEYS = ("reply", "response", "output", "text", "content", "message", "result", "final")


def _walk(obj):
    """Return (key, value) pairs for every string found in a JSON structure."""
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
    # 1) whole stdout is one JSON doc
    try:
        doc = json.loads(stdout)
        cands = _walk(doc)
        for key in REPLY_KEYS:
            for k, v in cands:
                if k == key and v.strip():
                    return v.strip(), "json:key=" + key
        # prefer the longest non-trivial string
        if cands:
            best = max((v for _, v in cands if len(v.strip()) > 1), key=len, default="")
            if best:
                return best.strip(), "json:longest"
    except (ValueError, TypeError):
        pass
    # 2) per-line JSON (NDJSON or interleaved progress)
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
    # 3) last non-empty line after the final header-looking line
    body = []
    for ln in stdout.splitlines():
        if ln.startswith("outputs:") or ln.startswith("result:") or ln.startswith("reply:"):
            body = []
        elif ln.strip():
            body.append(ln)
    if body:
        return "\n".join(body).strip(), "tail"
    # 4) everything non-empty
    tail = "\n".join(ln for ln in stdout.splitlines() if ln.strip()).strip()
    return (tail, "raw") if tail else ("", "empty")


cmd = [
    "openclaw", "agent",
    "--agent", AGENT,
    "--model", "llama-server/kimi-linear-48b",  # allow-listed id for the SAME live endpoint (127.0.0.1:18080/v1) as kimi-local; pin required because agent kimi's default primary is deepseek (cloud) and kimi-local/kimi-linear-48b is NOT in agents.defaults.modelPolicy.allow
    "--session-key", SESSION_KEY,
    "--message-file", PROMPT_FILE,
    "--json",
    "--timeout", str(TIMEOUT),
]

t0 = time.monotonic()
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

os.makedirs(OUTDIR, exist_ok=True)
with open(os.path.join(OUTDIR, LABEL + ".timed.out"), "w", encoding="utf-8") as f:
    for ts, ln in stdout_lines:
        f.write("%.6f\t%s" % (ts, ln))
with open(os.path.join(OUTDIR, LABEL + ".out"), "w", encoding="utf-8") as f:
    for _, ln in stdout_lines:
        f.write(ln)
with open(os.path.join(OUTDIR, LABEL + ".err"), "w", encoding="utf-8") as f:
    f.write(err)

stdout = "".join(ln for _, ln in stdout_lines)
reply, method = extract_reply(stdout)
with open(os.path.join(OUTDIR, LABEL + ".reply.txt"), "w", encoding="utf-8") as f:
    f.write(reply + "\n" if reply else "")

summary = {
    "label": LABEL,
    "agent": AGENT,
    "session_key": SESSION_KEY,
    "rc": rc,
    "wall_s": round(wall, 4),
    "n_lines": len(stdout_lines),
    "extraction": method,
    "reply_chars": len(reply),
    "timed_out": wall >= TIMEOUT - 1,
}
with open(os.path.join(OUTDIR, LABEL + ".client.json"), "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2)
print(json.dumps(summary))
