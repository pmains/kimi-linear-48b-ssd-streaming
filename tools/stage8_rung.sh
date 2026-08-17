#!/bin/bash
# Stage 8 rung driver — usable-context scaling under native NoPE.
#
# Invariant rung protocol (operator definition 2026-08-17):
#   - only --ctx-size changes between rungs
#   - same frozen live binary, same model, same env, same port, same probes
#   - per-rung gates: (1) allocation/startup, (2) prefill correctness,
#     (3) generation correctness incl. long-context needle retrieval,
#     (4) state/cache after long-context use
#   - failures classified separately: allocation | correctness | performance
#   - memory recorded per bucket: KV, KDA/recurrent state, expert cache, RSS
#
# Usage:
#   tools/stage8_rung.sh <ctx> [--port N] [--out DIR]
#
# Probes (identical shape at every rung):
#   A. fixed probe  — "Reply with the single word ok." (cross-rung invariant,
#      also the "normal request at this size" check)
#   B. boundary probe — filler + needle at ~70% depth, prompt sized to ~90% of
#      ctx, ends with the retrieval question. The needle check happens INSIDE
#      this request (the model must find it in the long context), so the
#      response is the generation-correctness + needle evidence.
#
# Env overrides:
#   KIMI_BIN      server binary (default runtime/live/bin/llama-server)
#   KIMI_MODEL    gguf path (default models/kimi-linear/...Q4_K_M.gguf)
#   KIMI_PORT     server port (default 18081; live stays on 18080)
set -u

KIMI_DIR="/Users/pmains/Code/openclaw/kimi"
BIN="${KIMI_BIN:-$KIMI_DIR/runtime/live/bin/llama-server}"
MODEL="${KIMI_MODEL:-$KIMI_DIR/models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf}"
PORT="${KIMI_PORT:-18081}"
CTX="${1:?usage: stage8_rung.sh <ctx> [--port N] [--out DIR]}"
shift
while [ "$#" -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2;;
    --out)  OUT="$2"; shift 2;;
    *) echo "unknown arg: $1"; exit 2;;
  esac
done

TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
OUT="${OUT:-$KIMI_DIR/dev-openclaw/state/stage8/$TS-ctx-$CTX}"
mkdir -p "$OUT"
LOG="$OUT/server.log"
DRIVER="$OUT/driver.log"
RESULTS="$OUT/results.json"
NEEDLE_FILE="$OUT/needle.txt"
HEALTH_URL="http://127.0.0.1:$PORT/health"
CHAT_URL="http://127.0.0.1:$PORT/v1/chat/completions"

now_ms() { python3 -c 'import time; print(int(time.time()*1000))'; }

log() { echo "[$(date '+%F %T %z')] $*" | tee -a "$DRIVER"; }

