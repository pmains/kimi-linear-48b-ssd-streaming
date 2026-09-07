#!/bin/bash
# Step 10D supervisor (retained driver, 2026-09-05).
#
# Executes the owner-ordered Step 10D flow that cannot run inside the gateway
# turn itself (restarting the gateway kills the hosting process tree):
#   1. restart the OpenClaw gateway (launchd KeepAlive brings it back) so the
#      openclaw.json `agents.entries.kimi.tools.loopDetection.enabled: true`
#      change is loaded;
#   2. wait for gateway + llama-server health (llama must stay the untouched
#      64K launchd server);
#   3. run the focused 10D validation leg (LOOP x3, P1 x2, P2 x2, NORM x2 =
#      9 fresh headless turns, step10d session keys) into
#      benchmarks/results/service-step-10d;
#   4. run the classifier on the new tree.
#
# Launch detached (own session) so it survives the gateway restart:
#   python3 -c "import os,sys; os.setsid(); os.execv(sys.argv[1], sys.argv[1:])" \
#       /bin/bash tools/service_step10d_supervise.sh
#
# Logs: benchmarks/results/service-step-10d/supervise.log
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/benchmarks/results/service-step-10d"
mkdir -p "$OUT"
# Detached supervisor survives the gateway restart: fix PATH first (the gateway
# restart must not depend on the dying process tree's env).
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.openclaw/tmp/agent-cli:$PATH"
OPENCLAW_BIN="$(command -v openclaw || echo "$HOME/.openclaw/tmp/agent-cli/openclaw")"
export OPENCLAW_BIN
exec >> "$OUT/supervise.log" 2>&1
echo "[$(date -u +%FT%TZ)] supervise start (openclaw=$OPENCLAW_BIN)"
echo "[$(date -u +%FT%TZ)] config loopDetection=$(python3 -c "import json;print(json.load(open('$HOME/.openclaw/openclaw.json'))['agents']['entries']['kimi']['tools']['loopDetection'])" 2>/dev/null)"

sleep 30

# ---- 1. gateway restart (OpenClaw-aware; launchd KeepAlive restarts it) ----
echo "[$(date -u +%FT%TZ)] openclaw gateway restart"
"$OPENCLAW_BIN" gateway restart --json 2>&1 | tail -8

# ---- 2. wait for gateway + llama-server health ----
for i in $(seq 1 90); do
    if "$OPENCLAW_BIN" gateway health >/dev/null 2>&1; then
        echo "[$(date -u +%FT%TZ)] gateway healthy after ${i} tries"
        break
    fi
    sleep 2
done
sleep 5
H=""
for i in $(seq 1 30); do
    H=$(curl -s -m 3 http://127.0.0.1:18080/health 2>/dev/null)
    [ -n "$H" ] && break
    sleep 2
done
echo "[$(date -u +%FT%TZ)] llama-server health: ${H:-unreachable}"
echo "[$(date -u +%FT%TZ)] n_ctx=$(curl -s -m 3 http://127.0.0.1:18080/slots 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['n_ctx'])" 2>/dev/null)"

# ---- 3. run the focused 10D validation leg ----
cd "$ROOT"
STEP10D_OUT="$OUT" STEP10D_KEYNS=step10d \
    bash tools/service_step10d_leg.sh
leg_rc=$?
echo "[$(date -u +%FT%TZ)] leg rc=$leg_rc"

# ---- 4. classify the new tree ----
python3 tools/service_step10d_analyze.py "$OUT" > "$OUT/analysis-run.log" 2>&1
echo "[$(date -u +%FT%TZ)] analysis rc=$?"

echo "[$(date -u +%FT%TZ)] supervise DONE leg_rc=$leg_rc"
exit "$leg_rc"
