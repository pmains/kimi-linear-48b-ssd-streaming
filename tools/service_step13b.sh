#!/bin/bash
# Step 13B driver — probe 256K (ctx 262144) feasibility on the current
# production runtime under the SINGLE-llama-server swap protocol (owner
# decision 2026-09-07: only one llama-server; live port 18080 is swapped for
# the test window, then restored to the launchd 64K job).
#
# Owner order 2026-09-07 11:40 MST ("Proceed with Step 13B exactly as
# specified in SERVICE-ROADMAP.md ... Do not optimize or alter
# expert-cache/runtime settings to make 256K fit."):
#   - same frozen runtime/env as the 13A PASS run; ONLY --ctx-size changes
#     65536 (launchd baseline) -> 262144 (test)
#   - 13A env: same live binary (COMMIT a895f6826), MXFP4 GGUF, E3E naive
#     stream, 8192 MiB expert cache, 4 read workers, zerocopy,
#     KIMI_STREAM_METAL_STAGE=1, KIMI_STREAM_E2_DIRECT_PLACE=1; observability
#     only (-lv 4, telemetry env redirected under the 13B evidence dir;
#     slot-save-path omitted exactly as 13A)
#
# Stages (SERVICE-ROADMAP.md 13B + owner order):
#   Stage 1 — allocation/startup at --ctx-size 262144: server healthy, Metal
#             init, KV/KDA lines, 8 GiB expert cache armed, resolved n_ctx,
#             short fixed probe (ok) with streamed TTFT, steady RSS, host
#             free %.  No OOM/assert in server log.
#             Headroom gate -> Stage 2 only if: healthy + n_ctx 262144 +
#             probe ok + steady RSS <= 17 GiB + host free >= 10%.
#   Stage 2 — beyond-128K probe: target 140K-160K ACTUAL prompt tokens (build
#             ~150K), deterministic needle placed beyond token 131,072
#             (target ~141K content token).  ACTUAL needle token position is
#             MEASURED via /tokenize + /detokenize on the test server
#             (binary search for the first prefix whose decode contains the
#             needle) — not estimated from char depth.
#   Restore — unconditional (EXIT trap + explicit tail): kill test server,
#             relaunch the launchd 64K job, verify /health, then write a
#             restore-verify record (llama 200, n_ctx 65536, gateway 200,
#             11B sha 8baf4746.., FK violations 0).
#   Classify — suggested 13B classification (FEASIBLE / FEASIBLE WITH
#              OPERATING LIMIT / NOT PRACTICAL) is written to results.json;
#              final decision is recorded in the phase report at the gate.
#
# Memory safety during Stage 2: RSS sampler (1 s) + host-free sampler; a
# watchdog aborts the probe if llama RSS >= 18 GiB or host free <= 3%
# (client exits via ABORT flag; server killed after a short grace; the
# restore path then returns production).  Boundary evidence is retained in
# either outcome so the measured limit can be classified.
#
# Usage: tools/service_step13b.sh
set -u

KIMI_DIR=/Users/pmains/Code/openclaw/kimi
cd "$KIMI_DIR" || exit 9
EVID="$KIMI_DIR/benchmarks/results/service-step-13/13b-256k"
TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
mkdir -p "$EVID/telemetry"
LOG="$EVID/driver.log"
SRVLOG="$EVID/server-262144.log"
PLIST="$HOME/Library/LaunchAgents/com.openclaw.kimi-llama-server.plist"
LABEL=com.openclaw.kimi-llama-server
UID_N="$(id -u)"
PORT=18080
CTX=262144
BIN="$KIMI_DIR/runtime/live/bin/llama-server"
MODEL="models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf"
HEALTH_URL="http://127.0.0.1:$PORT/health"
CHAT_URL="http://127.0.0.1:$PORT/v1/chat/completions"
TOK_URL="http://127.0.0.1:$PORT/tokenize"
DTOK_URL="http://127.0.0.1:$PORT/detokenize"
SRV_PID=""
RESTORED=0
GIB_KB=$(( 1024*1024 ))