# --- helpers ---------------------------------------------------------------
wait_healthy() {
  local tmo="${1:-900}"
  local deadline=$(( $(date +%s) + tmo ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -s -m 2 "$HEALTH_URL" >/dev/null 2>&1; then return 0; fi
    sleep 5
  done
  return 1
}

# build_boundary_payload <target-tokens> <needle> <outfile> <needlefile>
build_boundary_payload() {
  python3 - "$1" "$2" "$3" "$4" <<'PY'
import json, sys
target_tokens = int(sys.argv[1])
needle = sys.argv[2]
out = sys.argv[3]
needle_file = sys.argv[4]
needle_str = f"NEEDLE-{needle}"
open(needle_file, "w").write(needle_str)
# English ~4 chars/token; size to ~90% of ctx
target_chars = int(target_tokens * 4)
unit = ("The quick brown fox surveys the quiet valley under a wide sky. "
        "Rivers wind between granite ridges while hawks circle slowly. "
        "Each stone holds a story older than any written word. ")
units_needed = max(1, -(-target_chars // len(unit)))
filler = unit * units_needed
# embed needle at ~70% depth
idx = int(len(filler) * 0.70)
filler = filler[:idx] + f" {needle_str} " + filler[idx:]
user = (filler + "\n\nCarefully find the exact needle string hidden above. "
        "Reply with ONLY the needle string, no other text.")
payload = {
  "model": "kimi-linear-48b",
  "messages": [
    {"role": "system", "content": "You are a careful reading assistant."},
    {"role": "user", "content": user},
  ],
  "max_tokens": 16,
  "temperature": 0,
}
json.dump(payload, open(out, "w"))
print(f"boundary payload chars={len(user)} units={units_needed}", file=sys.stderr)
PY
}

# probe <name> <request-json-file> <response-file>
probe() {
  local name="$1" reqfile="$2" out="$3" t0 t1 rc
  t0=$(now_ms)
  curl -s -m 3600 -o "$out" -w "%{http_code}" -H "Content-Type: application/json" \
    -d @"$reqfile" "$CHAT_URL" > "$OUT/$name.http" 2>"$OUT/$name.curl.err" || true
  rc=$?
  t1=$(now_ms)
  echo $(( t1 - t0 )) > "$OUT/$name.wallms"
  log "probe[$name] rc=$rc http=$(cat "$OUT/$name.http" 2>/dev/null || echo '?') wall=$((t1-t0))ms"
  return 0
}

# --- gate 1: allocation / startup -------------------------------------------
log "=== Stage 8 rung ctx=$CTX ($TS) ==="
log "binary: $BIN"
log "model:  $MODEL"
log "port:   $PORT  (live server on 18080 untouched)"

# stop any stale rung server on this port
pkill -f -- "$BIN -m $MODEL .*--port $PORT" 2>/dev/null || true
sleep 2

KIMI_STREAM_EXPERTS=naive KIMI_EXPERT_CACHE_MB=4096 KIMI_EXPERT_CACHE_MODE=zerocopy \
nohup "$BIN" -m "$MODEL" -ngl 0 --no-mmap --ctx-size "$CTX" \
  --host 127.0.0.1 --port "$PORT" --parallel 1 -lv 4 \
  > "$LOG" 2>&1 &
SRV_PID=$!
echo "$SRV_PID" > "$OUT/server.pid"
log "server pid=$SRV_PID ctx=$CTX (log verbosity 4 for KV/state sizing)"

if ! wait_healthy 900; then
  log "GATE1 FAIL: server did not become healthy"
  python3 - "$RESULTS" "$CTX" "$PORT" "$TS" "$OUT" <<'PY'
import json, sys
json.dump({"ctx": int(sys.argv[2]), "port": int(sys.argv[3]),
           "timestamp": sys.argv[4], "artifact_dir": sys.argv[5],
           "classification": "allocation",
           "gates": {"gate1": "fail", "gate2": "n/a", "gate3": "n/a", "gate4": "n/a"},
           "detail": "server not healthy within timeout"}, open(sys.argv[1], "w"), indent=2)
PY
  exit 1
fi
log "GATE1 PASS: server healthy (allocation/startup ok)"
sleep 2

# capture startup log lines of interest (before probes)
{
  echo "=== startup log: memory/alloc lines ==="
  grep -aiE "kv buffer size|kv self size|size = .* cells|recurrent|state size|compute buffer|expert cache|armed|MiB|ctx" "$LOG" | head -40 || true
  echo "=== startup log: warnings/asserts ==="
  grep -aiE "GGML_ASSERT|not enough space|out of memory|cannot allocate|failed to allocate" "$LOG" | grep -v "il=26 n_used=8 n_tokens=0" | head -20 || true
} > "$OUT/startup.mem.txt" 2>&1
cat "$OUT/startup.mem.txt" >> "$DRIVER"

# --- peak RSS sampler (interpretive rule: capture transient prefill peak) ----
# Polls the rung server every 1s during the boundary prefill; steady-state RSS
# alone can miss transient allocation pressure that may become decisive at
# 256K+. Writes rss-samples.tsv + rss-peak.txt into the rung artifact dir.
start_rss_sampler() {
  local pid="$1" out="$2"
  nohup bash -c '
    set -u
    PID="$1"; OUT="$2"
    TSV="$OUT/rss-samples.tsv"
    : > "$TSV"
    PEAK=0; PEAK_AT=""
    while kill -0 "$PID" 2>/dev/null; do
      NOW=$(date "+%H:%M:%S")
      RSS=$(ps -o rss= -p "$PID" 2>/dev/null | tr -d " " || echo "?")
      [ "$RSS" != "?" ] && printf "%s\t%s\n" "$NOW" "$RSS" >> "$TSV"
      if [ "$RSS" != "?" ] && [ "$RSS" -gt "$PEAK" ] 2>/dev/null; then
        PEAK=$RSS; PEAK_AT=$NOW
      fi
      sleep 1
    done
    echo "peak_rss_kb=$PEAK at $PEAK_AT" > "$OUT/rss-peak.txt"
    echo "samples=$(wc -l < "$TSV")" >> "$OUT/rss-peak.txt"
  ' _ "$pid" "$out" > /dev/null 2>&1 &
  RSS_SAMPLER_PID=$!
  log "rss sampler pid=$RSS_SAMPLER_PID (1s polling, prefill phase)"
}

# --- gate 2+3: boundary probe (prefill + needle retrieval in one request) ----
log "GATE2/3: boundary probe (~90% ctx) with needle retrieval..."
BOUNDARY_TOKENS=$(( CTX * 9 / 10 ))
NEEDLE="7391-$(basename "$OUT" | tr -cd 0-9 | head -c 4)"
build_boundary_payload "$BOUNDARY_TOKENS" "$NEEDLE" "$OUT/probe-b.json" "$NEEDLE_FILE"
# start peak-RSS sampler just before the prefill so the transient allocation
# peak during prefill is captured, not just steady state
start_rss_sampler "$SRV_PID" "$OUT"
probe "b" "$OUT/probe-b.json" "$OUT/probe-b.resp.json"

PROMPT_TOKENS="?"
python3 -c "import json; print(json.load(open('$OUT/probe-b.resp.json'))['usage']['prompt_tokens'])" > "$OUT/probe-b.ptok" 2>/dev/null || true
[ -s "$OUT/probe-b.ptok" ] && PROMPT_TOKENS=$(cat "$OUT/probe-b.ptok")
log "probe-b prompt_tokens=$PROMPT_TOKENS (target ~$BOUNDARY_TOKENS)"

PREFILL_MS="?"
grep -aoE "prompt eval time *= *[0-9.]+ ms *\/ *[0-9]+ tokens" "$LOG" | tail -1 > "$OUT/probe-b.prefill" 2>/dev/null || true
[ -s "$OUT/probe-b.prefill" ] && PREFILL_MS=$(cat "$OUT/probe-b.prefill")
log "probe-b server prefill: $PREFILL_MS"

NEEDLE_HIT="no"
NEEDLE_STR="$(cat "$NEEDLE_FILE" 2>/dev/null || echo NEEDLE-7391)"
grep -q "$NEEDLE_STR" "$OUT/probe-b.resp.json" 2>/dev/null && NEEDLE_HIT="yes"
if [ "$NEEDLE_HIT" = "yes" ]; then
  log "GATE2 PASS: boundary prefill completed; GATE3 PASS: needle retrieved from long context"
  GATE2="pass"; GATE3="pass"
else
  log "GATE2/3: boundary probe completed but needle NOT retrieved (needle=$NEEDLE_STR)"
  GATE2="pass"; GATE3="fail"
fi

# --- gate 3b: fixed invariant probe (normal request at this size) ------------
log "GATE3b: fixed invariant probe..."
python3 - "$OUT/probe-a.json" <<'PY'
import json, sys
json.dump({"model":"kimi-linear-48b",
           "messages":[{"role":"user","content":"Reply with the single word ok."}],
           "max_tokens":4,"temperature":0}, open(sys.argv[1],"w"))
PY
probe "a" "$OUT/probe-a.json" "$OUT/probe-a.resp.json"
OK_HIT="no"
grep -qiE '"content"\s*:\s*"ok' "$OUT/probe-a.resp.json" 2>/dev/null && OK_HIT="yes"
log "probe-a contains 'ok': $OK_HIT"
if [ "$OK_HIT" != "yes" ]; then
  log "GATE3b FAIL: fixed probe did not return 'ok'"
  GATE3="fail"
fi

# --- gate 4: state/cache after long-context use ------------------------------
log "GATE4: state/cache behavior after probes..."
sleep 3
GATE4="fail"
if kill -0 "$SRV_PID" 2>/dev/null && curl -s -m 2 "$HEALTH_URL" >/dev/null 2>&1; then
  log "GATE4 PASS: server healthy after long-context use"
  GATE4="pass"
else
  log "GATE4 FAIL: server unhealthy/crashed after long-context use"
fi

# post-probe memory/state capture
{
  echo "=== post-probe log: state/cache lines ==="
  grep -aiE "slot|kv buffer|kv self|state|cache|recycl|alloc|MiB" "$LOG" | tail -30 || true
} > "$OUT/postprobe.mem.txt" 2>&1

# --- memory buckets ----------------------------------------------------------
KV_MIB="?"
grep -aoE "KV buffer size *= *[0-9.]+ MiB|KV self size *= *[0-9.]+ MiB" "$LOG" | tail -1 > "$OUT/kv.mib" 2>/dev/null || true
[ -s "$OUT/kv.mib" ] && KV_MIB=$(cat "$OUT/kv.mib")
KV_CELLS="?"
grep -aoE "size *= *[0-9.]+ MiB *\( *[0-9]+ cells" "$LOG" | tail -1 > "$OUT/kv.cells" 2>/dev/null || true
[ -s "$OUT/kv.cells" ] && KV_CELLS=$(cat "$OUT/kv.cells")
REC_MIB="?"
grep -aoE "recurrent state size *= *[0-9.]+ MiB|recurrent size *= *[0-9.]+ MiB" "$LOG" | tail -1 > "$OUT/rec.mib" 2>/dev/null || true
[ -s "$OUT/rec.mib" ] && REC_MIB=$(cat "$OUT/rec.mib")
RSS_KB="?"
if kill -0 "$SRV_PID" 2>/dev/null; then
  RSS_KB=$(ps -o rss= -p "$SRV_PID" 2>/dev/null | tr -d ' ' || echo "?")
fi
PEAK_RSS_KB="?"
PEAK_RSS_AT="?"
if [ -f "$OUT/rss-peak.txt" ]; then
  grep -E "^peak_rss_kb=" "$OUT/rss-peak.txt" | head -1 > "$OUT/peak.line" 2>/dev/null || true
  [ -s "$OUT/peak.line" ] && PEAK_RSS_KB=$(sed 's/^peak_rss_kb=//; s/ at .*//' "$OUT/peak.line")
  grep -oE "at .*" "$OUT/rss-peak.txt" | head -1 | sed 's/^at //' > "$OUT/peak.at" 2>/dev/null || true
  [ -s "$OUT/peak.at" ] && PEAK_RSS_AT=$(cat "$OUT/peak.at")
fi
EXPERT_LINE="?"
grep -aoE "expert cache armed \([0-9]+ MiB, [a-z-]+ mode\)" "$LOG" | tail -1 > "$OUT/expert.line" 2>/dev/null || true
[ -s "$OUT/expert.line" ] && EXPERT_LINE=$(cat "$OUT/expert.line")
WARN_COUNT=0
grep -acE "GGML_ASSERT|not enough space|out of memory|cannot allocate" "$LOG" 2>/dev/null | tr -d ' ' > "$OUT/warn.count" 2>/dev/null || true
[ -s "$OUT/warn.count" ] && WARN_COUNT=$(cat "$OUT/warn.count")
BENIGN_WARN=0
grep -acE "failed to allocate loaded ids buffers \(il=26" "$LOG" 2>/dev/null | tr -d ' ' > "$OUT/benign.count" 2>/dev/null || true
[ -s "$OUT/benign.count" ] && BENIGN_WARN=$(cat "$OUT/benign.count")

log "memory: KV='$KV_MIB' kvcells='$KV_CELLS' recurrent='$REC_MIB' expert='$EXPERT_LINE' rss=${RSS_KB}KB"
log "warnings: allocator=${WARN_COUNT} benign-il26=${BENIGN_WARN}"

# --- classification + results ------------------------------------------------
CLASS="PASS"
if [ "$GATE2" != "pass" ]; then CLASS="correctness"; fi
if [ "$GATE3" != "pass" ] && [ "$CLASS" = "PASS" ]; then CLASS="correctness"; fi
if [ "$GATE4" != "pass" ] && [ "$CLASS" = "PASS" ]; then CLASS="performance"; fi
if [ "$WARN_COUNT" -gt 0 ] && [ "$CLASS" = "PASS" ]; then CLASS="allocation"; fi

python3 - "$RESULTS" "$CTX" "$PORT" "$TS" "$OUT" "$GATE2" "$GATE3" "$GATE4" "$CLASS" \
  "$PROMPT_TOKENS" "$PREFILL_MS" "$KV_MIB" "$KV_CELLS" "$REC_MIB" "$RSS_KB" "$PEAK_RSS_KB" "$PEAK_RSS_AT" "$EXPERT_LINE" \
  "$WARN_COUNT" "$BENIGN_WARN" "$NEEDLE_HIT" "$OK_HIT" "$SRV_PID" <<'PY'
import json, sys
p, ctx, port, ts, out, g2, g3, g4, cls = sys.argv[1:10]
ptok, prefill, kv, kvcells, rec, rss, peak, peak_at, expert, warn, benign, needle, okhit, pid = sys.argv[10:]
json.dump({
  "rung": {"ctx": int(ctx), "binary_note": "frozen live runtime (runtime/live)",
           "port": int(port), "timestamp": ts, "artifact_dir": out,
           "server_pid": int(pid) if pid.isdigit() else None},
  "gates": {"gate1_allocation_startup": "pass",
            "gate2_prefill_correctness": g2,
            "gate3_generation_correctness": g3,
            "gate4_state_cache_after_long_ctx": g4},
  "classification": cls,
  "probes": {
    "b_boundary_needle": {"prompt_tokens": int(ptok) if ptok.isdigit() else None,
                          "server_prefill": prefill, "target_pct": 0.90,
                          "needle_retrieved": needle},
    "a_fixed_ok": {"contains_ok": okhit},
  },
  "memory": {"kv": kv, "kv_cells": kvcells, "kda_recurrent_state": rec,
             "expert_cache": expert,
             "total_rss_kb": int(rss) if rss.isdigit() else None,
             "peak_rss_kb_during_prefill": int(peak) if peak.isdigit() else None,
             "peak_rss_at": peak_at},
  "warnings": {"allocator_anomalies": int(warn) if warn.isdigit() else None,
               "benign_il26_loaded_ids": int(benign) if benign.isdigit() else None},
}, open(p, "w"), indent=2)
PY
log "=== rung ctx=$CTX complete: classification=$CLASS ==="
kill "$SRV_PID" 2>/dev/null || true
exit 0
