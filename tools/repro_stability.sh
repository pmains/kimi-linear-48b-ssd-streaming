#!/bin/bash
# BUG-001 reproduction harness (SERVICE-ROADMAP Task 3B.1) — supervised, looped.
#
# Runs the INSTRUMENTED dev build on an alternate port (live server untouched)
# and executes the Task 3B.1 battery: idle-only, short-request, long-prefill,
# repeated-request. On server exit it records exit code/uptime + final log
# tail per incarnation (mirrors the launchd wrapper) and restarts, so repeated
# samples accumulate. The instrumented server logs the received signal and a
# backtrace at the delivery point, which is the decisive evidence.
#
# Usage:
#   tools/repro_stability.sh [--phases idle,short,long,repeat] [--max-runs N]
# Env:
#   KIMI_BIN        dev binary (default llama.cpp/build-metal/bin/llama-server)
#   KIMI_PORT       port (default 18081 — do NOT collide with live 18080)
#   KIMI_CACHE_MB   expert cache budget (default 4096, same as live)
#   KIMI_CTX        context size (default 32768, same as live)
set -u

KIMI_DIR=/Users/pmains/Code/openclaw/kimi
BIN="${KIMI_BIN:-$KIMI_DIR/llama.cpp/build-metal/bin/llama-server}"
PORT="${KIMI_PORT:-18081}"
CTX="${KIMI_CTX:-32768}"
CACHE_MB="${KIMI_CACHE_MB:-4096}"
MODEL="$KIMI_DIR/models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf"
RUN_DIR="/tmp/kimi-stability-$PORT"
LIFE="$RUN_DIR/lifecycle.log"
SRVLOG="$RUN_DIR/server.log"
PROMPT_LONG="$RUN_DIR/prompt_long.txt"
PHASES="${PHASES:-idle,short,long,repeat}"
MAX_RUNS="${MAX_RUNS:-6}"
IDLE_SECS="${IDLE_SECS:-900}"   # phase 1 idle-only duration

mkdir -p "$RUN_DIR"
export KIMI_STREAM_EXPERTS=naive
export KIMI_EXPERT_CACHE_MB="$CACHE_MB"
export KIMI_EXPERT_CACHE_MODE=zerocopy
export KIMI_STREAM_DEBUG=1  # temp-context pool instrumentation (BUG-001)

BASE_URL="http://127.0.0.1:$PORT"

log() { echo "[$(date '+%F %T %z')] $*" | tee -a "$LIFE"; }

wait_health() {
    for i in $(seq 1 120); do
        if curl -sf -m 2 "$BASE_URL/health" > /dev/null 2>&1; then
            log "health OK after ~${i}s"
            return 0
        fi
        sleep 1
    done
    log "ERROR: server never became healthy"
    return 1
}

short_req() {
    curl -sf -m 600 "$BASE_URL/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d '{"messages":[{"role":"user","content":"Say hello in one short sentence."}],"max_tokens":32,"temperature":0.0}' \
        > "$RUN_DIR/short_resp.json" 2>> "$LIFE" \
        && log "short request OK" || log "short request FAILED"
}

long_prefill() {
    if [ ! -s "$PROMPT_LONG" ]; then
        python3 - "$PROMPT_LONG" <<'PY'
import sys
# ~5k-token realistic-ish prompt: repeated code-review text (English ~4 chars/token)
par = ("The following diff introduces a bounded LRU cache for routed experts. "
       "Review it for correctness, memory safety, and cache eviction behavior. "
       "Pay attention to the slot id mapping, the zero-copy reuse path, and the "
       "failure handling when the backing store read returns fewer bytes than "
       "requested. If you find a bug, explain the exact sequence of events that "
       "triggers it and propose a minimal fix. Do not mention the cache policy "
       "unless it is directly relevant to a defect.\n\n")
target = 45000  # chars ~= 11k tokens (matches the prefill that crashed at 17:48)
text = (par * (target // len(par) + 1))[:target]
open(sys.argv[1], "w").write(text)
print(f"wrote {len(text)} chars to {sys.argv[1]}")
PY
    fi
    curl -sf -m 1800 "$BASE_URL/v1/chat/completions" \
        -H "Content-Type: application/json" \
        --data-binary @- <<EOF > "$RUN_DIR/long_resp.json" 2>> "$LIFE"
{"messages":[{"role":"user","content":$(python3 -c "import json,sys;print(json.dumps(open('$PROMPT_LONG').read()))")}],"max_tokens":8,"temperature":0.0}
EOF
    [ $? -eq 0 ] && log "long prefill OK" || log "long prefill FAILED"
}

repeat_reqs() {
    local n=${1:-10} ok=0
    for i in $(seq 1 "$n"); do
        curl -sf -m 300 "$BASE_URL/v1/chat/completions" \
            -H "Content-Type: application/json" \
            -d '{"messages":[{"role":"user","content":"What is 2+2? Answer with one number."}],"max_tokens":8,"temperature":0.0}' \
            > /dev/null 2>> "$LIFE" && ok=$((ok+1))
    done
    log "repeated requests: $ok/$n OK"
}

# ---- battery against whichever incarnation is up ----
battery() {
    local run=$1
    case ",$PHASES," in
      *,idle,*)
        log "phase idle-only ${IDLE_SECS}s (run $run)"
        sleep "$IDLE_SECS"
        if ! curl -sf -m 2 "$BASE_URL/health" > /dev/null; then log "phase idle: server died during idle"; return; fi
        log "phase idle: survived" ;;
    esac
    case ",$PHASES," in *,short,*) short_req ;; esac
    case ",$PHASES," in *,long,*)  long_prefill ;; esac
    case ",$PHASES," in *,repeat,*) repeat_reqs 10 ;; esac
}

# ---- main loop: run server incarnation, run battery, record exit ----
run_id=0
while [ "$run_id" -lt "$MAX_RUNS" ]; do
    run_id=$((run_id + 1))
    start_ts=$(date +%s)
    log "RUN $run_id START bin=$BIN ctx=$CTX cache=${CACHE_MB}MiB"
    "$BIN" -m "$MODEL" -ngl 0 --no-mmap --ctx-size "$CTX" \
        --host 127.0.0.1 --port "$PORT" --parallel 1 \
        >> "$SRVLOG" 2>&1 &
    SRV_PID=$!
    log "RUN $run_id server pid=$SRV_PID"

    if ! wait_health; then
        kill -9 "$SRV_PID" 2>/dev/null
        wait "$SRV_PID" 2>/dev/null
        log "RUN $run_id aborting (no health)"
        break
    fi

    battery "$run_id"

    # now sit and wait for the server to exit on its own (idle soak)
    log "RUN $run_id entering idle soak (waiting for exit...)"
    wait "$SRV_PID"
    code=$?
    end_ts=$(date +%s)
    log "RUN $run_id EXIT code=$code uptime=$((end_ts - start_ts))s"
    log "--- RUN $run_id final 50 server-log lines ---"
    tail -50 "$SRVLOG" >> "$LIFE"
    log "---------------------------------------------"
done

log "harness finished after $MAX_RUNS runs (or abort)"
