#!/usr/bin/env bash
# Step 10D — focused live validation leg (retained, 2026-09-05).
#
# Runs the Step 10D validation probes through the REAL OpenClaw agent path at
# the aligned 64K configuration with tools.loopDetection.enabled:true live:
#   LOOP x3  engineered identical-read loop (nonexistent file, "retry the
#            identical read until success") -> must be terminated by the
#            general detector, NOT by the 600s client timeout
#   P1  x2   retained anchor prompt (the P1-r5 workload probe)
#   P2  x2   retained anchor prompt (the P2-r7 workload probe)
#   NORM x2  genuine multi-step tool task (read/exec/progress_card) -> rc=0,
#            zero loop events (no false positive)
#
# Env: STEP10D_OUT (default benchmarks/results/service-step-10d),
#      STEP10D_KEYNS (default step10d), STEP10D_TIMEOUT (default 600),
#      per-probe rep counts STEP10D_LOOP_REPS/STEP10D_P_REPS/STEP10D_NORM_REPS.
# Resumable: existing <label>.client.json skips that rep.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_ROOT="${STEP10D_OUT:-$ROOT/benchmarks/results/service-step-10d}"
OUT="$OUT_ROOT/64k"
TURN="$ROOT/tools/service_step10a_turn.py"
AGENT=kimi
KEYNS="${STEP10D_KEYNS:-step10d}"
TIMEOUT="${STEP10D_TIMEOUT:-600}"
LOOP_REPS="${STEP10D_LOOP_REPS:-3}"
P_REPS="${STEP10D_P_REPS:-2}"
NORM_REPS="${STEP10D_NORM_REPS:-2}"
LLAMA_LOG=/tmp/kimi-llama-server.launchd.log
GATEWAY_LOG="$HOME/Library/Logs/openclaw/gateway.log"
PROM09="$ROOT/benchmarks/results/service-step-09/prompts"
PROM10D="$OUT_ROOT/prompts"

mkdir -p "$OUT"

n_ctx_now() {
    curl -s -m 3 http://127.0.0.1:18080/slots 2>/dev/null | python3 -c \
        "import json,sys; print(json.load(sys.stdin)[0]['n_ctx'])" 2>/dev/null || echo 0
}

if [ "$(n_ctx_now)" != "65536" ]; then
    echo "ABORT: expected 64K server on 18080 (n_ctx=65536), got $(n_ctx_now)" >&2
    exit 4
fi

{
    echo "captured_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "gateway_pid=$(launchctl list 2>/dev/null | awk '$3==\"ai.openclaw.gateway\" {print $1}' | head -1)"
    echo "server_health=$(curl -s -m 3 http://127.0.0.1:18080/health)"
    echo "n_ctx=$(n_ctx_now)"
    echo "timeout=$TIMEOUT loop_reps=$LOOP_REPS p_reps=$P_REPS norm_reps=$NORM_REPS"
    echo "loopDetection=$(python3 -c "import json;print(json.load(open('$HOME/.openclaw/openclaw.json'))['agents']['entries']['kimi']['tools']['loopDetection'])" 2>/dev/null)"
} > "$OUT_ROOT/env.txt"

run_rep() { # probe label promptfile
    local probe="$1" label="$2" promptfile="$3"
    local dir="$OUT/$probe"
    mkdir -p "$dir"
    local key="agent:$AGENT:$KEYNS-64k-$label"
    if [ -f "$dir/$label.client.json" ]; then
        echo "[$label] cached"
    else
        echo "[$label] running $(date -u +%H:%M:%SZ)"
        python3 "$TURN" "$dir" "$label" "$promptfile" "$key" \
            --agent "$AGENT" --timeout "$TIMEOUT" \
            --llama-log "$LLAMA_LOG" --gateway-log "$GATEWAY_LOG" \
            > "$dir/$label.turn.stdout" 2> "$dir/$label.turn.stderr"
        rc=$(python3 -c "import json;print(json.load(open('$dir/$label.client.json'))['rc'])" 2>/dev/null)
        wall=$(python3 -c "import json;print(json.load(open('$dir/$label.client.json'))['wall_s'])" 2>/dev/null)
        echo "[$label] rc=$rc wall=${wall}s"
    fi
}

i=1
while [ "$i" -le "$LOOP_REPS" ]; do run_rep LOOP "LOOP-r$i" "$PROM10D/LOOP.md"; i=$((i+1)); done
i=1
while [ "$i" -le "$P_REPS" ]; do run_rep P1 "P1-r$i" "$PROM09/P1.md"; run_rep P2 "P2-r$i" "$PROM09/P2.md"; i=$((i+1)); done
i=1
while [ "$i" -le "$NORM_REPS" ]; do run_rep NORM "NORM-r$i" "$PROM10D/NORM.md"; i=$((i+1)); done

echo "DONE: 10D leg -> $OUT"
