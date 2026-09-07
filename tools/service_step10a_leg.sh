#!/usr/bin/env bash
# Step 10A — ctx leg driver (retained, 2026-09-04).
#
# Runs the P1/P2 real-path workload for one context window (64k or 128k)
# through the REAL OpenClaw agent path with full per-turn instrumentation
# (service_step10a_turn.py): llama-server log window, gateway log window,
# /slots snapshots, final CLI doc (agentMeta contextTokens/usage/error),
# client.json (rc/wall).
#
#   bash tools/service_step10a_leg.sh 64k   # current launchd server (65536)
#   bash tools/service_step10a_leg.sh 128k  # temp server --ctx-size 131072,
#                                           # launchd 64K restored afterwards
#
# Env: STEP10A_REPS (default 6 per probe), STEP10A_PROBES (default "P1 P2"),
#      STEP10A_KEEP=1 to leave the temp 128K server running (debug only).
# Resumable: an existing <probe>-r<N>.client.json skips that rep.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CTX="${1:?usage: service_step10a_leg.sh 64k|128k}"
SUITE="$ROOT/benchmarks/results/service-step-09"
OUT_ROOT="${STEP10A_OUT:-$ROOT/benchmarks/results/service-step-10a}"
OUT="$OUT_ROOT/$CTX"
TURN="$ROOT/tools/service_step10a_turn.py"
AGENT=kimi
REPS="${STEP10A_REPS:-6}"
PROBES="${STEP10A_PROBES:-P1 P2}"
LLAMA_LOG_64K=/tmp/kimi-llama-server.launchd.log
LLAMA_LOG_TMP="$OUT/llama-server-128k.log"
GATEWAY_LOG="$HOME/Library/Logs/openclaw/gateway.log"
PIDFILE=/tmp/kimi-llama-server.pid
MODEL="models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf"
BIN="$ROOT/runtime/live/bin/llama-server"
PLIST="$HOME/Library/LaunchAgents/com.openclaw.kimi-llama-server.plist"
SERVED_LABEL=com.openclaw.kimi-llama-server

mkdir -p "$OUT"

n_ctx_now() {
    curl -s -m 3 http://127.0.0.1:18080/slots 2>/dev/null | python3 -c \
        "import json,sys; print(json.load(sys.stdin)[0]['n_ctx'])" 2>/dev/null || echo 0
}

# ---------------- server setup per ctx ----------------
if [ "$CTX" = "64k" ]; then
    if [ "$(n_ctx_now)" != "65536" ]; then
        echo "ABORT: expected 64K server on 18080 (n_ctx=65536), got $(n_ctx_now)" >&2
        exit 4
    fi
    LLAMA_LOG="$LLAMA_LOG_64K"
    SERVER_NOTE="launchd 64K server (pid $(cat "$PIDFILE" 2>/dev/null))"
elif [ "$CTX" = "128k" ]; then
    SLOT_DIR="$ROOT/runtime/state/slot-cache-128k"
    mkdir -p "$SLOT_DIR"
    # EXIT trap: never leave production down if this script aborts mid-leg.
    restore_64k() {
        echo "[$(date -u +%H:%M:%SZ)] EXIT trap: restoring launchd 64K server"
        pkill -f "ctx-size 131072" 2>/dev/null
        sleep 2
        rm -f "$PIDFILE"
        launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null
        for i in $(seq 1 60); do
            [ "$(n_ctx_now)" = "65536" ] && break
            sleep 2
        done
        echo "[$(date -u +%H:%M:%SZ)] trap restore n_ctx=$(n_ctx_now)"
    }
    trap restore_64k EXIT
    echo "[$(date -u +%H:%M:%SZ)] stopping launchd 64K job for temp 128K leg"
    launchctl bootout "gui/$(id -u)/$SERVED_LABEL" 2>/dev/null
    sleep 3
    # ensure the port is actually free
    pkill -f "ctx-size 65536" 2>/dev/null
    sleep 2
    rm -f "$PIDFILE"
    echo "[$(date -u +%H:%M:%SZ)] starting temp 128K server (ctx 131072, expert cache ${KIMI_EXPERT_CACHE_MB:-8192}MB)"
    KIMI_CTX=131072 \
    KIMI_EXPERT_CACHE_MB="${KIMI_EXPERT_CACHE_MB:-8192}" \
    KIMI_EXPERT_READ_WORKERS=4 \
    KIMI_STREAM_METAL_STAGE=1 \
    KIMI_STREAM_E2_DIRECT_PLACE=1 \
    KIMI_STREAM_STATS_FILE="$OUT/stats.csv" \
    KIMI_STREAM_RETR_FILE="$OUT/retr.csv" \
    KIMI_STREAM_MEM_FILE="$OUT/mem.csv" \
    KIMI_STREAM_CACHE_LAYERS_FILE="$OUT/cache_layers.csv" \
    "$BIN" -m "$MODEL" -ngl 999 --no-mmap --ctx-size 131072 \
        --host 127.0.0.1 --port 18080 --parallel 1 \
        --slot-save-path "$SLOT_DIR" \
        >> "$LLAMA_LOG_TMP" 2>&1 &
    echo $! > "$PIDFILE"
    # wait for health + n_ctx (model load can take minutes; allow 300s)
    for i in $(seq 1 150); do
        [ "$(n_ctx_now)" = "131072" ] && break
        sleep 2
    done
    if [ "$(n_ctx_now)" != "131072" ]; then
        echo "ABORT: temp 128K server failed to come up (see $LLAMA_LOG_TMP)" >&2
        exit 5
    fi
    echo "[$(date -u +%H:%M:%SZ)] temp 128K server ready (pid $(cat "$PIDFILE"))"
    LLAMA_LOG="$LLAMA_LOG_TMP"
    SERVER_NOTE="temp 128K server (pid $(cat "$PIDFILE"))"
