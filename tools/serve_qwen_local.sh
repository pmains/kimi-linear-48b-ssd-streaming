#!/bin/bash
# Serve Qwen3-8B-Instruct (local GGUF) with the modified llama.cpp
# llama-server stack for OpenClaw's fast interactive/manager tier.
#
# Reuses the SAME frozen runtime bundle and slot-save-path prompt-cache
# machinery as the Kimi live server (Stage 6 warm-state infrastructure).
# This instance is independent: separate port, model, slot cache, pidfile.
# Kimi config/behavior is untouched (see tools/serve_kimi_local.sh).
#
# Usage:
#   tools/serve_qwen_local.sh [start|stop|status]
# Env:
#   QWEN_PORT=18082        server port
#   QWEN_CTX=40960         context size (native GGUF context_length)
#   QWEN_NGL=99            Metal offload layers (0 = CPU only)
#   QWEN_BIN=runtime/live/bin/llama-server (override binary)
set -euo pipefail

MODEL="models/qwen3/qwen3-8b-q4_k_m.gguf"
BIN="${QWEN_BIN:-runtime/live/bin/llama-server}"
PORT="${QWEN_PORT:-18082}"
CTX="${QWEN_CTX:-40960}"
NGL="${QWEN_NGL:-99}"
HOST="${QWEN_HOST:-127.0.0.1}"
LOG="/tmp/qwen-llama-server.log"
PIDFILE="/tmp/qwen-llama-server.pid"
STATE_DIR="runtime/state"
SLOT_SAVE_PATH="$STATE_DIR/qwen-slot-cache"

running_cmd() {
    pgrep -f -- "$BIN -m $MODEL -ngl $NGL --ctx-size $CTX --host $HOST --port $PORT --parallel 1" >/dev/null 2>&1
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
    echo "starting llama-server on $HOST:$PORT (ctx $CTX, ngl $NGL, slot-cache $SLOT_SAVE_PATH)"
    mkdir -p "$SLOT_SAVE_PATH"
    nohup "$BIN" -m "$MODEL" -ngl "$NGL" --ctx-size "$CTX" \
        --host "$HOST" --port "$PORT" --parallel 1 \
        --slot-save-path "$SLOT_SAVE_PATH" \
        > "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    for i in $(seq 1 90); do
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
        pkill -f -- "$BIN -m $MODEL -ngl $NGL --ctx-size $CTX --host $HOST --port $PORT --parallel 1"
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
