#!/bin/bash
# Phase 11 controlled CPU substitution — arm C.
#
# On the frozen Metal path (same envs as arm B), route ONLY the layer-0
# Q/K/V projection mul_mat through the CPU backend
# (KIMI_STREAM_LOCALIZE_SUB_QKV_CPU=1, default off; the scheduler copies
# the result back to Metal for the downstream conv1d). Same 64-tok
# deterministic config as arms A/B; same trace captures.
#
# Usage: tools/phase11_substitute_run.sh [OUTDIR]
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/benchmarks/results/phase-k1/localize/sub}"
BIN="$ROOT/llama.cpp/build-metal/bin/llama-cli"
MODEL="$ROOT/models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf"
PROMPT="$ROOT/benchmarks/prompts/phase-03-coding-lru.md"
mkdir -p "$OUT"
env KIMI_STREAM_EXPERTS=naive KIMI_EXPERT_CACHE_MODE=zerocopy KIMI_EXPERT_CACHE_MB=4096 \
    KIMI_TRACE_ACT=1 KIMI_TRACE_ACT_FILE="$OUT/act.bin" \
    KIMI_STREAM_METAL_STAGE=1 KIMI_STREAM_E2_DIRECT_PLACE=1 \
    KIMI_STREAM_LOCALIZE_SUB_QKV_CPU=1 \
    $BIN -m "$MODEL" -c 4096 -f "$PROMPT" -n 64 --temp 0 --seed 7 \
    --no-display-prompt --no-conversation --single-turn -ngl 999 \
    < /dev/null > "$OUT/run.log" 2>&1
rc=$?
echo "EXIT=$rc localize-sub" >> "$OUT/run.log"
echo "localize-sub: EXIT=$rc"
exit $rc
