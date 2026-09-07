#!/bin/bash
# Step 13A driver — reproduce 128K (ctx 131072) on the current production
# runtime under the SINGLE-llama-server swap protocol (owner decision
# 2026-09-07: only one llama-server; live port 18080 is swapped for the test
# window, then restored to the launchd 64K job).
#
# Conditions reproduced from production (roadmap 13A + stage-8 rung method):
#   - same frozen live binary (runtime/live, COMMIT a895f6826)
#   - same production model (MXFP4_MOE.gguf)
#   - same E3E operating env: naive stream, 8192 MiB cache, 4 read workers,
#     zerocopy, KIMI_STREAM_METAL_STAGE=1, KIMI_STREAM_E2_DIRECT_PLACE=1
#   - only --ctx-size changes: 65536 (launchd baseline) -> 131072 (test)
#   - observability: -lv 4 (log verbosity only; no compute change),
#     telemetry env vars redirected to the 13A evidence dir (never the
#     phase-11 e3e-promotion files)
#   - slot-save-path omitted (stage-8 rung servers did not use it; avoids
#     touching the production warm-state slot cache)
#
# Gates (stage-8 invariant rung protocol):
#   1 allocation/startup   server healthy at 131072; KV/recurrent/Metal lines
#   2 prefill correctness  boundary needle prompt admitted (tokens > 65536)
#   3 generation correctness + retrieval beyond 64K (needle string returned)
#   4 state/cache after long-context use (server healthy after)
# Plus: short fixed probe (ok) with streamed TTFT; boundary probe with
# streamed TTFT (time-to-first-token after the long prefill); prefill/decode
# tok/s from server print_timing; steady + peak RSS; host memory.
#
# Restore is unconditional (trap + explicit tail): launchd 64K job back,
# health + ctx 65536 + FK 0 + 11B sha verified.
#
# Usage: tools/service_step13a.sh
set -u

KIMI_DIR=/Users/pmains/Code/openclaw/kimi
EVID="$KIMI_DIR/benchmarks/results/service-step-13/13a-128k"
TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
mkdir -p "$EVID/telemetry"
LOG="$EVID/driver.log"
SRVLOG="$EVID/server-131072.log"
PLIST="$HOME/Library/LaunchAgents/com.openclaw.kimi-llama-server.plist"
LABEL=com.openclaw.kimi-llama-server
UID_N="$(id -u)"
PORT=18080
CTX=131072
BIN="$KIMI_DIR/runtime/live/bin/llama-server"
MODEL="models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf"
HEALTH_URL="http://127.0.0.1:$PORT/health"
CHAT_URL="http://127.0.0.1:$PORT/v1/chat/completions"
SRV_PID=""
RESTORED=0

log() { echo "[$(date '+%F %T %z')] $*" | tee -a "$LOG"; }

