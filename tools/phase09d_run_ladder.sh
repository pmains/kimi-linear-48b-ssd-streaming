#!/bin/bash
# Phase 9D ladder: bounded parallel expert-read worker-count sweep.
#
# Protocol (contemporaneous baselines): for each configuration, each worker
# count W in {2,4,8} is bracketed by fresh workers=1 runs (1, W, 1), so
# machine/environmental drift is measured per-W, not across the session.
# Workers=1 is the frozen Phase 8 sequential path (byte-identical baseline).
#
# Configurations (from the Phase 9C ladder):
#   coding-cap4    coding prompt @ 4 GB zero-copy cache
#   reasoning-cap8 reasoning prompt @ 8 GB zero-copy cache
#   uncached       coding prompt, no cache (pure-I/O worst case)
#
# Usage: tools/phase09d_run_ladder.sh [OUTDIR_ROOT]
# Env: N_TOKENS (default 128), N_REPS (default 1 bracketed pass)
set -euo pipefail

ROOT="${1:-benchmarks/results/phase-09d/ladder}"
N="${N_TOKENS:-128}"
SEED=1
export CTX=4096
export KIMI_EXPERT_CACHE_MODE=zerocopy
export KIMI_PHASE7_INSTR=1

CODING_PROMPT="benchmarks/prompts/phase-03-coding-lru.md"
REASON_PROMPT="benchmarks/prompts/phase-03-reasoning-pumps.md"

mkdir -p "$ROOT"

run_one() {
    local dir="$1"; shift
    local prompt="$1"; shift
    local cache_mb="$1"; shift
    local workers="$1"; shift
    if [ -f "$dir/stats.csv" ]; then
        echo "  (cached) $dir"
        return 0
    fi
    local cap=""
    if [ "$cache_mb" != "0" ]; then cap="KIMI_EXPERT_CACHE_MB=$cache_mb"; fi
    echo "  running workers=$workers cache=${cache_mb}MiB -> $dir"
    mkdir -p "$dir"
    : > "$dir/cpu.csv"
    env KIMI_EXPERT_READ_WORKERS="$workers" $cap \
        KIMI_PHASE9C_TRACE="$dir/p9c-layer.csv" \
        tools/phase04_run_streamed.sh "$dir" "$prompt" "$N" "$SEED" naive > "$dir.driver.log" 2>&1 &
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
}

sweep_config() {
    local name="$1"; local prompt="$2"; local cache_mb="$3"
    echo "=== config $name (cache ${cache_mb}MiB) ==="
    local base="$ROOT/$name"
    mkdir -p "$base"
    # baseline + W bracketing: 1, W, 1 for each W
    for W in 2 4 8; do
        run_one "$base/w1-before-W$W" "$prompt" "$cache_mb" 1
        run_one "$base/w$W"            "$prompt" "$cache_mb" "$W"
        run_one "$base/w1-after-W$W"   "$prompt" "$cache_mb" 1
    done
}

sweep_config "coding-cap4"    "$CODING_PROMPT" 4096
sweep_config "reasoning-cap8" "$REASON_PROMPT" 8192
sweep_config "uncached"       "$CODING_PROMPT" 0

echo "ladder complete: $ROOT"
echo "analyze with: python3 tools/phase09d_analyze.py $ROOT"
