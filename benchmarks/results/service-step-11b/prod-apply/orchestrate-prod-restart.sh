#!/bin/bash
# Step 11B production restart + legs orchestrator (authorized 2026-09-06).
# Detached: launched in a new session (python os.setsid) so it survives the
# gateway restart. ASCII-clean; no token material anywhere in this file.
# Sequence: verify active sha -> wait for turn flush -> kickstart launchd
# gateway -> wait health :18789 -> record PID/sha/llama -> run six production
# legs -> PASS marker, or FAIL (restore pristine archive + kickstart + marker).
set -u
LOG=/tmp/oc11b-env/prod-apply-run2.log
MARK=/tmp/oc11b-env/prod-apply-run2.marker
exec >>"$LOG" 2>&1
echo "=== orchestrator start $(date) ==="

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

echo "--- running six production legs $(date) ---"
python3 /tmp/oc11b-env/prod-fix-legs.py
rc=$?
echo "legs rc=$rc $(date)"

if [ "$rc" -eq 0 ]; then
  echo "PASS" > "$MARK"
  echo "--- all six legs PASS ---"
else
  echo "--- LEGS FAILED: restoring pristine archive ---"
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

echo "=== orchestrator end $(date) ==="
cp "$LOG" /Users/pmains/Code/openclaw/kimi/benchmarks/results/service-step-11b/prod-apply/orchestrator.log 2>/dev/null || true
cp "$MARK" /Users/pmains/Code/openclaw/kimi/benchmarks/results/service-step-11b/prod-apply/orchestrator.marker 2>/dev/null || true
