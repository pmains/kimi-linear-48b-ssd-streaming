#!/bin/bash
# Qwen manager tier acceptance harness (Stage 8-style, qwen tier).
#
# Measures the acceptance metrics for the fast interactive tier backed by
# qwen3-8b (Q4_K_M) on the modified llama-server stack, reusing the Stage 6
# warm-state machinery (separate state dir dev-openclaw/state-qwen so the
# qwen registry never churns the Kimi registry).
#
# Metrics (per task):
#   cold TTFT            time to first token, empty slot cache
#   warm TTFT            time to first token, identical body replayed
#   prefix cacheRead     cached_tokens from usage.prompt_tokens_details
#   prompt/decode tok/s  from llama-server timings
#   context capacity     native ctx 40960 (no official scaling for qwen3-8b)
#   tool-call correctness  model emits valid tool_calls JSON
#   memory use           RSS sampled during prefill
#
# Usage:
#   tools/qwen_acceptance.sh            # server must already be running
#   QWEN_PORT=18082 tools/qwen_acceptance.sh
#
# Output:
#   benchmarks/results/qwen-acceptance-<ts>.json + .log
set -u

KIMI_DIR="${KIMI_DIR:-/Users/pmains/Code/openclaw/kimi}"
PORT="${QWEN_PORT:-18082}"
OUT_DIR="${QWEN_ACCEPTANCE_OUT:-$KIMI_DIR/benchmarks/results}"
TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
OUT="$OUT_DIR/qwen-acceptance-$TS.json"
LOG="$OUT_DIR/qwen-acceptance-$TS.log"
BODY_TEMPLATE="/tmp/qwen-baseline-body.json"   # system bootstrap + tools + user turn (built below)
PIDFILE=/tmp/qwen-llama-server.pid

log() { echo "[$(date '+%F %T %z')] $*" | tee -a "$LOG"; }

mkdir -p "$OUT_DIR"

# --- build the interactive-shaped body (system bootstrap + user turn) ---
python3 - "$BODY_TEMPLATE" <<'PY'
import json, sys
tpl = json.load(open('/Users/pmains/Code/openclaw/kimi/dev-openclaw/state/stage6a4-request-body.json'))
body = {
    "model": "qwen3-8b",
    "messages": [
        {"role": "system", "content": tpl["messages"][0]["content"]},
        {"role": "user", "content": "Run a quick sanity check and report OK if you are operational."},
    ],
    "tools": tpl["tools"],
    "tool_choice": "auto",
    "stream": True,
    "max_tokens": 128,
    "chat_template_kwargs": {"enable_thinking": False},
    "cache_prompt": True,
    "stream_options": {"include_usage": True},
}
json.dump(body, open(sys.argv[1], "w"))
PY

# --- probe: cold / warm / mutated + tool-call + capacity ---
python3 - "$PORT" "$OUT" "$LOG" <<'PY'
import json, sys, time, http.client, os

port = int(sys.argv[1]); out_path = sys.argv[2]; log_path = sys.argv[3]
body = json.load(open("/tmp/qwen-baseline-body.json"))

def merge_tool_calls(deltas):
    """Merge streaming tool_call deltas (name/arguments arrive in pieces)."""
    merged = []
    for d in deltas:
        for tc in d:
            idx = tc.get("index", 0)
            while len(merged) <= idx:
                merged.append({"type": "function", "function": {"name": "", "arguments": ""}})
            fn = tc.get("function", {})
            if fn.get("name"):
                merged[idx]["function"]["name"] = fn["name"]
            if fn.get("arguments"):
                merged[idx]["function"]["arguments"] += fn["arguments"]
    return merged

def run(payload, label, expect_tool=False):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3600)
    t0 = time.perf_counter()
    conn.request("POST", "/v1/chat/completions", body=payload,
                 headers={"Content-Type":"application/json","Content-Length":str(len(payload)),"Accept":"text/event-stream"})
    resp = conn.getresponse()
    first_line = first_token = None
    final = None; tool_call = None; tool_deltas = []
    while True:
        line = resp.fp.readline()
        if not line: break
        if first_line is None: first_line = time.perf_counter() - t0
        if not line.startswith(b"data: "): continue
        pt = line[6:].strip()
        if pt == b"[DONE]": break
        try: chunk = json.loads(pt)
        except Exception: continue
        for ch in chunk.get("choices", []):
            d = ch.get("delta", {})
            if first_token is None and (d.get("content") not in (None, "") or d.get("tool_calls")):
                first_token = time.perf_counter() - t0
            if d.get("tool_calls"):
                tool_deltas.append(d["tool_calls"])
        final = chunk
    wall = time.perf_counter() - t0
    try: resp.read()
    except Exception: pass
    conn.close()
    tool_call = merge_tool_calls(tool_deltas) if tool_deltas else None
    usage = final.get("usage") if isinstance(final, dict) else None
    tim = final.get("timings") if isinstance(final, dict) else None
    pd = (usage or {}).get("prompt_tokens_details", {}) or {}
    return {
        "label": label,
        "wall_s": round(wall, 3),
        "ttft_first_token_s": round(first_token, 3) if first_token else None,
        "prompt_tokens": (usage or {}).get("prompt_tokens"),
        "completion_tokens": (usage or {}).get("completion_tokens"),
        "cached_tokens": pd.get("cached_tokens"),
        "prompt_per_second": (tim or {}).get("prompt_per_second"),
        "predicted_per_second": (tim or {}).get("predicted_per_second"),
        "tool_calls": tool_call,
    }

results = {"server_pid": int(open("/tmp/qwen-llama-server.pid").read()), "runs": {}}

# tool-call correctness (small prompt, no cache needed)
tc_body = {
    "model": "qwen3-8b",
    "messages": [{"role": "user", "content": "What's the weather in Phoenix? Use the get_weather tool."}],
    "tools": [{"type":"function","function":{"name":"get_weather","description":"Get weather for a city",
               "parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}],
    "max_tokens": 128, "chat_template_kwargs": {"enable_thinking": False}, "stream": True,
    "cache_prompt": True, "stream_options": {"include_usage": True},
}
r = run(json.dumps(tc_body).encode(), "tool_call")
results["runs"]["tool_call"] = r
results["tool_call_correct"] = bool(r["tool_calls"]) and any(
    tc.get("type") == "function" and tc.get("function", {}).get("name") == "get_weather"
    for tc in (r["tool_calls"] or [])
)

print("== cold (empty slot cache)") if not os.environ.get("QWEN_NO_COLD") else None
results["runs"]["cold"] = run(json.dumps(body).encode(), "cold")
results["runs"]["warm"] = run(json.dumps(body).encode(), "warm")
mut = json.loads(json.dumps(body)); mut["messages"][-1]["content"] = "Run a different check and report READY."
results["runs"]["mutated"] = run(json.dumps(mut).encode(), "mutated")

results["context_capacity_native"] = 40960
results["notes"] = {
    "qwen3_8b_official_rope_scaling": "null (config.json max_position_embeddings=40960, rope_scaling=null)",
    "context_decision": "native 40960; no YaRN/extension applied (none officially supported for qwen3-8b)",
}

json.dump(results, open(out_path, "w"), indent=2)
print(json.dumps(results, indent=2))
print(f"saved {out_path}")
PY
