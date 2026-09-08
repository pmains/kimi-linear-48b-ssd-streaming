#!/bin/bash
# Step 13E driver - Real OpenClaw Agent Qualification at 256K (retained 2026-09-07).
#
# Owner go 2026-09-07 15:12 MST. Production is ALREADY the 262144 contract
# (13D, commit 48f26e1): launchd job com.openclaw.kimi-llama-server runs
# KIMI_CTX=262144 and openclaw.json contextWindow 262144 on both provider
# entries. There is NO swap protocol in 13E - we qualify the REAL production
# state through the real agent path (headless openclaw agent turns).
#
# Legs (SERVICE-ROADMAP §13E):
#   1 short control (agent kimi)
#   2 engineering/tool turn (agent kimi, write+exec)
#   3 Poliscopic turn (agent poliscopic, real bootstrap + tool allowlist)
#   4 assembled OpenClaw prompt > 65,536 tokens, needle beyond 65,536
#   5 assembled OpenClaw prompt > 131,072 tokens, needle beyond 131,072
# Each leg records resolved ctx, assembled promptTokens, compaction evidence,
# TTFT/prefill/decode (from llama + gateway log windows), wall, tool behavior,
# correctness, health. Watchdog on legs 4/5: abort if llama RSS >= 18 GiB or
# host free <= 3%.
#
# Usage:  STAGE=1 bash tools/service_step13e.sh   # legs 1-3 (short)
#         STAGE=2 bash tools/service_step13e.sh   # legs 4-5 (deep, hours)
set -u

KIMI_DIR=/Users/pmains/Code/openclaw/kimi
ROOT="$KIMI_DIR"
EVID="$ROOT/benchmarks/results/service-step-13/13e-256k"
RUNNER="$ROOT/tools/service_step10a_turn.py"
CORPUS="$ROOT/tools/service_step13e_corpus.py"
STAGE="${STAGE:-1}"
LLAMA_LOG=/tmp/kimi-llama-server.launchd.log
GATEWAY_LOG="$HOME/Library/Logs/openclaw/gateway.log"
PORT=18080
LLAMA_PID="$(pgrep -f 'llama-server.*18080' | head -1)"
LOG="$EVID/driver.log"
mkdir -p "$EVID"
TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"

log() { echo "[$(date '+%F %T %z')] $*" | tee -a "$LOG"; }

