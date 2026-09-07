#!/bin/bash
# Serve the modified OpenClaw dev gateway (openclaw-src) on :18790.
#
# WHY THIS EXISTS (2026-08-18 incident):
#   `npm run gateway:dev` is a TRAP for this project:
#     - it passes NO --port, so the gateway tries 18789 (stock gateway port)
#       and dies with EADDRINUSE;
#     - run without OPENCLAW_CONFIG_PATH/OPENCLAW_STATE_DIR it falls back to
#       the `dev` profile (~/.openclaw-dev) and boots a stub C3-PO config
#       with NO caveman/manager agents, NO kimi-local/qwen providers, and NO
#       compaction override. A full replacement of that stub was observed.
#   Use THIS script (or the exact command below) instead.
#
# The dev gateway MUST run with the isolated dev-openclaw env:
#   - config:  dev-openclaw/config/openclaw.json
#              (agents caveman/manager/main, llama-cpp kimi + qwen providers,
#               agents.defaults.compaction.timeoutSeconds=900 for the slow
#               Kimi Linear model — do NOT remove)
#   - state:   dev-openclaw/state   (real session DB incl. caveman history)
#   - home:    dev-openclaw/home    (isolated caches)
#   - auth:    borrows the STOCK gateway token from ~/.openclaw/openclaw.json
#              (gateway.auth.token) so Control UI / tools authenticate.
#   - channels: skipped (OPENCLAW_SKIP_CHANNELS=1) — dev harness only.
#
# Usage:
#   tools/serve_dev_gateway.sh [start|stop|status]
# Env:
#   DEV_PORT=18790   gateway port
#   DEV_LOG=<abs or rel path>   log file (default dev-openclaw/logs/gateway-dev.log)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/openclaw-src"
PORT="${DEV_PORT:-18790}"
LOG="${DEV_LOG:-$ROOT/dev-openclaw/logs/gateway-dev.log}"
PIDFILE="${DEV_PIDFILE:-/tmp/kimi-dev-gateway.pid}"

CONFIG_PATH="$ROOT/dev-openclaw/config/openclaw.json"
STATE_DIR="$ROOT/dev-openclaw/state"
HOME_DIR="$ROOT/dev-openclaw/home"

# Stable auth token: borrowed from the stock gateway so the dev gateway
# accepts the same token (Control UI, CLI helpers).
STOCK_CONFIG="$HOME/.openclaw/openclaw.json"
TOKEN="$(python3 -c "
import json,sys
try:
    cfg = json.load(open('$STOCK_CONFIG'))
    print(cfg.get('gateway', {}).get('auth', {}).get('token', ''))
except Exception:
    sys.exit(1)
" 2>/dev/null || true)"
if [ -z "$TOKEN" ]; then
  echo "error: could not read gateway.auth.token from $STOCK_CONFIG" >&2
  exit 1
fi

start() {
  if lsof -i :"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "dev gateway already listening on :$PORT"
    return 1
  fi
  if [ ! -f "$CONFIG_PATH" ]; then
    echo "error: missing config $CONFIG_PATH" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$LOG")"
  cd "$SRC"
  OPENCLAW_HOME="$HOME_DIR" \
  OPENCLAW_STATE_DIR="$STATE_DIR" \
  OPENCLAW_CONFIG_PATH="$CONFIG_PATH" \
  OPENCLAW_GATEWAY_TOKEN="$TOKEN" \
  OPENCLAW_SKIP_CHANNELS=1 \
  nohup node --import tsx scripts/run-node.mts --dev gateway --port "$PORT" \
    > "$LOG" 2>&1 &
  echo "$!" > "$PIDFILE"
  echo "dev gateway launching (pid $(cat "$PIDFILE")); log: $LOG"
  echo "waiting for :$PORT ..."
  for _ in $(seq 1 60); do
    if lsof -i :"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "UP on :$PORT"
      return 0
    fi
    sleep 1
  done
  echo "not listening after 60s — check $LOG" >&2
  return 1
}

stop() {
  local pids
  pids="$(pgrep -f "run-node.mts --dev gateway --port $PORT" || true)"
  if [ -z "$pids" ]; then
    echo "no dev gateway process found (port $PORT)"
    return 0
  fi
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 3
  pids="$(pgrep -f "run-node.mts --dev gateway --port $PORT" || true)"
  if [ -n "$pids" ]; then
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
  rm -f "$PIDFILE"
  echo "dev gateway stopped"
}

status() {
  if lsof -i :"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "dev gateway: UP on :$PORT"
    curl -s -m 3 http://127.0.0.1:"$PORT"/health && echo
  else
    echo "dev gateway: DOWN (port $PORT free)"
    return 1
  fi
}

case "${1:-status}" in
  start) start ;;
  stop)  stop ;;
  status) status ;;
  *) echo "usage: $0 [start|stop|status]" >&2; exit 2 ;;
esac
