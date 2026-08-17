#!/bin/bash
# launchd wrapper for the Kimi llama-server (expert-streaming runtime).
# Records exit evidence to a persistent lifecycle log so spontaneous
# terminations can be diagnosed (see BUGS.md / SERVICE-ROADMAP.md Task 3B).
# launchd KeepAlive restarts us; the log preserves what happened each time.
set -u

KIMI_DIR=/Users/pmains/Code/openclaw/kimi
BIN="${KIMI_BIN:-$KIMI_DIR/runtime/live/bin/llama-server}"
LIFE=/tmp/kimi-llama-server.lifecycle.log
SRVLOG=/tmp/kimi-llama-server.launchd.log
PIDFILE=/tmp/kimi-llama-server.pid
STATE_DIR="$KIMI_DIR/runtime/state"
SLOT_SAVE_PATH="$STATE_DIR/slot-cache"

cd "$KIMI_DIR"
export KIMI_STREAM_EXPERTS="${KIMI_STREAM_EXPERTS:-naive}"
export KIMI_EXPERT_CACHE_MB="${KIMI_EXPERT_CACHE_MB:-4096}"
export KIMI_EXPERT_CACHE_MODE="${KIMI_EXPERT_CACHE_MODE:-zerocopy}"
export KIMI_HOST="${KIMI_HOST:-127.0.0.1}"
export KIMI_PORT="${KIMI_PORT:-18080}"
export KIMI_CTX="${KIMI_CTX:-32768}"
mkdir -p "$SLOT_SAVE_PATH"

MODEL="models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf"
CMD="$BIN -m $MODEL -ngl 0 --no-mmap --ctx-size $KIMI_CTX --host $KIMI_HOST --port $KIMI_PORT --parallel 1"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "[$(date '+%F %T %z')] already running pid=$(cat "$PIDFILE")" >> "$LIFE"
  exit 0
fi

if pgrep -f -- "$CMD" >/dev/null 2>&1; then
  echo "[$(date '+%F %T %z')] matched existing llama-server; refusing duplicate start" >> "$LIFE"
  exit 0
fi

start_ts=$(date +%s)
echo "[$(date '+%F %T %z')] START pid=$$ ctx=$KIMI_CTX cache=${KIMI_EXPERT_CACHE_MB} host=$KIMI_HOST port=$KIMI_PORT" >> "$LIFE"

trap 'rm -f "$PIDFILE"' EXIT

"$BIN" \
  -m "$MODEL" \
  -ngl 0 --no-mmap --ctx-size "$KIMI_CTX" \
  --host "$KIMI_HOST" --port "$KIMI_PORT" --parallel 1 \
  --slot-save-path "$SLOT_SAVE_PATH" \
  >> "$SRVLOG" 2>&1 &
srv_pid=$!
echo "$srv_pid" > "$PIDFILE"
wait "$srv_pid"
code=$?

end_ts=$(date +%s)
echo "[$(date '+%F %T %z')] EXIT code=$code uptime=$((end_ts - start_ts))s" >> "$LIFE"
echo "--- last 40 lines of server output ---" >> "$LIFE"
tail -40 "$SRVLOG" >> "$LIFE"
echo "---------------------------------------" >> "$LIFE"

exit "$code"