# ---------------- helpers ----------------
health_ok() { curl -s -m 5 http://127.0.0.1:$PORT/health >/dev/null 2>&1; }
gw_ok() { [ "$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:18789/healthz)" = "200" ]; }
n_ctx_now() { curl -s -m 5 http://127.0.0.1:$PORT/props 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['default_generation_settings']['n_ctx'])" 2>/dev/null || echo 0; }

wait_healthy() {
  local tmo="${1:-300}" deadline=$(( $(date +%s) + tmo ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    health_ok && return 0
    sleep 5
  done
  return 1
}

# run_one <label> <agent> <promptfile> <timeout>
run_one() {
  local label="$1" agent="$2" prompt="$3" tmo="$4"
  local outdir="$EVID/$label"
  mkdir -p "$outdir"
  if [ -f "$outdir/$label-r1.record.json" ]; then
    log "skip $label (record exists)"
    return 0
  fi
  log "=== $label start (agent=$agent timeout=${tmo}s) $(date '+%F %T %z') ==="
  python3 "$RUNNER" "$outdir" "$label-r1" "$prompt" "agent:$agent:step13e-$label-r1" \
    --agent "$agent" --timeout "$tmo" \
    --llama-log "$LLAMA_LOG" --gateway-log "$GATEWAY_LOG" \
    > "$outdir/$label.driver.out" 2>&1
  local rc=$?
  log "=== $label end rc=$rc $(date '+%F %T %z') ==="
  return $rc
}

# gate_agentmeta <label>: parse record.json -> contextTokens/source/promptTokens, doc rc
gate_agentmeta() {
  local label="$1"
  python3 - "$EVID/$label/$label-r1.record.json" <<'PY'
import json, sys
try:
    rec = json.load(open(sys.argv[1]))
    am = rec.get("agentMeta", {})
    print("contextTokens:", am.get("contextTokens"), "source:", am.get("contextTokensSource"),
          "promptTokens:", am.get("promptTokens"))
    print("doc_status:", rec.get("doc_status"), "rc:", (rec.get("summary") or {}).get("rc"),
          "wall_s:", (rec.get("summary") or {}).get("wall_s"))
    err = rec.get("error")
    if err: print("error:", json.dumps(err)[:300])
    sys.exit(0)
except Exception as e:
    print("parse error:", e)
    sys.exit(1)
PY
}

echo "=== Step 13E stage $STAGE driver ($TS) ===" > "$LOG"

# ---------------- stage 0: preflight (both stages) ----------------
log "--- preflight ---"
if ! health_ok; then log "ABORT: llama not healthy"; exit 3; fi
CTX_NOW="$(n_ctx_now)"
log "llama n_ctx=$CTX_NOW gw=$([ "$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:18789/healthz)" = 200 ] && echo 200 || echo FAIL)"
[ "$CTX_NOW" = "262144" ] || { log "ABORT: expected n_ctx 262144"; exit 3; }
python3 - "$EVID" <<'PY'
import json, subprocess, sys, os
evid = sys.argv[1]
def sh(c):
    try: return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=25).stdout.strip()
    except Exception: return "?"
rec = {
  "step": "13E-stage-" + os.environ.get("STAGE", "?"),
  "ts": os.popen("date -u +%Y-%m-%dT%H-%M-%SZ").read().strip(),
  "llama_health": sh("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/health"),
  "resolved_n_ctx": sh("curl -s http://127.0.0.1:18080/props | python3 -c \"import json,sys; print(json.load(sys.stdin)['default_generation_settings']['n_ctx'])\""),
  "gateway_healthz": sh("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18789/healthz"),
  "llama_pid": sh("pgrep -f 'llama-server.*18080' | head -1"),
  "step11b_sha": sh("shasum -a 256 /opt/homebrew/lib/node_modules/openclaw/dist/redact-CquADQ9-.js | cut -c1-10"),
  "fk_violations": sh("python3 -c \"import sqlite3; print(len(sqlite3.connect('/Users/pmains/.openclaw/agents/kimi/agent/openclaw-agent.sqlite').execute('PRAGMA foreign_key_check').fetchall()))\""),
}
json.dump(rec, open(f"{evid}/preflight-stage{os.environ.get('STAGE','?')}.json", "w"), indent=2)
print("PREFLIGHT:", json.dumps(rec))
PY
log "preflight recorded"

if [ "$STAGE" = "1" ]; then
  # ---------------- legs 1-3 (short) ----------------
  mkdir -p "$EVID/prompts" "$EVID/work/code"
  # leg 1: short control
  printf 'Reply with exactly: OK\n' > "$EVID/prompts/leg1-short.txt"
  run_one leg1-short-control kimi "$EVID/prompts/leg1-short.txt" 900
  gate_agentmeta leg1-short-control
  python3 - "$EVID/leg1-short-control/leg1-short-control-r1.record.json" <<'PY'
import json, sys
rec = json.load(open(sys.argv[1]))
am = rec.get("agentMeta", {})
ok = am.get("contextTokens") == 262144 and am.get("contextTokensSource") == "resolved"
sys.exit(0 if ok else 1)
PY
  [ $? -eq 0 ] || { log "GATE1 FAIL: leg1 contextTokens != 262144 resolved"; exit 4; }
  log "GATE1 pass: *** control resolves 262144"

  # leg 2: engineering/tool turn (write + py_compile in 13E workdir)
  cat > "$EVID/prompts/leg2-eng.txt" <<EOF
You are building a small Python text-analysis module. Working directory: $EVID/work/code (already created). Use the write tool to create text_stats.py with functions word_count(text)->int, line_count(text)->int, char_count(text)->int (characters excluding whitespace). Then run: exec python3 -m py_compile $EVID/work/code/text_stats.py. Reply with the exit status.
EOF
  run_one leg2-eng-tool kimi "$EVID/prompts/leg2-eng.txt" 1800
  gate_agentmeta leg2-eng-tool
  python3 - "$EVID/leg2-eng-tool/leg2-eng-tool-r1.record.json" <<'PY'
import json, sys, os
rec = json.load(open(sys.argv[1]))
am = rec.get("agentMeta", {})
rc = (rec.get("summary") or {}).get("rc")
ok = rc == 0 and am.get("contextTokens") == 262144
sys.exit(0 if ok else 1)
PY
  [ $? -eq 0 ] || { log "GATE2 FAIL: leg2 rc!=0 or ctx wrong"; exit 5; }
  ls "$EVID/work/code/text_stats.py" >/dev/null 2>&1 && log "GATE2 pass: *** created + turn rc 0" || log "GATE2 WARN: text_stats.py missing"

  # leg 3: Poliscopic turn (agent poliscopic, real bootstrap via read)
  cat > "$EVID/prompts/leg3-poliscopic.txt" <<'EOF'
Read the file /Users/pmains/Code/openclaw/poliscopic/AGENTS.md using the read tool. In one sentence, state the project's purpose. Then reply with exactly: POLISCOPIC-13E-OK
EOF
  run_one leg3-poliscopic poliscopic "$EVID/prompts/leg3-poliscopic.txt" 1200
  gate_agentmeta leg3-poliscopic
  python3 - "$EVID/leg3-poliscopic/leg3-poliscopic-r1.record.json" <<'PY'
import json, sys
rec = json.load(open(sys.argv[1]))
am = rec.get("agentMeta", {})
rc = (rec.get("summary") or {}).get("rc")
print("FINDING poliscopic contextTokens:", am.get("contextTokens"), "source:", am.get("contextTokensSource"))
sys.exit(0 if rc == 0 else 1)
PY
  [ $? -eq 0 ] || { log "GATE3 FAIL: leg3 rc!=0"; exit 6; }
  log "GATE3 pass: *** turn rc 0 (contextTokens recorded above; 65536 = leftover ceiling finding, 262144 = aligned)"
  log "=== STAGE 1 COMPLETE: legs 1-3 recorded ==="
  touch "$EVID/STAGE1_DONE"
  exit 0
fi

# ---------------- STAGE 2: legs 4-5 (deep) ----------------
if ! health_ok || [ "$(n_ctx_now)" != "262144" ]; then log "ABORT: production unhealthy before deep legs"; exit 7; fi
LLAMA_PID="$(pgrep -f 'llama-server.*18080' | head -1)"
[ -n "$LLAMA_PID" ] || { log "ABORT: cannot find llama pid"; exit 7; }
log "deep-leg llama pid=$LLAMA_PID"

# leg 4: assembled prompt > 65,536; needle beyond assembled 65,536
log "--- leg 4 corpus build (target message ~60K tokens) ---"
NEEDLE4="NEEDLE-13E-$(date +%N | head -c 4)"
echo "$NEEDLE4" > "$EVID/needle-leg4.txt"
mkdir -p "$EVID/leg4-beyond64k"
python3 "$CORPUS" "$PORT" "$NEEDLE4" \
  "$EVID/leg4-beyond64k/message.txt" \
  "$EVID/leg4-beyond64k/calibrate.json" 60000 0.94 \
  > "$EVID/leg4-beyond64k/corpus.out" 2>&1
python3 - "$EVID/leg4-beyond64k/calibrate.json" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
print("message_tokens:", c.get("message_tokens"), "needle_msg_pos:", c.get("needle_message_token_pos"))
# assembled needle pos = overhead(~12720 from 13D/leg1) + msg pos
ov = 12720
ap = (c.get("needle_message_token_pos") or 0) + ov
print("estimated assembled needle pos:", ap, "(need > 65536)")
sys.exit(0 if (c.get("message_tokens") or 0) > 53000 and ap > 65536 else 1)
PY
[ $? -eq 0 ] || { log "GATE4a FAIL: leg4 corpus calibration"; exit 8; }
log "leg4 corpus ready: $NEEDLE4"

# watchdog sampler (background; aborts leg by killing client if RSS/free breach)
WATCH_LABEL=leg4-beyond64k
(
  while kill -0 "$LLAMA_PID" 2>/dev/null; do
    RSS_KB=$(ps -o rss= -p "$LLAMA_PID" 2>/dev/null | tr -d ' ')
    RSS_GIB=$(python3 -c "print(f'{$RSS_KB/1048576:.2f}')" 2>/dev/null)
    # host free% via memory_pressure -Q (13B-proven metric; the earlier
    # vm_stat free% was wrong on 16KB-page macOS and false-aborted run 1
    # of this stage; do not use vm_stat free pages here)
    FREE_PCT=$(memory_pressure -Q 2>/dev/null | grep -o '[0-9]*%' | head -1 | tr -d '%' || echo 100)
    echo "$(date +%s) rss_gib=$RSS_GIB free_pct=$FREE_PCT" >> "$EVID/leg4-beyond64k/watchdog.tsv"
    if python3 -c "import sys; sys.exit(0 if float('${RSS_GIB:-0}') >= 18 else 1)"; then
      echo "ABORT: llama RSS >= 18 GiB" >> "$EVID/leg4-beyond64k/watchdog.tsv"
      touch "$EVID/leg4-beyond64k/ABORT"
      pkill -f "step13e-$WATCH_LABEL" 2>/dev/null
    fi
    if python3 -c "import sys; sys.exit(0 if float('${FREE_PCT:-100}') <= 3 else 1)"; then
      echo "ABORT: host free <= 3%" >> "$EVID/leg4-beyond64k/watchdog.tsv"
      touch "$EVID/leg4-beyond64k/ABORT"
      pkill -f "step13e-$WATCH_LABEL" 2>/dev/null
    fi
    sleep 10
  done
) &
WATCH_PID=$!

log "--- leg 4 turn (real agent path; assembled >65536) ---"
run_one leg4-beyond64k kimi "$EVID/leg4-beyond64k/message.txt" 14400
LEG4_RC=$?
kill "$WATCH_PID" 2>/dev/null
gate_agentmeta leg4-beyond64k
python3 - "$EVID/leg4-beyond64k" "$NEEDLE4" <<'PY'
import json, sys, os
d, needle = sys.argv[1], sys.argv[2]
rec = json.load(open(f"{d}/leg4-beyond64k-r1.record.json"))
am = rec.get("agentMeta", {})
pt = am.get("promptTokens") or 0
reply = open(f"{d}/leg4-beyond64k-r1.reply.txt", encoding="utf-8", errors="replace").read() if os.path.exists(f"{d}/leg4-beyond64k-r1.reply.txt") else ""
cal = json.load(open(f"{d}/calibrate.json"))
needle_hit = needle in reply
assembled_needle = (cal.get("needle_message_token_pos") or 0) + 12720
ok = pt > 65536 and needle_hit and assembled_needle > 65536 and (rec.get("summary") or {}).get("rc") == 0
print("GATE4 verdict:", "PASS" if ok else "FAIL",
      "| promptTokens:", pt, ">65536:", pt > 65536,
      "| needle_hit:", needle_hit, "| assembled_needle_pos:", assembled_needle)
json.dump({"verdict": "PASS" if ok else "FAIL", "promptTokens": pt,
           "needle_hit": needle_hit, "assembled_needle_pos": assembled_needle,
           "needle": needle}, open(f"{d}/gate4.json", "w"), indent=2)
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] || { log "GATE4 FAIL: leg4 deep turn (see gate4.json)"; exit 9; }
[ ! -f "$EVID/leg4-beyond64k/ABORT" ] || { log "GATE4 FAIL: watchdog aborted"; exit 9; }
log "GATE4 pass: *** prompt >65536 with needle retrieval past boundary"

# leg 5: assembled prompt > 131,072; needle beyond assembled 131,072
log "--- leg 5 corpus build (target message ~128K tokens) ---"
NEEDLE5="NEEDLE-13E-$(date +%N | head -c 4)"
echo "$NEEDLE5" > "$EVID/needle-leg5.txt"
mkdir -p "$EVID/leg5-beyond128k"
python3 "$CORPUS" "$PORT" "$NEEDLE5" \
  "$EVID/leg5-beyond128k/message.txt" \
  "$EVID/leg5-beyond128k/calibrate.json" 128000 0.965 \
  > "$EVID/leg5-beyond128k/corpus.out" 2>&1
python3 - "$EVID/leg5-beyond128k/calibrate.json" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
print("message_tokens:", c.get("message_tokens"), "needle_msg_pos:", c.get("needle_message_token_pos"))
ov = 12720
ap = (c.get("needle_message_token_pos") or 0) + ov
print("estimated assembled needle pos:", ap, "(need > 131072)")
sys.exit(0 if (c.get("message_tokens") or 0) > 119000 and ap > 131072 else 1)
PY
[ $? -eq 0 ] || { log "GATE5a FAIL: leg5 corpus calibration"; exit 10; }
log "leg5 corpus ready: $NEEDLE5"

WATCH_LABEL=leg5-beyond128k
(
  while kill -0 "$LLAMA_PID" 2>/dev/null; do
    RSS_KB=$(ps -o rss= -p "$LLAMA_PID" 2>/dev/null | tr -d ' ')
    RSS_GIB=$(python3 -c "print(f'{$RSS_KB/1048576:.2f}')" 2>/dev/null)
    # host free% via memory_pressure -Q (13B-proven metric; the earlier
    # vm_stat free% was wrong on 16KB-page macOS and false-aborted run 1
    # of this stage; do not use vm_stat free pages here)
    FREE_PCT=$(memory_pressure -Q 2>/dev/null | grep -o '[0-9]*%' | head -1 | tr -d '%' || echo 100)
    echo "$(date +%s) rss_gib=$RSS_GIB free_pct=$FREE_PCT" >> "$EVID/leg5-beyond128k/watchdog.tsv"
    if python3 -c "import sys; sys.exit(0 if float('${RSS_GIB:-0}') >= 18 else 1)"; then
      echo "ABORT: llama RSS >= 18 GiB" >> "$EVID/leg5-beyond128k/watchdog.tsv"
      touch "$EVID/leg5-beyond128k/ABORT"
      pkill -f "step13e-$WATCH_LABEL" 2>/dev/null
    fi
    if python3 -c "import sys; sys.exit(0 if float('${FREE_PCT:-100}') <= 3 else 1)"; then
      echo "ABORT: host free <= 3%" >> "$EVID/leg5-beyond128k/watchdog.tsv"
      touch "$EVID/leg5-beyond128k/ABORT"
      pkill -f "step13e-$WATCH_LABEL" 2>/dev/null
    fi
    sleep 10
  done
) &
WATCH_PID2=$!

log "--- leg 5 turn (real agent path; assembled >131072) ---"
run_one leg5-beyond128k kimi "$EVID/leg5-beyond128k/message.txt" 21600
LEG5_RC=$?
kill "$WATCH_PID2" 2>/dev/null
gate_agentmeta leg5-beyond128k
python3 - "$EVID/leg5-beyond128k" "$NEEDLE5" <<'PY'
import json, sys, os
d, needle = sys.argv[1], sys.argv[2]
rec = json.load(open(f"{d}/leg5-beyond128k-r1.record.json"))
am = rec.get("agentMeta", {})
pt = am.get("promptTokens") or 0
reply = open(f"{d}/leg5-beyond128k-r1.reply.txt", encoding="utf-8", errors="replace").read() if os.path.exists(f"{d}/leg5-beyond128k-r1.reply.txt") else ""
cal = json.load(open(f"{d}/calibrate.json"))
needle_hit = needle in reply
assembled_needle = (cal.get("needle_message_token_pos") or 0) + 12720
ok = pt > 131072 and needle_hit and assembled_needle > 131072 and (rec.get("summary") or {}).get("rc") == 0
print("GATE5 verdict:", "PASS" if ok else "FAIL",
      "| promptTokens:", pt, ">131072:", pt > 131072,
      "| needle_hit:", needle_hit, "| assembled_needle_pos:", assembled_needle)
json.dump({"verdict": "PASS" if ok else "FAIL", "promptTokens": pt,
           "needle_hit": needle_hit, "assembled_needle_pos": assembled_needle,
           "needle": needle}, open(f"{d}/gate5.json", "w"), indent=2)
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] || { log "GATE5 FAIL: leg5 deep turn (see gate5.json)"; exit 11; }
[ ! -f "$EVID/leg5-beyond128k/ABORT" ] || { log "GATE5 FAIL: watchdog aborted"; exit 11; }
log "GATE5 pass: *** prompt >131072 with needle retrieval past 131072"

