#!/bin/bash
# launchd wrapper for the Kimi llama-server (expert-streaming runtime).
# Records exit evidence to a persistent lifecycle log so spontaneous
# terminations can be diagnosed (see BUGS.md / SERVICE-ROADMAP.md Task 3B).
# launchd KeepAlive restarts us; the log preserves what happened each time.
set -u

KIMI_DIR=/Users/pmains/Code/openclaw/kimi
BIN="$KIMI_DIR/runtime/live/bin/llama-server"
LIFE=/tmp/kimi-llama-server.lifecycle.log
SRVLOG=/tmp/kimi-llama-server.launchd.log

cd "$KIMI_DIR"
export KIMI_STREAM_EXPERTS=naive
export KIMI_EXPERT_CACHE_MB=4096
export KIMI_EXPERT_CACHE_MODE=zerocopy

start_ts=$(date +%s)
echo "[$(date '+%F %T %z')] START pid=$$ ctx=32768" >> "$LIFE"

"$BIN" \
  -m models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf \
  -ngl 0 --no-mmap --ctx-size 32768 \
  --host 127.0.0.1 --port 18080 --parallel 1 \
  >> "$SRVLOG" 2>&1
code=$?

end_ts=$(date +%s)
echo "[$(date '+%F %T %z')] EXIT code=$code uptime=$((end_ts - start_ts))s" >> "$LIFE"
echo "--- last 40 lines of server output ---" >> "$LIFE"
tail -40 "$SRVLOG" >> "$LIFE"
echo "---------------------------------------" >> "$LIFE"

exit "$code"
