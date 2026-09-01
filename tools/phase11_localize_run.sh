#!/bin/bash
# Phase 11 Metal numerical localization — first bounded experiment.
#
# Instrument the computation feeding layer-1 attn_out (KDA attention
# boundaries, env-gated captures added in the fork) and compare CPU vs
# Metal at each boundary to find the first causally meaningful divergence.
#
# CPU  = streamed MXFP4, -ngl 0 (frozen CPU reference path)
# Metal = streamed MXFP4, -ngl 999, KIMI_STREAM_METAL_STAGE=1 +
#         KIMI_STREAM_E2_DIRECT_PLACE=1 (the frozen E2 Metal path used in E4)
#
# Same 64-tok deterministic config as the E1b trace runs.
# Usage: tools/phase11_localize_run.sh [OUTDIR_PREFIX]
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/benchmarks/results/phase-k1/localize}"
BIN="$ROOT/llama.cpp/build-metal/bin/llama-cli"
MODEL="$ROOT/models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf"
PROMPT="$ROOT/benchmarks/prompts/phase-03-coding-lru.md"
COMMON="KIMI_STREAM_EXPERTS=naive KIMI_EXPERT_CACHE_MODE=zerocopy KIMI_EXPERT_CACHE_MB=4096 KIMI_TRACE_ACT=1"
ARGS="-m $MODEL -c 4096 -f $PROMPT -n 64 --temp 0 --seed 7 --no-display-prompt --no-conversation --single-turn"

run_one() { # $1=label $2=ngl $3=extra_envs $4=outdir
    local label="$1" ngl="$2" extra="$3" dir="$4"
    mkdir -p "$dir"
    env $COMMON $extra KIMI_TRACE_ACT_FILE="$dir/act.bin" \
        $BIN $ARGS -ngl "$ngl" < /dev/null > "$dir/run.log" 2>&1
    local rc=$?
    echo "EXIT=$rc $label" >> "$dir/run.log"
    echo "$label: EXIT=$rc"
    return $rc
}

run_one "localize-cpu"   0   ""                                "$OUT/cpu"
run_one "localize-metal" 999 "KIMI_STREAM_METAL_STAGE=1 KIMI_STREAM_E2_DIRECT_PLACE=1" "$OUT/metal"
echo "localization trace runs complete"
