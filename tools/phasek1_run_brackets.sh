#!/bin/bash
# Phase K1 bracket harness: MXFP4 vs frozen Q4_K_M control.
#
# K-track evaluation under the frozen 9G protocol (ROADMAP.md: "each
# phase gated on measured evidence and evaluated under the frozen 9G
# protocol"). The 9G bracket structure, seeded within-bracket
# randomization, paired estimator, env covariates, and artifact set are
# preserved verbatim. The candidate differs from the 9G positive
# control: B is a different MODEL FILE (MXFP4_MOE quant), not a worker
# count. A stays the frozen Phase 8/9D/9F byte-identical control path
# (Q4_K_M, KIMI_EXPERT_READ_WORKERS=1).
#
# Experimental unit (one bracket):
#     A_before -> B -> A_after   (seeded random slot order)
#   A = Q4_K_M, workers=1 (frozen control)
#   B = MXFP4_MOE, workers=1 (candidate)
# Paired speedup:
#     S_i = tok/s(B_i) / mean(tok/s(A_before,i), tok/s(A_after,i))
#
# Model identity is recorded per run in <dir>/k1-model.json (the frozen
# phase04 runner's manifest.json does not record the model path).
#
# Usage:
#   tools/phasek1_run_brackets.sh [OUTDIR] [N_BRACKETS] [N_TOKENS]
# Env:
#   CONFIG      coding-cap4 (default) | reasoning-cap8 | uncached
#   A_MODEL     Q4_K_M GGUF path (default: repo models/kimi-linear/...Q4_K_M.gguf)
#   B_MODEL     MXFP4 GGUF path (default: repo models/kimi-linear/...MXFP4_MOE.gguf)
#   WORKERS     1 (both A and B; the candidate is the model, not the I/O path)
#   SEED        RNG seed for bracket-order randomization (recorded in harness.json)
#   CTX         4096 (KV-cache pin, matches the 9D/9F ladder / 9G harness)
#
# Analysis:
#   python3 tools/phasek1_analyze.py <OUTDIR>
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTDIR="${1:-benchmarks/results/phase-k1/brackets}"
N_BRACKETS="${2:-10}"
N="${3:-128}"
CONFIG="${CONFIG:-coding-cap4}"
WORKERS="${WORKERS:-1}"
SEED="${SEED:-$(date +%s)}"
export CTX="${CTX:-4096}"
export KIMI_EXPERT_CACHE_MODE=zerocopy
export KIMI_PHASE7_INSTR=1

A_MODEL="${A_MODEL:-$REPO_ROOT/models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf}"
B_MODEL="${B_MODEL:-$REPO_ROOT/models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf}"

for m in "$A_MODEL" "$B_MODEL"; do
    if [ ! -f "$m" ]; then
        echo "ERROR: model file missing: $m" >&2
        exit 2
    fi
done

case "$CONFIG" in
    coding-cap4)    PROMPT="$REPO_ROOT/benchmarks/prompts/phase-03-coding-lru.md";    CACHE_MB=4096 ;;
    reasoning-cap8) PROMPT="$REPO_ROOT/benchmarks/prompts/phase-03-reasoning-pumps.md"; CACHE_MB=8192 ;;
    uncached)       PROMPT="$REPO_ROOT/benchmarks/prompts/phase-03-coding-lru.md";    CACHE_MB=0 ;;
    *) echo "unknown CONFIG=$CONFIG" >&2; exit 2 ;;
esac

BASE="$OUTDIR/$CONFIG"
mkdir -p "$BASE/env"

# --- seeded within-bracket randomization (frozen 9G protocol) ---
ORDERS_JSON="$(python3 - "$SEED" "$N_BRACKETS" <<'PY'
import json, random, sys
seed, n = int(sys.argv[1]), int(sys.argv[2])
rng = random.Random(seed)
orders = [rng.sample(["A-before", "B", "A-after"], 3) for _ in range(n)]
print(json.dumps(orders))
PY
)"

bracket_order() {
    python3 - "$ORDERS_JSON" "$1" <<'PY'
import json, sys
orders = json.loads(sys.argv[1])
print(" ".join(orders[int(sys.argv[2]) - 1]))
PY
}

