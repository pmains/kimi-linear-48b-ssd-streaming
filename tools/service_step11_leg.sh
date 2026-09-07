#!/usr/bin/env bash
# Step 11 — sustained usable-context leg driver (retained, 2026-09-05).
#
# Runs three realistic same-key multi-turn sessions through the REAL OpenClaw
# agent path at the aligned 64K config (loop detection enabled):
#   R (research conversation, 10 turns)   key agent:kimi:step11-64k-research
#   C (coding work, 8 turns)              key agent:kimi:step11-64k-code
#   G (context growth -> auto-compaction, 12 turns)
#                                        key agent:kimi:step11-64k-growth
#
# Same session key across a session's turns => context accumulates and grows.
# Per-turn instrumentation via retained service_step10a_turn.py (llama window,
# gateway window, slots, final doc with promptTokens/usage, client.json).
#
# Env: STEP11_OUT (default benchmarks/results/service-step-11),
#      STEP11_TIMEOUT (default 1200), STEP11_KEYNS (default step11).
# Resumable: existing <label>.client.json skips that turn.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_ROOT="${STEP11_OUT:-$ROOT/benchmarks/results/service-step-11}"
OUT="$OUT_ROOT/64k"
PROM="$OUT_ROOT/prompts"
TURN="$ROOT/tools/service_step10a_turn.py"
AGENT=kimi
KEYNS="${STEP11_KEYNS:-step11}"
TIMEOUT="${STEP11_TIMEOUT:-1200}"
LLAMA_LOG=/tmp/kimi-llama-server.launchd.log
GATEWAY_LOG="$HOME/Library/Logs/openclaw/gateway.log"
mkdir -p "$OUT" "$OUT/R" "$OUT/C" "$OUT/G"

n_ctx_now() {
    curl -s -m 3 http://127.0.0.1:18080/slots 2>/dev/null | python3 -c \
        "import json,sys; print(json.load(sys.stdin)[0]['n_ctx'])" 2>/dev/null || echo 0
}
if [ "$(n_ctx_now)" != "65536" ]; then
    echo "ABORT: expected 64K server (n_ctx=65536), got $(n_ctx_now)" >&2
    exit 4
fi

{
    echo "captured_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "gateway_pid=$(launchctl list 2>/dev/null | awk '$3==\"ai.openclaw.gateway\" {print $1}' | head -1)"
    echo "server_health=$(curl -s -m 3 http://127.0.0.1:18080/health)"
    echo "n_ctx=$(n_ctx_now)"
    echo "loopDetection=$(python3 -c "import json;print(json.load(open('$HOME/.openclaw/openclaw.json'))['agents']['entries']['kimi']['tools']['loopDetection'])" 2>/dev/null)"
    echo "compaction=$(python3 -c "import json;print(json.dumps(json.load(open('$HOME/.openclaw/openclaw.json'))['agents']['defaults'].get('compaction')))" 2>/dev/null)"
} > "$OUT_ROOT/env.txt"

run_session() { # sessionkey prompts_dir reps
    local key="$1" pdir="$2" reps="$3" session
    session="$(basename "$pdir")"
    local i=1
    while [ "$i" -le "$reps" ]; do
        local label="${session}$i"
        local prompt="$pdir/$label.md"
        local dir="$OUT/$session"
        mkdir -p "$dir"
        if [ -f "$dir/$label.client.json" ]; then
            echo "[$label] cached"
        else
            echo "[$label] running $(date -u +%H:%M:%SZ)"
            python3 "$TURN" "$dir" "$label" "$prompt" "$key" \
                --agent "$AGENT" --timeout "$TIMEOUT" \
                --llama-log "$LLAMA_LOG" --gateway-log "$GATEWAY_LOG" \
                > "$dir/$label.turn.stdout" 2> "$dir/$label.turn.stderr"
            rc=$(python3 -c "import json;print(json.load(open('$dir/$label.client.json'))['rc'])" 2>/dev/null)
            wall=$(python3 -c "import json;print(json.load(open('$dir/$label.client.json'))['wall_s'])" 2>/dev/null)
            echo "[$label] rc=$rc wall=${wall}s"
        fi
        i=$((i + 1))
    done
}

run_session "agent:$AGENT:$KEYNS-64k-research" "$PROM/R" 10
run_session "agent:$AGENT:$KEYNS-64k-code" "$PROM/C" 8
run_session "agent:$AGENT:$KEYNS-64k-growth" "$PROM/G" 12

echo "DONE: step11 leg -> $OUT"
