#!/bin/bash
# Step 10B supervisor (retained driver, 2026-09-05).
#
# Executes the owner-ordered Step 10B flow that cannot run inside the gateway
# turn itself (restarting the gateway kills the hosting process tree):
#   1. restart the OpenClaw gateway (launchd KeepAlive brings it back) so the
#      openclaw.json `llama-server` provider contextWindow 32768 -> 65536
#      change is loaded;
#   2. wait for gateway + llama-server health;
#   3. rerun the retained Step 10A real-path liveness workload (64K leg,
#      P1/P2 x 9 = 18 fresh headless turns, identical prompts/instrumentation)
#      into a NEW results tree benchmarks/results/service-step-10b with fresh
#      step10b session keys (never colliding with the frozen step10a keys);
#   4. run the analyzer on the new tree (legs=64k).
#
# Launch detached (own session) so it survives the gateway restart:
#   python3 -c "import os,sys; os.setsid(); os.execv(sys.argv[1], sys.argv[1:])" \
#       /bin/bash tools/service_step10b_supervise.sh
#
# Logs: benchmarks/results/service-step-10b/supervise.log
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/benchmarks/results/service-step-10b"
mkdir -p "$OUT"
# Detached supervisor survives the gateway restart: fix PATH first (the gateway
# restart must not depend on the dying process tree's env).
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.openclaw/tmp/agent-cli:$PATH"
OPENCLAW_BIN="$(command -v openclaw || echo "$HOME/.openclaw/tmp/agent-cli/openclaw")"
export OPENCLAW_BIN
exec >> "$OUT/supervise.log" 2>&1
echo "[$(date -u +%FT%TZ)] supervise start (openclaw=$OPENCLAW_BIN)"

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
for i in $(seq 1 30); do
    H=$(curl -s -m 3 http://127.0.0.1:18080/health 2>/dev/null)
    [ -n "$H" ] && break
    sleep 2
done
echo "[$(date -u +%FT%TZ)] llama-server health: ${H:-unreachable}"

# ---- 3. rerun retained liveness workload into the 10B results tree ----
cd "$ROOT"
STEP10A_OUT="$OUT" STEP10A_KEYNS=step10b STEP10A_REPS=9 \
    bash tools/service_step10a_leg.sh 64k
leg_rc=$?
echo "[$(date -u +%FT%TZ)] leg rc=$leg_rc"

# ---- 4. analyze the new tree ----
STEP10A_OUT="$OUT" STEP10A_LEGS=64k \
    python3 tools/service_step10a_analyze.py > "$OUT/analysis-run.log" 2>&1
echo "[$(date -u +%FT%TZ)] analysis rc=$?"

echo "[$(date -u +%FT%TZ)] supervise DONE leg_rc=$leg_rc"
exit "$leg_rc"