cat > "$OUTDIR/harness.json" <<EOF
{
  "harness": "phasek1_run_brackets.sh",
  "mode": "model-ab",
  "n_brackets": $N_BRACKETS,
  "n_tokens": $N,
  "seed": $SEED,
  "config": "$CONFIG",
  "prompt": "$PROMPT",
  "cache_mb": $CACHE_MB,
  "a_model": "$A_MODEL",
  "b_model": "$B_MODEL",
  "workers": $WORKERS,
  "ctx": "$CTX",
  "bracket_orders": $ORDERS_JSON,
  "started_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

env_snapshot() {
    local out="$1"
    python3 - "$out" <<'PY'
import datetime, json, subprocess, sys
out = sys.argv[1]
def sh(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception as e:
        return "<error: %s>" % e
snap = {
    "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "uptime": sh("uptime"),
    "loadavg": sh("sysctl -n vm.loadavg"),
    "memory_pressure": sh("memory_pressure -Q 2>/dev/null | head -20"),
    "vm_stat": sh("vm_stat | head -14"),
    "top_cpu": sh("ps -Aceo pid,pcpu,comm -r | head -8"),
    "cpu_thermal_level": sh("sysctl -n machdep.xcpm.cpu_thermal_level 2>/dev/null"),
    "pmset_therm": sh("pmset -g therm 2>/dev/null"),
    "live_server_health": sh("curl -s -m 3 http://127.0.0.1:18080/health 2>/dev/null"),
    "live_server_procs": sh("pgrep -fl 'runtime/live/bin/llama-server' || true"),
    "bench_procs": sh("pgrep -fl 'build-metal/bin/llama-cli' || true"),
}
with open(out, "w") as f:
    json.dump(snap, f, indent=2)
print("env snapshot -> %s" % out)
PY
}

run_one() {
    local dir="$1" model="$2"
    if [ -f "$dir/stats.csv" ]; then
        echo "  (cached) $dir"
        return 0
    fi
    local cap=""
    if [ "$CACHE_MB" != "0" ]; then cap="KIMI_EXPERT_CACHE_MB=$CACHE_MB"; fi
    echo "  running workers=$WORKERS cache=${CACHE_MB}MiB model=$(basename "$model") -> $dir"
    mkdir -p "$dir"
    : > "$dir/cpu.csv"
    env KIMI_MODEL="$model" KIMI_EXPERT_READ_WORKERS="$WORKERS" $cap \
        KIMI_PHASE9C_TRACE="$dir/p9c-layer.csv" \
        tools/phase04_run_streamed.sh "$dir" "$PROMPT" "$N" "$SEED" naive > "$dir.driver.log" 2>&1 &
    local runner=$!
    while kill -0 $runner 2>/dev/null; do
        local pid
        pid=$(pgrep -f "build-metal/bin/llama-cli" | head -1 || true)
        if [ -n "$pid" ]; then
            ps -p "$pid" -o %cpu= 2>/dev/null >> "$dir/cpu.csv" || true
        fi
        sleep 0.5
    done
    wait $runner || true
    if [ ! -f "$dir/stats.csv" ]; then
        echo "ERROR: run produced no stats.csv: $dir" >&2
        return 1
    fi
    # model provenance sidecar (the frozen runner's manifest lacks it)
    python3 - "$dir" "$model" "$CACHE_MB" <<'PY'
import json, os, sys
d, m, cache = sys.argv[1], sys.argv[2], int(sys.argv[3])
with open(os.path.join(d, "k1-model.json"), "w") as f:
    json.dump({
        "model": m,
        "model_basename": os.path.basename(m),
        "model_bytes": os.path.getsize(m),
        "cache_mb": cache,
        "workers": int(os.environ.get("KIMI_EXPERT_READ_WORKERS", "1")),
    }, f, indent=2)
PY
}

echo "=== Phase K1 harness: config=$CONFIG brackets=$N_BRACKETS n_tokens=$N seed=$SEED workers=$WORKERS ==="
echo "    A=$(basename "$A_MODEL")  B=$(basename "$B_MODEL")  cache=${CACHE_MB}MiB"

for i in $(seq 1 "$N_BRACKETS"); do
    echo "=== bracket $i/$N_BRACKETS (order: $(bracket_order $i)) ==="
    env_snapshot "$BASE/env/b${i}-before.json"
    for slot in $(bracket_order $i); do
        case "$slot" in
            A-before) run_one "$BASE/b${i}-A-before" "$A_MODEL" ;;
            A-after)  run_one "$BASE/b${i}-A-after"  "$A_MODEL" ;;
            B)        run_one "$BASE/b${i}-B"        "$B_MODEL" ;;
        esac
    done
    env_snapshot "$BASE/env/b${i}-after.json"
done

echo "harness complete: $BASE"
echo "analyze with: python3 tools/phasek1_analyze.py $OUTDIR"
