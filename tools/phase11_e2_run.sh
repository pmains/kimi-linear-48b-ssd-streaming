#!/bin/bash
# Phase 11 E2 A/B — re-run with the compatibility-accounting-fixed binary.
# A = E1 Metal gate only (frozen baseline); B = E1 gate + E2 direct placement.
# Configs: cache 0 / 256 / 4096 MiB (zerocopy mode; cache=0 disables zc).
# Usage: tools/phase11_e2_run.sh <results-dir-prefix>   (default: benchmarks/results/phase-k1)
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/benchmarks/results/phase-k1}"
BIN="$ROOT/llama.cpp/build-metal/bin/llama-cli"
MODEL="$ROOT/models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf"
PROMPT="$ROOT/benchmarks/prompts/phase-03-coding-lru.md"
SUF="${SUF:-r2}"   # suffix for fresh result dirs (preserve pre-fix evidence)
COMMON="KIMI_STREAM_METAL_STAGE=1 KIMI_STREAM_EXPERTS=naive KIMI_EXPERT_CACHE_MODE=zerocopy"
ARGS="-m $MODEL -ngl 999 -c 4096 -f $PROMPT -n 64 --temp 0 --seed 7 --no-display-prompt --no-conversation --single-turn"

run_one() { # $1=arm(A|B) $2=cache_mb $3=outdir
    local arm="$1" mb="$2" dir="$3"
    mkdir -p "$dir"
    local envs="$COMMON KIMI_EXPERT_CACHE_MB=$mb"
    [ "$arm" = B ] && envs="$envs KIMI_STREAM_E2_DIRECT_PLACE=1"
    env $envs \
        KIMI_STREAM_STATS_FILE="$dir/stats.csv" \
        KIMI_STREAM_RETR_FILE="$dir/retr.csv" \
        KIMI_STREAM_MEM_FILE="$dir/mem.csv" \
        KIMI_STREAM_CACHE_LAYERS_FILE="$dir/cache_layers.csv" \
        $BIN $ARGS < /dev/null > "$dir/run.log" 2>&1
    local rc=$?
    echo "EXIT=$rc e2-$arm-$mb" >> "$dir/run.log"
    echo "e2-$arm-$mb: EXIT=$rc"
    return $rc
}

for mb in 0 256 4096; do
    run_one A "$mb" "$OUT/e2-A-$mb-$SUF" || exit 1
    run_one B "$mb" "$OUT/e2-B-$mb-$SUF" || exit 1
done
echo "all e2 runs complete"