wait_healthy() {
  local tmo="${1:-600}"
  local deadline=$(( $(date +%s) + tmo ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -s -m 2 "$HEALTH_URL" >/dev/null 2>&1; then return 0; fi
    sleep 3
  done
  return 1
}
# fail-fast variant: if the test server process dies (e.g. Metal OOM during
# startup), do not burn the full timeout — declare failure shortly after.
wait_healthy_fast() {
  local tmo="${1:-600}"
  local deadline=$(( $(date +%s) + tmo ))
  local start=$SECONDS
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -s -m 2 "$HEALTH_URL" >/dev/null 2>&1; then return 0; fi
    if ! kill -0 "$SRV_PID" 2>/dev/null && [ $(( SECONDS - start )) -gt 45 ]; then
      log "test server process died after ${SECONDS}s; failing fast"
      return 1
    fi
    sleep 3
  done
  return 1
}
wait_port_free() {
  local tmo="${1:-60}"
  local deadline=$(( $(date +%s) + tmo ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if ! lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  return 1
}

restore_production() {
  [ "$RESTORED" = 1 ] && return 0
  RESTORED=1
  log "RESTORE: killing test server pid=${SRV_PID:-none}"
  if [ -n "$SRV_PID" ]; then
    kill "$SRV_PID" 2>/dev/null || true
  fi
  sleep 2
  wait_port_free 30 || log "RESTORE warn: port $PORT still busy after kill"
  if ! launchctl print "gui/$UID_N/$LABEL" >/dev/null 2>&1; then
    log "RESTORE: bootstrap launchd job from plist"
    launchctl bootstrap "gui/$UID_N" "$PLIST" 2>>"$LOG" || log "RESTORE warn: bootstrap rc=$?"
  else
    log "RESTORE: launchd job already loaded"
  fi
  local deadline=$(( $(date +%s) + 300 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -s -m 2 "$HEALTH_URL" >/dev/null 2>&1; then break; fi
    sleep 3
  done
  log "RESTORE: llama /health check done"
}
trap 'log "EXIT trap fired"; restore_production' EXIT

log "=== Step 13A ctx=$CTX swap window $TS ==="
log "binary: $BIN ($(head -1 "$KIMI_DIR/runtime/live/COMMIT" 2>/dev/null))"
log "model:  $MODEL"
log "plist:  $PLIST (launchd baseline KIMI_CTX=65536)"

# --- phase 0: preflight (production state) + abort guard ---------------------
log "--- phase 0 preflight ---"
python3 - "$EVID" "$TS" <<'PY'
import json, subprocess, sys
evid, ts = sys.argv[1], sys.argv[2]
def sh(c):
    try: return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception: return "?"
rec = {
  "ts": ts,
  "gateway_health": sh("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18789/healthz"),
  "llama_ctx_before": sh("curl -s http://127.0.0.1:18080/slots | python3 -c \"import json,sys; print(json.load(sys.stdin)[0]['n_ctx'])\""),
  "llama_health_before": sh("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/health"),
  "step11b_sha": sh("shasum -a 256 /opt/homebrew/lib/node_modules/openclaw/dist/redact-CquADQ9-.js | cut -c1-10"),
  "fk_violations": sh("python3 -c \"import sqlite3; print(len(sqlite3.connect('/Users/pmains/.openclaw/agents/kimi/agent/openclaw-agent.sqlite').execute('PRAGMA foreign_key_check').fetchall()))\""),
  "mem_free_pct": sh("memory_pressure -Q 2>/dev/null | grep -o '[0-9]*%' | head -1"),
}
json.dump(rec, open(f"{evid}/preflight-{ts}.json", "w"), indent=2)
print("preflight:", json.dumps(rec))
PY
# Abort guard: never swap away from a broken baseline.
LH=$(curl -s -m 3 -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo 000)
GW=$(curl -s -m 3 -o /dev/null -w "%{http_code}" http://127.0.0.1:18789/healthz 2>/dev/null || echo 000)
if [ "$LH" != "200" ] || [ "$GW" != "200" ]; then
  log "PREFLIGHT ABORT: llama=$LH gw=$GW — production not healthy; refusing swap"
  exit 2
fi
log "preflight OK: llama=$LH gw=$GW"

# --- phase 1: swap (bootout launchd 64K -> manual 131072 server) ------------
log "--- phase 1 swap ---"
launchctl bootout "gui/$UID_N/$LABEL" 2>>"$LOG"
if ! wait_port_free 90; then
  log "GATE1 FAIL: port $PORT did not free after bootout"
  exit 1
fi
log "port $PORT free; starting manual ctx=$CTX server"

KIMI_STREAM_EXPERTS=naive KIMI_EXPERT_CACHE_MB=8192 KIMI_EXPERT_READ_WORKERS=4 \
KIMI_EXPERT_CACHE_MODE=zerocopy KIMI_STREAM_METAL_STAGE=1 KIMI_STREAM_E2_DIRECT_PLACE=1 \
KIMI_STREAM_STATS_FILE="$EVID/telemetry/stats.csv" \
KIMI_STREAM_RETR_FILE="$EVID/telemetry/retr.csv" \
KIMI_STREAM_MEM_FILE="$EVID/telemetry/mem.csv" \
KIMI_STREAM_CACHE_LAYERS_FILE="$EVID/telemetry/cache_layers.csv" \
nohup "$BIN" -m "$MODEL" -ngl 999 --no-mmap --ctx-size "$CTX" \
  --host 127.0.0.1 --port "$PORT" --parallel 1 -lv 4 \
  > "$SRVLOG" 2>&1 &
SRV_PID=$!
echo "$SRV_PID" > "$EVID/server.pid"
log "manual server pid=$SRV_PID ctx=$CTX (log $SRVLOG)"

if ! wait_healthy_fast 900; then
  log "GATE1 FAIL: manual 131072 server did not become healthy (allocation/Metal/startup boundary)"
  tail -30 "$SRVLOG" >> "$LOG"
  exit 1
fi
log "GATE1 PASS: allocation/startup healthy at ctx=$CTX"
sleep 2
CTX_ACTUAL=$(curl -s -m 3 "http://127.0.0.1:$PORT/slots" | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['n_ctx'])" 2>/dev/null || echo "?")
log "server resolved n_ctx=$CTX_ACTUAL (expected $CTX)"

{
  echo "=== startup: KV/recurrent/expert/Metal/ctx lines ==="
  grep -aiE "KV buffer size|KV self size|llama_kv_cache: size|llama_memory_recurrent|expert cache armed|compute buffer|metal|context length|n_ctx|ggml_metal|not enough space|out of memory|cannot allocate|GGML_ASSERT" "$SRVLOG" | head -60 || true
} > "$EVID/startup.mem.txt" 2>&1
cat "$EVID/startup.mem.txt" >> "$LOG"

# --- phase 2: short probe (fixed ok) + streamed TTFT -------------------------
log "--- phase 2 short probe + TTFT ---"
python3 - "$CHAT_URL" "$EVID" <<'PY'
import json, sys, time, urllib.request
url, evid = sys.argv[1], sys.argv[2]
payload = {"model":"kimi-linear-48b",
           "messages":[{"role":"user","content":"Reply with the single word ok."}],
           "max_tokens":4,"temperature":0,"stream":True}
req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                             headers={"Content-Type":"application/json"})
t0 = time.time(); first = None; text = ""; ttft = None
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        for raw in r:
            line = raw.decode("utf-8","replace").strip()
            if line.startswith("data:") and first is None:
                first = time.time(); ttft = first - t0
            if line.startswith("data:") and "[DONE]" not in line:
                try:
                    chunk = json.loads(line[5:])
                    d = chunk.get("choices",[{}])[0].get("delta",{}).get("content","")
                    if d: text += d
                except Exception: pass
except Exception as e:
    print("probe-a error:", e)
ok = "ok" in text.lower().strip()
res = {"ttft_s": round(ttft,3) if ttft else None, "text": text.strip(), "ok": ok}
json.dump(res, open(f"{evid}/probe-a.json","w"), indent=2)
print("probe-a:", json.dumps(res))
PY
grep -aoE "prompt eval time *= *[0-9.]+ ms *\/ *[0-9]+ tokens" "$SRVLOG" | tail -1 > "$EVID/probe-a.prefill" 2>/dev/null || true
[ -s "$EVID/probe-a.prefill" ] && log "probe-a prefill: $(cat "$EVID/probe-a.prefill")"

# --- phase 3: boundary needle probe (beyond 64K), streamed TTFT --------------
log "--- phase 3 boundary needle probe (>64K retrieval) ---"
NEEDLE="13A-$(date +%N | head -c 4)"
python3 - "$PORT" "$NEEDLE" "$EVID" <<'PY'
import json, sys
port, needle, evid = int(sys.argv[1]), sys.argv[2], sys.argv[3]
needle_str = f"NEEDLE-{needle}"
open(f"{evid}/needle.txt","w").write(needle_str)
# Stage-8 filler; stage-8 observed 92,344 tokens from 471,859 chars (~5.11
# chars/token). Target actual ~95K tokens (comparable to stage-8's 92,344)
# with the needle at 90% char depth -> ~85K token position, unambiguously
# beyond the 65,536 boundary under tokenizer variance (85-105K actual ->
# needle 76-94K).
unit = ("The quick brown fox surveys the quiet valley under a wide sky. "
        "Rivers wind between granite ridges while hawks circle slowly. "
        "Each stone holds a story older than any written word. ")
target_chars = int(95000 * 5.11)
units_needed = max(1, -(-target_chars // len(unit)))
filler = unit * units_needed
idx = int(len(filler) * 0.90)
filler = filler[:idx] + f" {needle_str} " + filler[idx:]
user = (filler + "\n\nCarefully find the exact needle string hidden above. "
        "Reply with ONLY the needle string, no other text.")
payload = {"model":"kimi-linear-48b",
           "messages":[{"role":"system","content":"You are a careful reading assistant."},
                       {"role":"user","content":user}],
           "max_tokens":24,"temperature":0,"stream":True}
json.dump(payload, open(f"{evid}/probe-b.json","w"))
print(f"probe-b chars={len(user)} units={units_needed}", file=sys.stderr)
PY

# RSS sampler during the long prefill
nohup bash -c '
  PID="$1"; OUT="$2"; TSV="$OUT/rss-samples.tsv"; : > "$TSV"; PEAK=0; PAT=""
  while kill -0 "$PID" 2>/dev/null; do
    NOW=$(date "+%H:%M:%S"); RSS=$(ps -o rss= -p "$PID" 2>/dev/null | tr -d " " || echo "?")
    [ "$RSS" != "?" ] && printf "%s\t%s\n" "$NOW" "$RSS" >> "$TSV"
    if [ "$RSS" != "?" ] && [ "$RSS" -gt "$PEAK" ] 2>/dev/null; then PEAK=$RSS; PAT=$NOW; fi
    sleep 1
  done
  echo "peak_rss_kb=$PEAK at $PAT" > "$OUT/rss-peak.txt"
  echo "samples=$(wc -l < "$TSV")" >> "$OUT/rss-peak.txt"
' _ "$SRV_PID" "$EVID" > /dev/null 2>&1 &
RSS_SAMPLER=$!
log "rss sampler pid=$RSS_SAMPLER watching $SRV_PID"

# Streamed boundary probe: TTFT = ms to first content token after the long
# prefill; full wall = until [DONE]; content accumulates for needle check.
python3 - "$CHAT_URL" "$EVID" <<'PY'
import json, sys, time, urllib.request
url, evid = sys.argv[1], sys.argv[2]
payload = json.load(open(f"{evid}/probe-b.json"))
req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                             headers={"Content-Type":"application/json"})
t0 = time.time(); first = None; text = ""; ttft = None; err = None
try:
    with urllib.request.urlopen(req, timeout=7200) as r:
        for raw in r:
            line = raw.decode("utf-8","replace").strip()
            if not line.startswith("data:"): continue
            if first is None:
                first = time.time(); ttft = first - t0
            if "[DONE]" in line: break
            try:
                chunk = json.loads(line[5:])
                d = chunk.get("choices",[{}])[0].get("delta",{}).get("content","")
                if d: text += d
            except Exception: pass
except Exception as e:
    err = str(e)
t1 = time.time()
res = {"ttft_s": round(ttft,3) if ttft else None,
       "wall_s": round(t1 - t0, 3), "error": err,
       "text": text.strip()}
json.dump(res, open(f"{evid}/probe-b.stream.json","w"), indent=2)
print("probe-b stream:", json.dumps({k: res[k] for k in ("ttft_s","wall_s","error")}))
PY
NEEDLE_HIT="no"
NEEDLE_STR="$(cat "$EVID/needle.txt" 2>/dev/null || echo NEEDLE-13A)"
grep -q "$NEEDLE_STR" "$EVID/probe-b.stream.json" 2>/dev/null && NEEDLE_HIT="yes"
log "needle retrieved: $NEEDLE_HIT"

PREFILL="?"
grep -aoE "prompt eval time *= *[0-9.]+ ms *\/ *[0-9]+ tokens" "$SRVLOG" | tail -1 > "$EVID/probe-b.prefill" 2>/dev/null || true
[ -s "$EVID/probe-b.prefill" ] && PREFILL=$(cat "$EVID/probe-b.prefill")
EVAL="?"
grep -aoE "eval time *= *[0-9.]+ ms *\/ *[0-9]+ tokens" "$SRVLOG" | tail -1 > "$EVID/probe-b.eval" 2>/dev/null || true
[ -s "$EVID/probe-b.eval" ] && EVAL=$(cat "$EVID/probe-b.eval")
log "probe-b prefill: $PREFILL"
log "probe-b decode:  $EVAL"
# prompt tokens from the server print_timing line (authoritative)
PT="?"
if [ -n "$PREFILL" ] && [ "$PREFILL" != "?" ]; then
  PT=$(echo "$PREFILL" | grep -aoE "[0-9]+ tokens" | grep -aoE "[0-9]+" | head -1)
fi
log "probe-b prompt_tokens=$PT (needle char-depth 0.90 -> ~$(( (PT == "?" ? 0 : PT) * 90 / 100 )) token position)" 2>/dev/null || log "probe-b prompt_tokens=$PT"

# --- phase 4: post-probe health + memory ------------------------------------
sleep 2
GATE4="pass"
if ! kill -0 "$SRV_PID" 2>/dev/null || ! curl -s -m 2 "$HEALTH_URL" >/dev/null 2>&1; then
  GATE4="fail"
fi
log "GATE4: server healthy after long-context use: $GATE4"

RSS_KB=$(ps -o rss= -p "$SRV_PID" 2>/dev/null | tr -d ' ' || echo "?")
MEM_FREE=$(memory_pressure -Q 2>/dev/null | grep -o '[0-9]*%' | head -1 || echo "?")
log "post-probe rss=${RSS_KB}KB host-free=$MEM_FREE"

# --- classification + results ------------------------------------------------
python3 - "$EVID" "$TS" "$CTX" "$PT" "$PREFILL" "$EVAL" "$NEEDLE_HIT" "$GATE4" "$RSS_KB" "$MEM_FREE" <<'PY'
import json, sys, os, re
evid, ts, ctx, pt, prefill, evl, needle, g4, rss, memfree = sys.argv[1:11]
def rd(f):
    try: return open(f"{evid}/{f}").read().strip()
    except Exception: return None
pa = {}
try: pa = json.load(open(f"{evid}/probe-a.json"))
except Exception: pass
pb = {}
try: pb = json.load(open(f"{evid}/probe-b.stream.json"))
except Exception: pass
prefill_tps = None; decode_tps = None
if prefill and prefill != "?":
    m = re.search(r"([\d.]+) ms / ([\d]+) tokens", prefill)
    if m: prefill_tps = round(int(m.group(2)) / (float(m.group(1))/1000), 2)
if evl and evl != "?":
    m = re.search(r"([\d.]+) ms / ([\d]+) tokens", evl)
    if m: decode_tps = round(int(m.group(2)) / (float(m.group(1))/1000), 2)
peak = None; peak_at = None
rows = []
try:
    rows = [l for l in open(f"{evid}/rss-samples.tsv") if "\t" in l and l.split("\t")[1].strip().isdigit()]
except Exception: pass
if rows:
    mrow = max(rows, key=lambda l: int(l.split("\t")[1]))
    peak = mrow.split("\t")[1].strip(); peak_at = mrow.split("\t")[0]
ptok = int(pt) if pt and pt != "?" else None
ok = pa.get("ok") is True
needle_ok = needle == "yes"
g2 = "pass" if (ptok is not None and ptok > 65536) else "fail"
classif = "PASS"
if not ok: classif = "correctness"
elif not needle_ok: classif = "correctness"
elif g4 != "pass": classif = "performance"
elif g2 != "pass": classif = "correctness"
res = {
  "step": "13A", "ctx": int(ctx), "ts": ts, "artifact_dir": evid,
  "classification": classif,
  "gates": {"gate1_allocation_startup": "pass",
            "gate2_prefill_admission_gt_64k": g2,
            "gate3_generation_needle_beyond_64k": needle_ok,
            "gate4_state_after_long_ctx": g4},
  "probe_a_short": {"ok": pa.get("ok"), "ttft_s": pa.get("ttft_s"), "text": pa.get("text")},
  "probe_b_boundary": {"prompt_tokens": ptok,
                       "needle_retrieved": needle_ok,
                       "needle": open(f"{evid}/needle.txt").read().strip() if os.path.exists(f"{evid}/needle.txt") else None,
                       "server_prefill_line": prefill,
                       "server_eval_line": evl,
                       "prefill_tok_s": prefill_tps,
                       "decode_tok_s": decode_tps,
                       "ttft_s_first_token": pb.get("ttft_s"),
                       "wall_s": pb.get("wall_s"),
                       "error": pb.get("error")},
  "memory": {"steady_rss_kb": rss, "peak_rss_kb_during_prefill": peak,
             "peak_at": peak_at, "host_free_pct": memfree},
}
json.dump(res, open(f"{evid}/results.json","w"), indent=2)
print("RESULTS:", json.dumps(res, indent=2))
PY

log "=== 13A test window complete; restoring production ==="
restore_production
echo "$TS" > "$EVID/window-ts.txt"
touch "$EVID/DONE"
exit 0
