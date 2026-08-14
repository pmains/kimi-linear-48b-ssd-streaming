#!/bin/bash
# Phase 7 ladder: instrumented subset of the Phase 6B ladder protocol.
#
# Same protocol as tools/phase06_run_ladder.sh (ctx 4096 pin, min-of-2,
# reversed order, uncached in-session control) but with KIMI_PHASE7_INSTR=1
# (mem.csv + cache_layers.csv per run) and a configurable capacity subset.
# Zero-copy mode (the Phase 6B baseline) is the default; override with
# KIMI_EXPERT_CACHE_MODE=placement for the Phase 6 reference path.
# Per-rung stats.csv now carries the Phase 7 hit-class/repack/placement
# columns; run tools/phase07_summarize.py --ladder ROOT for the merged table.
#
# Usage:
#   tools/phase07_run_ladder.sh OUTDIR_ROOT [CAPS_GB...]
#     default caps: 1 4 8 (the practical range from Phase 6B)
set -euo pipefail

export CTX=4096
export KIMI_EXPERT_CACHE_MODE="${KIMI_EXPERT_CACHE_MODE:-zerocopy}"

ROOT="${1:?outdir root}"
shift || true
CAPS_GB=("$@")
if [ ${#CAPS_GB[@]} -eq 0 ]; then CAPS_GB=(1 4 8); fi
N=64
SEED=1
PROMPT="benchmarks/prompts/phase-04-ref.md"

mkdir -p "$ROOT"

run_cap() {
    local gb="$1"
    local mib
    mib=$(python3 -c "print(int(round($gb * 1024)))")
    for suffix in a b; do
        local dir="$ROOT/cap-${gb}-${suffix}"
        if [ ! -f "$dir/stats.csv" ]; then
            KIMI_PHASE7_INSTR=1 KIMI_EXPERT_CACHE_MB="$mib" \
                tools/phase04_run_streamed.sh "$dir" "$PROMPT" "$N" "$SEED" naive > /dev/null 2>&1
        fi
    done
    echo "  cap ${gb}GB done"
}

BASE="$ROOT/uncached"
if [ ! -f "$BASE/stats.csv" ]; then
    KIMI_PHASE7_INSTR=1 tools/phase04_run_streamed.sh "$BASE" "$PROMPT" "$N" "$SEED" naive > /dev/null 2>&1
fi
echo "  uncached done"

# reversed order to decorrelate thermal drift from capacity
for ((i = ${#CAPS_GB[@]} - 1; i >= 0; i--)); do
    run_cap "${CAPS_GB[i]}"
done
echo "ladder complete: $ROOT"
echo "merge with: python3 tools/phase07_summarize.py --ladder $ROOT"