# final verify
sleep 2
log "--- final verify ---"
log "llama health=$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:18080/health) n_ctx=$(n_ctx_now) gw=$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:18789/healthz)"
python3 - "$EVID" "$LLAMA_PID" <<'PY'
import json, subprocess, sys
evid, lpid = sys.argv[1], sys.argv[2]
def sh(c):
    try: return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=25).stdout.strip()
    except Exception: return "?"
rec = {
  "step": "13E-stage2-final",
  "llama_health": sh("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/health"),
  "resolved_n_ctx": sh("curl -s http://127.0.0.1:18080/props | python3 -c \"import json,sys; print(json.load(sys.stdin)['default_generation_settings']['n_ctx'])\""),
  "gateway_healthz": sh("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18789/healthz"),
  "llama_pid": sh("pgrep -f 'llama-server.*18080' | head -1"),
  "step11b_sha": sh("shasum -a 256 /opt/homebrew/lib/node_modules/openclaw/dist/redact-CquADQ9-.js | cut -c1-10"),
  "fk_violations": sh("python3 -c \"import sqlite3; print(len(sqlite3.connect('/Users/pmains/.openclaw/agents/kimi/agent/openclaw-agent.sqlite').execute('PRAGMA foreign_key_check').fetchall()))\""),
}
json.dump(rec, open(f"{evid}/final-verify.json", "w"), indent=2)
print("FINAL:", json.dumps(rec))
PY
log "=== STAGE 2 COMPLETE: legs 4-5 recorded ==="
touch "$EVID/STAGE2_DONE"
exit 0
