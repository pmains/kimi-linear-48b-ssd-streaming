#!/bin/bash
# Step 11B REQUALIFICATION 2 orchestrator (owner order 2026-09-06 21:05:46).
# Detached: launched in a new session (python os.setsid) so it survives the
# gateway restart. ASCII-clean; no token material anywhere in this file.
# Sequence: verify active sha -> wait for turn flush -> kickstart launchd
# gateway -> wait health :18789 -> record PID -> verify sha -> llama check ->
# run six production legs EXACTLY ONCE with the FK-safe driver
# (prod-fix-legs-req2.py; per-leg FK gates, exit 3 on FK violation)
# -> PASS marker (retain patch) or FAIL (restore pristine + restart).
set -u
LOG=/tmp/oc11b-env/requal2-apply.log
MARK=/tmp/oc11b-env/requal2-apply.marker
exec >>"$LOG" 2>&1
echo "=== requal2 orchestrator start $(date) ==="

DIST=/opt/homebrew/lib/node_modules/openclaw/dist/redact-CquADQ9-.js
ARCHIVE=/Users/pmains/Code/openclaw/kimi/benchmarks/results/service-step-11b/prod-apply/pristine-redact-CquADQ9-.js.pre-apply-20260906-1202
EXPECT=8baf47468478e930ad5b54cd5f8deda49164141d4069bb7c0418cb849ab179ce

echo "pre-restart sha: $(shasum -a 256 "$DIST" | cut -c1-16)"
NOW=$(shasum -a 256 "$DIST" | cut -d' ' -f1)
if [ "$NOW" != "$EXPECT" ]; then
  echo "ABORT: active sha mismatch pre-restart ($NOW)"
  echo "ABORT" > "$MARK"
  exit 9
fi

echo "sleeping 60s for current turn to flush..."
sleep 60

echo "--- kickstart gateway $(date) ---"
launchctl kickstart -k gui/501/ai.openclaw.gateway
echo "kickstart rc=$?"

UP=""
for i in $(seq 1 90); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18789/healthz 2>/dev/null || echo 000)
  if [ "$code" = "200" ]; then UP=1; echo "health OK at attempt $i"; break; fi
  sleep 2
done
if [ -z "$UP" ]; then
  echo "GATEWAY NOT UP AFTER KICKSTART"
  echo "ERROR" > "$MARK"
  exit 3
fi

echo "gateway pid now: $(pgrep -fl 'dist/index.js gateway --port 18789' | head -1)"
echo "post-restart sha: $(shasum -a 256 "$DIST" | cut -c1-16)"
echo "llama health: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/health)"

echo "--- running six production legs once with FK-safe driver $(date) ---"
python3 /tmp/oc11b-env/prod-fix-legs-req2.py
rc=$?
echo "legs rc=$rc $(date)"

# final FK gate on the production DB, independent of driver exit code
python3 - <<'PYEOF'
import sqlite3, sys
con = sqlite3.connect("/Users/pmains/.openclaw/agents/kimi/agent/openclaw-agent.sqlite", timeout=30)
viol = con.execute("PRAGMA foreign_key_check").fetchall()
con.close()
print("final production FK violations:", len(viol))
if viol:
    print(viol[:5])
    sys.exit(4)
PYEOF
fkrc=$?
echo "final-fk rc=$fkrc"

if [ "$rc" -eq 0 ] && [ "$fkrc" -eq 0 ]; then
  echo "PASS" > "$MARK"
  echo "--- ALL SIX LEGS PASS: patch retained ---"
else
  echo "--- LEGS FAILED (rc=$rc fk=$fkrc): restoring pristine archive ---"
  cp "$ARCHIVE" "$DIST"
  echo "restored sha: $(shasum -a 256 "$DIST" | cut -c1-16)"
  launchctl kickstart -k gui/501/ai.openclaw.gateway
  echo "rollback kickstart rc=$?"
  for i in $(seq 1 45); do
    code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18789/healthz 2>/dev/null || echo 000)
    if [ "$code" = "200" ]; then echo "rollback health OK at attempt $i"; break; fi
    sleep 2
  done
  echo "FAIL" > "$MARK"
fi

echo "=== requal2 orchestrator end $(date) ==="
cp "$LOG" /Users/pmains/Code/openclaw/kimi/benchmarks/results/service-step-11b/prod-apply/requal2-orchestrator.log 2>/dev/null || true
cp "$MARK" /Users/pmains/Code/openclaw/kimi/benchmarks/results/service-step-11b/prod-apply/requal2-orchestrator.marker 2>/dev/null || true
