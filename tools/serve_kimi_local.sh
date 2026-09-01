#!/bin/bash
# Serve Kimi-Linear-48B-A3B-Instruct with the SSD-backed expert-streaming
# runtime (llama.cpp llama-server, OpenAI-compatible /v1 API) for OpenClaw.
#
# DEV/LIVE MODEL (how dev stays separate from live):
#   - llama.cpp/ is the DEV tree; rebuild it freely for future optimizations.
#   - runtime/live/ is the FROZEN live bundle (binary + dylibs + backend .so
#     plugins, rpath rewritten to @loader_path). The live server runs ONLY
#     from this bundle, so dev rebuilds can never change what agents are
#     served. Provenance: runtime/live/COMMIT (pinned llama.cpp rev).
#   - To promote a validated dev build: rebuild build-metal, then run
#     tools/freeze_live_runtime.sh, then restart this server.
#   - To A/B a dev build: KIMI_BIN=llama.cpp/build-metal/bin/llama-server
#     KIMI_PORT=18081 ./tools/serve_kimi_local.sh start  (unregistered port)
#
# Design (from Phase 4-8 measurements):
#   - expert cache budget: 4096 MiB default (Phase 8: 4 GB sweet spot for
#     code workloads; 8 GB for reasoning-heavy; raise via KIMI_CACHE_MB)
#   - zerocopy cache mode (Phase 6B baseline)
#   - ctx 8192: KV cache is cheap (7 attention layers ~0.23 GB f16 at 8k);
#     agent system prompts are large, 4096 is too small
#   - CPU (-ngl 0), no mmap, deterministic sampling params optional
#   - port 18080; API key not required (loopback)
#
# Usage:
#   tools/serve_kimi_local.sh [start|stop|status]
# Env:
#   KIMI_CACHE_MB=4096|8192|...   expert cache budget (default 4096)
#   KIMI_PORT=18080               server port
#   KIMI_CTX=65536                context size
#   KIMI_BIN=runtime/live/bin/llama-server (override binary, e.g. dev build)
set -euo pipefail

MODEL="models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf"
BIN="${KIMI_BIN:-runtime/live/bin/llama-server}"
PORT="${KIMI_PORT:-18080}"
CTX="${KIMI_CTX:-65536}"
CACHE_MB="${KIMI_CACHE_MB:-4096}"
NGL="${KIMI_NGL:-999}"
HOST="${KIMI_HOST:-127.0.0.1}"
LOG="/tmp/kimi-llama-server.log"
PIDFILE="/tmp/kimi-llama-server.pid"
STATE_DIR="runtime/state"
SLOT_SAVE_PATH="$STATE_DIR/slot-cache"

running_cmd() {
    pgrep -f -- "$BIN -m $MODEL -ngl $NGL --no-mmap --ctx-size $CTX --host $HOST --port $PORT --parallel 1" >/dev/null 2>&1
}

start() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "already running (pid $(cat "$PIDFILE"))"
        return 0
    fi
    if running_cmd; then
        echo "already running (matched process)"
        return 0
    fi
    echo "starting llama-server on $HOST:$PORT (ctx $CTX, expert cache ${CACHE_MB} MiB, zerocopy)"
    # Mutual exclusion: kimi and qwen3 share the 24 GB unified-memory budget.
    # Running both simultaneously caused 18+ GB of swap thrash (see
    # goldenrod-progress/2026-08-25-diagnosis-hardening.md). One or the other.
    "$(dirname "$0")/serve_qwen_local.sh" stop >/dev/null 2>&1 || true
    mkdir -p "$SLOT_SAVE_PATH"
    KIMI_STREAM_EXPERTS=naive \
    KIMI_EXPERT_CACHE_MB="$CACHE_MB" \
    KIMI_EXPERT_CACHE_MODE=zerocopy \
    KIMI_STREAM_METAL_STAGE=1 \
    KIMI_STREAM_E2_DIRECT_PLACE=1 \
    nohup "$BIN" -m "$MODEL" -ngl "$NGL" --no-mmap --ctx-size "$CTX" \
        --host "$HOST" --port "$PORT" --parallel 1 \
        --slot-save-path "$SLOT_SAVE_PATH" \
        > "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    for i in $(seq 1 60); do
        if curl -sf -m 2 "http://$HOST:$PORT/health" > /dev/null 2>&1; then
            echo "healthy after ~${i}s (pid $(cat "$PIDFILE"))"
            return 0
        fi
        sleep 1
    done
    echo "ERROR: server did not become healthy; see $LOG" >&2
    return 1
}

stop() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        kill "$(cat "$PIDFILE")"
        rm -f "$PIDFILE"
        echo "stopped"
        return 0
    fi
    if running_cmd; then
        pkill -f -- "$BIN -m $MODEL -ngl $NGL --no-mmap --ctx-size $CTX --host $HOST --port $PORT --parallel 1"
        rm -f "$PIDFILE"
        echo "stopped (matched process)"
        return 0
    else
        echo "not running"
    fi
}

status() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "running (pid $(cat "$PIDFILE")) on $HOST:$PORT"
        curl -s -m 2 "http://$HOST:$PORT/health" && echo
        return 0
    fi
    if running_cmd; then
        echo "running (matched process) on $HOST:$PORT"
        curl -s -m 2 "http://$HOST:$PORT/health" && echo
        return 0
    else
        echo "not running"
    fi
}

case "${1:-start}" in
    start) start ;;
    stop) stop ;;
    status) status ;;
    *) echo "usage: $0 [start|stop|status]" >&2; exit 2 ;;
esac
