#!/bin/bash
# Phase 9G bracket harness: repeated A -> B -> A paired brackets.
#
# Experimental unit (one bracket):
#     A_before -> B -> A_after
# contemporaneous (tight timing, same session, same config).
#   A = frozen workers=A_WORKERS path (Phase 8/9D/9F byte-identical control)
#   B = candidate (default B_WORKERS=4 pipelined repack: the 9G positive
#       control from the phase-09g design)
# Paired speedup per bracket:
#     S_i = tok/s(B_i) / mean(tok/s(A_before,i), tok/s(A_after,i))
#
# Every run keeps the full Phase 4/7/9 artifact set (stats.csv, retr.csv,
# moe.csv, mem.csv, cache_layers.csv, p9c-layer.csv, cpu.csv, run.log,
# manifest.json) so raw per-run data are retained for later re-analysis.
#
# Environment covariates are snapshotted before and after every bracket:
# memory pressure, vm_stat, load average, top CPU processes, CPU thermal
# level, pmset therm state, live llama-server health (the production server
# shares this machine; its load is a mandatory covariate per the 9G design).
#
# Usage:
#   tools/phase09g_run_brackets.sh [OUTDIR] [N_BRACKETS] [N_TOKENS]
# Env:
#   CONFIG      coding-cap4 (default) | reasoning-cap8 | uncached
#   A_WORKERS   1   (frozen control)
#   B_WORKERS   4   (candidate)
#   CTX         4096 (KV-cache pin, matches the 9D/9F ladder)
#
# Pilot usage (harness validation, 2026-08-28):
#   CONFIG=coding-cap4 tools/phase09g_run_brackets.sh \
#       benchmarks/results/phase-09g/pilot 4 128
set -euo pipefail

OUTDIR="${1:-benchmarks/results/phase-09g/pilot}"
N_BRACKETS="${2:-4}"
N="${3:-128}"
CONFIG="${CONFIG:-coding-cap4}"
A_WORKERS="${A_WORKERS:-1}"
B_WORKERS="${B_WORKERS:-4}"
SEED=1
export CTX="${CTX:-4096}"
export KIMI_EXPERT_CACHE_MODE=zerocopy
export KIMI_PHASE7_INSTR=1

case "$CONFIG" in
    coding-cap4)    PROMPT="benchmarks/prompts/phase-03-coding-lru.md";    CACHE_MB=4096 ;;
    reasoning-cap8) PROMPT="benchmarks/prompts/phase-03-reasoning-pumps.md"; CACHE_MB=8192 ;;
    uncached)       PROMPT="benchmarks/prompts/phase-03-coding-lru.md";    CACHE_MB=0 ;;
    *) echo "unknown CONFIG=$CONFIG" >&2; exit 2 ;;
esac

BASE="$OUTDIR/$CONFIG"
mkdir -p "$BASE/env"

cat > "$OUTDIR/harness.json" <<EOF
{
  "harness": "phase09g_run_brackets.sh",
  "pilot": true,
  "n_brackets": $N_BRACKETS,
  "n_tokens": $N,
  "seed": $SEED,
  "config": "$CONFIG",
  "prompt": "$PROMPT",
  "cache_mb": $CACHE_MB,
  "a_workers": $A_WORKERS,
  "b_workers": $B_WORKERS,
  "ctx": "$CTX",
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
    local dir="$1" workers="$2"
    if [ -f "$dir/stats.csv" ]; then
        echo "  (cached) $dir"
        return 0
    fi
    local cap=""
    if [ "$CACHE_MB" != "0" ]; then cap="KIMI_EXPERT_CACHE_MB=$CACHE_MB"; fi
    echo "  running workers=$workers cache=${CACHE_MB}MiB -> $dir"
    mkdir -p "$dir"
    : > "$dir/cpu.csv"
    env KIMI_EXPERT_READ_WORKERS="$workers" $cap \
        KIMI_PHASE9C_TRACE="$dir/p9c-layer.csv" \
        tools/phase04_run_streamed.sh "$dir" "$PROMPT" "$N" "$SEED" naive > "$dir.driver.log" 2>&1 &
    local runner=$!
    # CPU sampler: sample llama-cli %cpu every 0.5 s during the run
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
}

echo "=== Phase 9G harness pilot: config=$CONFIG brackets=$N_BRACKETS n_tokens=$N ==="
echo "    A=workers=$A_WORKERS (frozen)  B=workers=$B_WORKERS (candidate)  cache=${CACHE_MB}MiB"

for i in $(seq 1 "$N_BRACKETS"); do
    echo "=== bracket $i/$N_BRACKETS ==="
    env_snapshot "$BASE/env/b${i}-before.json"
    run_one "$BASE/b${i}-A-before" "$A_WORKERS"
    run_one "$BASE/b${i}-B"        "$B_WORKERS"
    run_one "$BASE/b${i}-A-after"  "$A_WORKERS"
    env_snapshot "$BASE/env/b${i}-after.json"
done

echo "harness complete: $BASE"
echo "analyze with: python3 tools/phase09g_analyze.py $OUTDIR"