else
    echo "ABORT: ctx must be 64k or 128k" >&2
    exit 2
fi

# ---------------- environment snapshot ----------------
{
    echo "captured_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "ctx=$CTX"
    echo "server_note=$SERVER_NOTE"
    echo "llama_log=$LLAMA_LOG"
    echo "gateway_pid=$(launchctl list 2>/dev/null | awk '$3=="ai.openclaw.gateway" {print $1}' | head -1)"
    echo "gateway_start=$(ps -o lstart= -p "$(launchctl list 2>/dev/null | awk '$3=="ai.openclaw.gateway" {print $1}' | head -1)" 2>/dev/null)"
    echo "server_health=$(curl -s -m 3 http://127.0.0.1:18080/health)"
    echo "n_ctx=$(n_ctx_now)"
    echo "reps=$REPS probes=$PROBES"
} > "$OUT/env.txt"

# ---------------- per-rep instrumented turns ----------------
for probe in $PROBES; do
    prompt="$SUITE/prompts/$probe.md"
    dir="$OUT/$probe"
    mkdir -p "$dir"
    i=1
    while [ "$i" -le "$REPS" ]; do
        label="$probe-r$i"
        key="agent:$AGENT:${STEP10A_KEYNS:-step10a}-$CTX-$label"
        if [ -f "$dir/$label.client.json" ]; then
            echo "[$CTX/$label] cached"
        else
            echo "[$CTX/$label] running (real path, no --deliver) $(date -u +%H:%M:%SZ)"
            python3 "$TURN" "$dir" "$label" "$prompt" "$key" \
                --agent "$AGENT" --timeout 1200 \
                --llama-log "$LLAMA_LOG" --gateway-log "$GATEWAY_LOG" \
                > "$dir/$label.turn.stdout" 2> "$dir/$label.turn.stderr"
            rc=$(python3 -c "import json;print(json.load(open('$dir/$label.client.json'))['rc'])" 2>/dev/null)
            wall=$(python3 -c "import json;print(json.load(open('$dir/$label.client.json'))['wall_s'])" 2>/dev/null)
            echo "[$CTX/$label] rc=$rc wall=${wall}s"
        fi
        i=$((i + 1))
    done
done

# ---------------- restore 64K launchd server after 128K leg ----------------
if [ "$CTX" = "128k" ] && [ "${STEP10A_KEEP:-0}" != "1" ]; then
    echo "[$(date -u +%H:%M:%SZ)] stopping temp 128K server; restoring launchd 64K"
    kill "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null
    sleep 3
    rm -f "$PIDFILE"
    pkill -f "ctx-size 131072" 2>/dev/null
    sleep 2
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null
    for i in $(seq 1 60); do
        [ "$(n_ctx_now)" = "65536" ] && break
        sleep 2
    done
    if [ "$(n_ctx_now)" = "65536" ]; then
        echo "[$(date -u +%H:%M:%SZ)] 64K launchd server restored and verified"
    else
        echo "[$(date -u +%H:%M:%SZ)] WARNING: 64K server not verified (n_ctx=$(n_ctx_now))" >&2
    fi
fi

echo "DONE: $CTX leg -> $OUT"
