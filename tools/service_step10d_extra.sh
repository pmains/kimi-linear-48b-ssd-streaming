#!/bin/bash
# Step 10D supplementary probes (retained 2026-09-05): clean NORM rep with
# absolute paths + deterministic LOOP-STRICT identical-read reps, so
# confirmation (1) [loop terminated by detector] and (2) [legit multi-step
# use completes] are exercised live. Test prompts only - no config/code change.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_ROOT="${STEP10D_OUT:-$ROOT/benchmarks/results/service-step-10d}"
OUT="$OUT_ROOT/64k"
TURN="$ROOT/tools/service_step10a_turn.py"
AGENT=kimi
KEYNS="${STEP10D_KEYNS:-step10d}"
TIMEOUT=600
LLAMA_LOG=/tmp/kimi-llama-server.launchd.log
GATEWAY_LOG="$HOME/Library/Logs/openclaw/gateway.log"
PROM="$OUT_ROOT/prompts"
run_rep() {
    local probe="$1" label="$2" promptfile="$3"
    local dir="$OUT/$probe"; mkdir -p "$dir"
    local key="agent:$AGENT:$KEYNS-64k-$label"
    [ -f "$dir/$label.client.json" ] && { echo "[$label] cached"; return; }
    echo "[$label] running $(date -u +%H:%M:%SZ)"
    python3 "$TURN" "$dir" "$label" "$promptfile" "$key" \
        --agent "$AGENT" --timeout "$TIMEOUT" \
        --llama-log "$LLAMA_LOG" --gateway-log "$GATEWAY_LOG" \
        > "$dir/$label.turn.stdout" 2> "$dir/$label.turn.stderr"
    rc=$(python3 -c "import json;print(json.load(open('$dir/$label.client.json'))['rc'])" 2>/dev/null)
    wall=$(python3 -c "import json;print(json.load(open('$dir/$label.client.json'))['wall_s'])" 2>/dev/null)
    echo "[$label] rc=$rc wall=${wall}s"
}
run_rep NORM "NORM-r3" "$PROM/NORM-ABS.md"
run_rep LOOP-STRICT "LOOP-STRICT-r1" "$PROM/LOOP-STRICT.md"
run_rep LOOP-STRICT "LOOP-STRICT-r2" "$PROM/LOOP-STRICT.md"
echo "EXTRA DONE -> $OUT"
