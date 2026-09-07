#!/usr/bin/env bash
# E3E — corrected-Metal expert-cache capacity curve.
# KIMI_EXPERT_READ_WORKERS=4 fixed (E3C recommended point). Only
# KIMI_EXPERT_CACHE_MB differs across arms. Everything else identical and
# deterministic (fork a895f6826, MXFP4, -c 4096 -n 48 --temp 0 --seed 7,
# -ngl 999, KIMI_STREAM_METAL_STAGE=1, KIMI_STREAM_E2_DIRECT_PLACE=1, naive
# stream, zerocopy cache mode).
#
# Usage: tools/phase11_e3e_capacity.sh [MB ...]
#   default budgets: 1024 2048 3072 4096 6144 8192
#   the 4096 arm is re-run last to bracket run-to-run drift.
set -u
cd "$(dirname "$0")/.." || exit 1

OUT=benchmarks/results/phase-11/e3e
mkdir -p "$OUT"

BUDGETS=("$@")
if [ ${#BUDGETS[@]} -eq 0 ]; then
    BUDGETS=(1024 2048 3072 4096 6144 8192)
fi

run_arm() {
    local MB="$1"
    local tag="cap${MB}"
    echo "=== ARM $tag ($(date +%T)) ==="
    env KIMI_EXPERT_READ_WORKERS=4 \
        KIMI_STREAM_STATS_FILE="$OUT/$tag-stats.csv" \
        KIMI_STREAM_RETR_FILE="$OUT/$tag-retr.csv" \
        KIMI_STREAM_CACHE_LAYERS_FILE="$OUT/$tag-layers.csv" \
        KIMI_STREAM_MEM_FILE="$OUT/$tag-mem.csv" \
        KIMI_STREAM_EXPERTS=naive KIMI_EXPERT_CACHE_MODE=zerocopy \
        KIMI_EXPERT_CACHE_MB="$MB" KIMI_STREAM_METAL_STAGE=1 \
        KIMI_STREAM_E2_DIRECT_PLACE=1 \
        llama.cpp/build-metal/bin/llama-cli \
        -m models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
        -c 4096 -f benchmarks/prompts/phase-11-e5-engineering.md -n 48 \
        --temp 0 --seed 7 --no-display-prompt --no-conversation --single-turn -ngl 999 \
        < /dev/null > "$OUT/$tag.out" 2>&1
    echo "=== ARM $MB exit=$? ($(date +%T)) ==="
}

for MB in "${BUDGETS[@]}"; do
    run_arm "$MB"
done
# drift bracket: re-run the frozen baseline last
run_arm 4096
echo "ALL ARMS DONE"