log() { echo "[$(date '+%F %T %z')] $*" | tee -a "$LOG"; }

wait_healthy() {
  local tmo="${1:-600}"
  local deadline
  deadline=$(( $(date +%s) + tmo ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -s -m 2 "$HEALTH_URL" >/dev/null 2>&1; then return 0; fi
    sleep 3
  done
  return 1
}
# fail-fast variant: if the test server process dies (e.g. Metal OOM during
# startup), declare failure shortly after rather than burning the timeout.
wait_healthy_fast() {
  local tmo="${1:-600}"
  local deadline
  deadline=$(( $(date +%s) + tmo ))
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
  local deadline
  deadline=$(( $(date +%s) + tmo ))
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
  local deadline
  deadline=$(( $(date +%s) + 300 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -s -m 2 "$HEALTH_URL" >/dev/null 2>&1; then break; fi
    sleep 3
  done
  log "RESTORE: llama /health check done"
}
trap 'log "EXIT trap fired"; restore_production' EXIT

log "=== Step 13B ctx=$CTX swap window $TS ==="
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
LH=$(curl -s -m 3 -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo 000)
GW=$(curl -s -m 3 -o /dev/null -w "%{http_code}" http://127.0.0.1:18789/healthz 2>/dev/null || echo 000)
if [ "$LH" != "200" ] || [ "$GW" != "200" ]; then
  log "PREFLIGHT ABORT: llama=$LH gw=$GW — production not healthy; refusing swap"
  exit 2
fi
log "preflight OK: llama=$LH gw=$GW"

# --- phase 1: swap (bootout launchd 64K -> manual 262144 server) -------------
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
  log "GATE1 FAIL: manual 262144 server did not become healthy (allocation/Metal/startup boundary)"
  tail -40 "$SRVLOG" >> "$LOG"
  exit 1
fi
log "GATE1 PASS: allocation/startup healthy at ctx=$CTX"
sleep 2
CTX_ACTUAL=$(curl -s -m 3 "http://127.0.0.1:$PORT/slots" | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['n_ctx'])" 2>/dev/null || echo "?")
log "server resolved n_ctx=$CTX_ACTUAL (expected $CTX)"

{
  echo "=== startup: KV/recurrent/expert/Metal/ctx/compute lines ==="
  grep -aiE "KV buffer size|KV self size|llama_kv_cache: size|llama_memory_recurrent|expert cache armed|compute buffer|metal|context length|n_ctx|ggml_metal|not enough space|out of memory|cannot allocate|GGML_ASSERT|failed to allocate" "$SRVLOG" | head -80 || true
} > "$EVID/startup.mem.txt" 2>&1
cat "$EVID/startup.mem.txt" >> "$LOG"

# --- phase 2: Stage 1 short probe (fixed ok) + streamed TTFT -----------------
log "--- phase 2 (Stage 1) short probe + TTFT ---"
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

# Stage 1 memory snapshot (steady, after short use): 3 samples, median RSS.
sleep 4
RSS_A="$(ps -o rss= -p "$SRV_PID" 2>/dev/null | tr -d ' ' || echo '?')"
sleep 2
RSS_B="$(ps -o rss= -p "$SRV_PID" 2>/dev/null | tr -d ' ' || echo '?')"
sleep 2
RSS_C="$(ps -o rss= -p "$SRV_PID" 2>/dev/null | tr -d ' ' || echo '?')"
FREE_PCT=$(memory_pressure -Q 2>/dev/null | grep -o '[0-9]*%' | head -1 || echo "?")
log "stage1 steady rss samples (KB): $RSS_A $RSS_B $RSS_C; host free $FREE_PCT"

python3 - "$EVID" "$CTX_ACTUAL" "$RSS_A" "$RSS_B" "$RSS_C" "$FREE_PCT" "$SRVLOG" <<'PY'
import json, re, sys, os
evid, ctx_actual, rss_a, rss_b, rss_c, free_pct, srvlog = sys.argv[1:8]
def num(x):
    try: return int(x)
    except Exception: return None
rss = sorted([v for v in (num(rss_a), num(rss_b), num(rss_c)) if v is not None])
rss_med = rss[len(rss)//2] if rss else None
free = num(str(free_pct).rstrip('%')) if free_pct else None
# OOM/abort detection, fatal-only.  Run-1 lesson: the naive grep matched the
# word "abort" inside the BENIGN llama.cpp fit-params warning
#   'common_fit_params: failed to fit params to free device memory:
#    n_gpu_layers already set by user to 999, abort'
# (auto-fit tuning aborts because -ngl 999 was already set; not a real
# allocation failure).  Exclude that line and require a real fatal signal.
BAD = ("out of memory", "not enough space", "cannot allocate",
       "ggml_assert", "metal: error", "failed to allocate", "abort()",
       "terminate called", "std::bad_alloc")
SKIP = ("common_fit_params", "n_gpu_layers already set by user")
oom_lines = []
for ln in open(srvlog, errors="replace"):
    low = ln.lower()
    if any(s in low for s in SKIP):
        continue
    if any(s in low for s in BAD):
        oom_lines.append(ln.rstrip())
oom = len(oom_lines) > 0
probe = {}
try: probe = json.load(open(f"{evid}/probe-a.json"))
except Exception: pass
ok_alloc = (ctx_actual == "262144") and (probe.get("ok") is True) and (not oom)
# Headroom gate (owner order: proceed to the beyond-128K probe only if
# allocation is healthy and memory headroom is acceptable).
rss_ok = (rss_med is not None) and (rss_med <= 17 * 1024 * 1024)
free_ok = (free is not None) and (free >= 10)
go_stage2 = bool(ok_alloc and rss_ok and free_ok)
reasons = []
if ctx_actual != "262144": reasons.append(f"resolved ctx {ctx_actual} != 262144")
if probe.get("ok") is not True: reasons.append("short probe not ok")
if oom: reasons.append("fatal OOM/assert lines in server log: %s" % oom_lines)
if not rss_ok: reasons.append(f"steady RSS {rss_med} KB > 17 GiB" if rss_med else "RSS unreadable")
if not free_ok: reasons.append(f"host free {free_pct} < 10%")
rec = {"stage1": "pass" if ok_alloc else "fail",
       "go_stage2": go_stage2, "gate_reasons": reasons,
       "resolved_n_ctx": ctx_actual,
       "steady_rss_kb": rss_med, "steady_rss_gib": round(rss_med/1024/1024, 2) if rss_med else None,
       "host_free_pct": free,
       "probe_a": probe,
       "oom_lines_present": oom,
       "oom_matched_lines": oom_lines}
json.dump(rec, open(f"{evid}/stage1.json","w"), indent=2)
print("STAGE1:", json.dumps(rec))
PY
STAGE1_GO=$(python3 -c "import json; print(json.load(open('$EVID/stage1.json'))['go_stage2'])" 2>/dev/null || echo False)

if [ "$STAGE1_GO" != "True" ]; then
  log "STAGE 2 SKIPPED: headroom/allocation gate not met (see stage1.json). Classifying measured boundary."
  touch "$EVID/STAGE2_SKIPPED"
else
  log "STAGE 2 GO: allocation healthy + headroom acceptable -> beyond-128K probe"
fi

# --- phase 3 (Stage 2, conditional): 150K-token probe, needle > 131072 ------
if [ "$STAGE1_GO" = "True" ]; then
  log "--- phase 3 (Stage 2) beyond-128K needle probe ---"
  rm -f "$EVID/ABORT"
  NEEDLE="13B-$(date +%N | head -c 4)"
  NEEDLE_STR="NEEDLE-$NEEDLE"
  echo "$NEEDLE_STR" > "$EVID/needle.txt"

  # Build + calibrate the prompt against the TEST server tokenizer, then
  # MEASURE the needle token position (binary search over /detokenize).
  python3 - "$PORT" "$NEEDLE_STR" "$EVID" <<'PY'
import json, sys, time, urllib.request
port, needle, evid = int(sys.argv[1]), sys.argv[2], sys.argv[3]
base = f"http://127.0.0.1:{port}"

def tok(content, add_special=False, timeout=300):
    body = json.dumps({"content": content, "add_special": add_special}).encode()
    req = urllib.request.Request(f"{base}/tokenize", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r).get("tokens", [])

def dtok(ids, timeout=300):
    body = json.dumps({"tokens": ids}).encode()
    req = urllib.request.Request(f"{base}/detokenize", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r).get("content", "")

unit = ("The quick brown fox surveys the quiet valley under a wide sky. "
        "Rivers wind between granite ridges while hawks circle slowly. "
        "Each stone holds a story older than any written word. ")
instr = ("\n\nCarefully find the exact needle string hidden above. "
         "Reply with ONLY the needle string, no other text.")
sysmsg = "You are a careful reading assistant."
try:
    tpu = len(tok(unit))
except Exception as e:
    print("calibrate fatal: tokenize unit failed:", e); sys.exit(3)

# Target ~150K content tokens (actual prompt 140K-160K incl. template ~40).
target_m = 149_950
units = max(1, int(target_m / tpu))
depth = 0.94   # needle at 94% of the filler char stream -> ~141K content tokens

def build_content(u):
    filler = unit * u
    idx = int(len(filler) * depth)
    return filler[:idx] + f" {needle} " + filler[idx:] + instr

content = build_content(units)
m = len(tok(content))
# one nudge pass if the measured content token count drifted
if m < 140_000 or m > 155_000:
    units = max(1, int(units * (150_000 / max(1, m))))
    content = build_content(units)
    m = len(tok(content))

cal = {"unit_tokens": tpu, "units": units, "content_tokens": m,
       "target_content_tokens": target_m, "char_depth": depth,
       "needle": needle}
print(f"calibrate: unit_tokens={tpu} units={units} content_tokens={m}")

ids = tok(content)
needle_method = "not-measured"
needle_end_content_token = None
if len(ids) > 0:
    try:
        lo, hi = 0, len(ids)
        probe_lo = dtok(ids[:hi])
        if needle in probe_lo:
            while lo < hi:
                mid = (lo + hi) // 2
                if needle in dtok(ids[:mid]):
                    hi = mid
                else:
                    lo = mid + 1
            needle_end_content_token = lo   # first prefix containing the full needle
            needle_method = "detokenize-binary-search"
            cal["needle_end_content_token"] = needle_end_content_token
            cal["needle_method"] = needle_method
            print(f"calibrate: needle end at content token {lo} of {len(ids)}")
        else:
            print("calibrate warn: needle not found in full detokenize output")
    except Exception as e:
        print("calibrate warn: needle measurement failed:", e)
json.dump(cal, open(f"{evid}/calibrate.json","w"), indent=2)
user = content
payload = {"model":"kimi-linear-48b",
           "messages":[{"role":"system","content":sysmsg},
                       {"role":"user","content":user}],
           "max_tokens":24,"temperature":0,"stream":True}
json.dump(payload, open(f"{evid}/probe-b.json","w"))
print(f"probe-b chars={len(user)} content_tokens={m}", file=sys.stderr)
PY
  CALIB_OK=$(python3 -c "import json; d=json.load(open('$EVID/calibrate.json')); print('ok' if d.get('content_tokens',0)>=130000 else 'bad')" 2>/dev/null || echo bad)
  if [ "$CALIB_OK" != "ok" ]; then
    log "STAGE 2 FAIL: prompt calibration did not reach >=130K content tokens; see calibrate.json"
    touch "$EVID/STAGE2_CALIB_FAIL"
  else
    # RSS + host-free sampler with watchdog (abort if RSS >= 18 GiB or free <= 3%)
    nohup bash -c '
      PID="$1"; OUT="$2"
      TSV="$OUT/rss-samples.tsv"; : > "$TSV"
      WDG="$OUT/watchdog.tsv"; : > "$WDG"
      PEAK=0; PAT=""; ABORTF="$OUT/ABORT"; rm -f "$ABORTF"
      ABORTED=0
      while kill -0 "$PID" 2>/dev/null; do
        NOW=$(date "+%H:%M:%S")
        RSS=$(ps -o rss= -p "$PID" 2>/dev/null | tr -d " " || echo "?")
        [ "$RSS" != "?" ] && printf "%s\t%s\n" "$NOW" "$RSS" >> "$TSV"
        if [ "$RSS" != "?" ] && [ "$RSS" -gt "$PEAK" ] 2>/dev/null; then PEAK=$RSS; PAT=$NOW; fi
        # host free every 6th sample
        if [ $(( $(date +%s) % 6 )) -lt 2 ]; then
          FREE=$(memory_pressure -Q 2>/dev/null | grep -o "[0-9]*%" | head -1)
          printf "%s\t%s\t%s\n" "$NOW" "${RSS:-?}" "$FREE" >> "$WDG"
          if [ "$RSS" != "?" ] && [ "$ABORTED" = 0 ]; then
            if [ "$RSS" -ge 18874368 ] 2>/dev/null; then ABORTED=1; echo "rss" > "$ABORTF"; fi
            FV=$(echo "$FREE" | tr -d "%")
            if [ "$FV" != "" ] && [ "$FV" -le 3 ] 2>/dev/null; then ABORTED=1; echo "free" > "$ABORTF"; fi
          fi
        fi
        sleep 1
      done
      echo "peak_rss_kb=$PEAK at $PAT" > "$OUT/rss-peak.txt"
      echo "aborted=$ABORTED reason=$(cat "$ABORTF" 2>/dev/null || echo none)" >> "$OUT/rss-peak.txt"
      echo "samples=$(wc -l < "$TSV")" >> "$OUT/rss-peak.txt"
    ' _ "$SRV_PID" "$EVID" > /dev/null 2>&1 &
    RSS_SAMPLER=$!
    log "rss sampler/watchdog pid=$RSS_SAMPLER watching $SRV_PID"

    # Watchdog enforcement: give the client up to 90 s to notice ABORT and
    # exit, then kill the test server so an unsafe prefill cannot continue.
    nohup bash -c '
      OUT="$1"; ABORTF="$OUT/ABORT"; SRV="$2"
      for i in $(seq 1 60); do
        [ -f "$ABORTF" ] && break
        sleep 5
      done
      if [ -f "$ABORTF" ]; then
        sleep 60   # client grace to record partial state
        kill -9 "$SRV" 2>/dev/null || true
        touch "$OUT/WATCHDOG_KILLED"
      fi
    ' _ "$EVID" "$SRV_PID" > /dev/null 2>&1 &
    WDG_PID=$!
    log "watchdog enforcer pid=$WDG_PID (ABORT -> kill after grace)"

    python3 - "$PORT" "$EVID" <<'PY'
import http.client, json, os, sys, threading, time
port, evid = int(sys.argv[1]), sys.argv[2]
abortf = os.path.join(evid, "ABORT")
payload = json.load(open(os.path.join(evid, "probe-b.json")))
conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
conn.request("POST", "/v1/chat/completions", body=json.dumps(payload).encode(),
             headers={"Content-Type": "application/json",
                      "Accept": "text/event-stream"})
resp = conn.getresponse()
if resp.status != 200:
    print("HTTP", resp.status, resp.read(4000).decode("utf-8", "replace"))
    sys.exit(3)
# Poison-proof body read: http.client's SocketIO permanently raises
# "cannot read from timed out object" after the FIRST socket timeout (the
# run-2 failure mode), so body reads must be BLOCKING.  A guardian thread
# enforces the ABORT flag and the hard deadline by closing the connection,
# which unblocks the read; the bash watchdog enforcer additionally kills the
# server ~60 s after ABORT, also unblocking the read via connection reset.
sock = conn.sock
sock.settimeout(None)
guard_done = threading.Event()
def guardian():
    deadline = time.time() + 10800.0   # 3 h hard cap
    while not guard_done.is_set():
        if os.path.exists(abortf) or time.time() > deadline:
            try: conn.close()
            except Exception: pass
            return
        time.sleep(2)
threading.Thread(target=guardian, daemon=True).start()

t0 = time.time(); first = None; text = ""; err = None; done = False
buf = b""
try:
    while not done:
        try:
            chunk = resp.read(65536)
        except Exception as e:
            if os.path.exists(abortf):
                err = "aborted by memory watchdog (ABORT flag)"
            else:
                err = "read ended: %s" % e
            break
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            if first is None:
                first = time.time()
            if "[DONE]" in line:
                done = True; break
            try:
                d = json.loads(line[5:])
                c = d.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if c:
                    text += c
            except Exception:
                pass
except Exception as e:
    err = err or ("client exception: %s" % e)
guard_done.set()
t1 = time.time()
res = {"ttft_s": round(first - t0, 3) if first else None,
       "wall_s": round(t1 - t0, 3), "error": err, "done": done,
       "text": text.strip()}
json.dump(res, open(os.path.join(evid, "probe-b.stream.json"), "w"), indent=2)
print("probe-b stream:", json.dumps({k: res[k] for k in ("ttft_s","wall_s","error","done")}))
PY
    NEEDLE_HIT="no"
    NEEDLE_STR2="$(cat "$EVID/needle.txt" 2>/dev/null || echo NEEDLE-13B)"
    grep -q "$NEEDLE_STR2" "$EVID/probe-b.stream.json" 2>/dev/null && NEEDLE_HIT="yes"
    log "needle retrieved: $NEEDLE_HIT"

    PREFILL="?"
    # probe-b only: prefill line must show >= 100000 prompt tokens (probe-a's
    # 14-token line and stale lines must not masquerade as probe-b results).
    grep -aoE "prompt eval time *= *[0-9.]+ ms *\/ *[0-9]{6,} tokens" "$SRVLOG" | tail -1 > "$EVID/probe-b.prefill" 2>/dev/null || true
    [ -s "$EVID/probe-b.prefill" ] && PREFILL=$(cat "$EVID/probe-b.prefill")
    EVAL="?"
    # decode line for probe-b: >= 5 generated tokens (excludes probe-a's 2-token line)
    grep -aoE "eval time *= *[0-9.]+ ms *\/ *([5-9]|[0-9]{2,}) tokens" "$SRVLOG" | tail -1 > "$EVID/probe-b.eval" 2>/dev/null || true
    [ -s "$EVID/probe-b.eval" ] && EVAL=$(cat "$EVID/probe-b.eval")
    log "probe-b prefill: $PREFILL"
    log "probe-b decode:  $EVAL"
  fi
fi

# --- phase 4: post-probe health + memory -------------------------------------
sleep 2
GATE4="pass"
if [ -n "$SRV_PID" ]; then
  if ! kill -0 "$SRV_PID" 2>/dev/null || ! curl -s -m 2 "$HEALTH_URL" >/dev/null 2>&1; then
    GATE4="fail"
  fi
else
  GATE4="fail"
fi
log "GATE4: server healthy after run: $GATE4"

RSS_KB="?"
[ -n "$SRV_PID" ] && RSS_KB=$(ps -o rss= -p "$SRV_PID" 2>/dev/null | tr -d ' ' || echo "?")
MEM_FREE=$(memory_pressure -Q 2>/dev/null | grep -o '[0-9]*%' | head -1 || echo "?")
log "post-probe rss=${RSS_KB}KB host-free=$MEM_FREE"

# --- results + suggested classification ---------------------------------------
python3 - "$EVID" "$TS" "$CTX" "$GATE4" "$RSS_KB" "$MEM_FREE" "$STAGE1_GO" <<'PY'
import json, os, re, sys
evid, ts, ctx, g4, rss, memfree, stage1_go = sys.argv[1:8]
def rd(f):
    try: return open(f"{evid}/{f}").read().strip()
    except Exception: return None
def jrd(f):
    try: return json.load(open(f"{evid}/{f}"))
    except Exception: return {}
pa = jrd("probe-a.json"); pb = jrd("probe-b.stream.json")
s1 = jrd("stage1.json"); cal = jrd("calibrate.json")
prefill = rd("probe-b.prefill"); evl = rd("probe-b.eval")
prefill_tps = decode_tps = None
if prefill:
    m = re.search(r"([\d.]+) ms / ([\d]+) tokens", prefill)
    if m: prefill_tps = round(int(m.group(2)) / (float(m.group(1))/1000), 2)
if evl:
    m = re.search(r"([\d.]+) ms / ([\d]+) tokens", evl)
    if m: decode_tps = round(int(m.group(2)) / (float(m.group(1))/1000), 2)
pt = None
if prefill:
    m = re.search(r"([\d.]+) tokens", prefill)
    if m: pt = int(m.group(1))
peak = None; peak_at = None
rows = []
try:
    rows = [l for l in open(f"{evid}/rss-samples.tsv") if "\t" in l and l.split("\t")[1].strip().isdigit()]
except Exception: pass
if rows:
    mrow = max(rows, key=lambda l: int(l.split("\t")[1]))
    peak = mrow.split("\t")[1].strip(); peak_at = mrow.split("\t")[0]
needle_ok = bool(pb.get("text") and "NEEDLE-" in pb["text"])
needle_end_ct = cal.get("needle_end_content_token")
needle_method = cal.get("needle_method", "not-measured")
# Prompt-relative needle position estimate: content needle end + template
# overhead.  Template overhead = actual prompt tokens (pt) - content tokens
# (cal) - small template tail after the user turn (~10 tokens).  Needle is
# inside the content, so its first token is ~ needle_end - needle_tokens;
# we report the measured content-relative end and the derived estimate.
m_ct = cal.get("content_tokens")
needle_pos_prompt_est = None
if needle_end_ct is not None and pt and m_ct:
    overhead = max(0, pt - m_ct - 10)          # template head tokens
    span = 6                                   # needle span tokens ~ NEEDLE-13B-XXXX
    needle_pos_prompt_est = overhead + (needle_end_ct - span)
stage2_ran = (stage1_go == "True")
stage2_state = "ran"
if not stage2_ran: stage2_state = "skipped-stage1-gate"
elif os.path.exists(f"{evid}/STAGE2_CALIB_FAIL"): stage2_state = "calib-fail"
elif pb.get("error") == "aborted by memory watchdog (ABORT flag)": stage2_state = "aborted-memory-watchdog"
elif os.path.exists(f"{evid}/WATCHDOG_KILLED"): stage2_state = "aborted-memory-watchdog"
# suggested classification (final decision in the phase report)
sug = None
if s1.get("stage1") != "pass":
    sug = "256K NOT PRACTICAL (stage-1 allocation/startup boundary: %s)" % "; ".join(s1.get("gate_reasons", []))
elif stage2_state == "skipped-stage1-gate":
    sug = "256K NOT PRACTICAL (headroom gate not met: %s)" % "; ".join(s1.get("gate_reasons", []))
elif stage2_state in ("aborted-memory-watchdog",):
    sug = "256K FEASIBLE WITH OPERATING LIMIT (deep-context prefill hit memory watchdog; boundary evidence retained)"
elif needle_ok and (needle_pos_prompt_est is None or needle_pos_prompt_est > 131072 + 2000):
    sug = "256K FEASIBLE WITH OPERATING LIMIT" if (peak and int(peak) > 16*1024*1024) else "256K FEASIBLE"
elif not needle_ok:
    sug = "256K NOT PRACTICAL (needle beyond 128K not retrieved)"
else:
    sug = "256K FEASIBLE WITH OPERATING LIMIT (retrieval ok but margin/position ambiguous)"
res = {
  "step": "13B", "ctx": int(ctx), "ts": ts, "artifact_dir": evid,
  "stage2": {"ran": stage2_ran, "state": stage2_state},
  "suggested_classification": sug,
  "gates": {"stage1_allocation_startup": s1.get("stage1"),
            "stage2_beyond_128k_retrieval": "pass" if (needle_ok and stage2_state == "ran") else stage2_state,
            "gate4_state_after_run": g4},
  "configured_ctx": int(ctx),
  "resolved_n_ctx": s1.get("resolved_n_ctx"),
  "probe_a_short": {"ok": pa.get("ok"), "ttft_s": pa.get("ttft_s"), "text": pa.get("text")},
  "probe_b_beyond_128k": {
      "prompt_tokens_actual": pt,
      "content_tokens": m_ct,
      "needle_end_content_token": needle_end_ct,
      "needle_measure_method": needle_method,
      "needle_prompt_position_est": needle_pos_prompt_est,
      "needle_beyond_131072_margin_est": (needle_pos_prompt_est - 131072) if needle_pos_prompt_est is not None else None,
      "needle_retrieved": needle_ok,
      "needle": rd("needle.txt"),
      "server_prefill_line": prefill,
      "server_eval_line": evl,
      "prefill_tok_s": prefill_tps,
      "decode_tok_s": decode_tps,
      "ttft_s_first_content": pb.get("ttft_s"),
      "wall_s": pb.get("wall_s"),
      "error": pb.get("error"),
      "done": pb.get("done"),
      "response": pb.get("text")},
  "memory": {"steady_rss_kb": s1.get("steady_rss_kb"),
             "steady_rss_gib": s1.get("steady_rss_gib"),
             "peak_rss_kb_during_run": peak,
             "peak_at": peak_at,
             "post_run_rss_kb": rss,
             "host_free_pct": memfree,
             "watchdog": rd("rss-peak.txt")},
  "startup_highlights": rd("startup.mem.txt"),
}
json.dump(res, open(f"{evid}/results.json","w"), indent=2)
print("RESULTS:", json.dumps(res, indent=2))
PY

# --- phase 5: restore production + verify -------------------------------------
log "=== 13B test window complete; restoring production ==="
restore_production
sleep 3
python3 - "$EVID" "$TS" <<'PY'
import json, subprocess, sys
evid, ts = sys.argv[1], sys.argv[2]
def sh(c):
    try: return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=25).stdout.strip()
    except Exception: return "?"
rec = {
  "ts": ts,
  "llama_health_after": sh("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/health"),
  "llama_ctx_after": sh("curl -s http://127.0.0.1:18080/slots | python3 -c \"import json,sys; print(json.load(sys.stdin)[0]['n_ctx'])\""),
  "gateway_health_after": sh("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18789/healthz"),
  "step11b_sha_after": sh("shasum -a 256 /opt/homebrew/lib/node_modules/openclaw/dist/redact-CquADQ9-.js | cut -c1-10"),
  "fk_violations_after": sh("python3 -c \"import sqlite3; print(len(sqlite3.connect('/Users/pmains/.openclaw/agents/kimi/agent/openclaw-agent.sqlite').execute('PRAGMA foreign_key_check').fetchall()))\""),
  "leftover_test_servers": sh("pgrep -f 'ctx-size 262144' | wc -l | tr -d ' '"),
}
json.dump(rec, open(f"{evid}/restore-verify.json","w"), indent=2)
print("RESTORE VERIFY:", json.dumps(rec))
PY
echo "$TS" > "$EVID/window-ts.txt"
touch "$EVID/DONE"
log "=== 13B driver complete ==="
exit 0
