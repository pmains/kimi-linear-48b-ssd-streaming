#!/bin/bash
# Phase 11 E2 zerocopy expert-slot integrity check — streamed Metal run
# with KIMI_DX_ZC_VERIFY=1 (env-gated slot-integrity instrumentation added
# to the fork; inert when unset).
#
# Verifies, per expert consumption, that the persistent zerocopy slot the
# graph will consume (slot_ids) contains the exact bytes belonging to the
# requested layer/tensor/expert:
#   requested expert E -> GGUF offset -> placed bytes (FNV-1a64 recorded at
#   placement) -> slot S -> slot_ids mapping -> current slot bytes checksum
# plus a fresh-pread cross-check per (layer, kind, expert).
#
# Same deterministic 64-tok config as the localize runs (naive stream,
# zerocopy cache, Metal stage + E2 direct place, -ngl 999).
#
# Usage: tools/phase11_zc_slotcheck_run.sh [CACHE_MB] [OUTDIR]
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE_MB="${1:-4096}"
OUT="${2:-$ROOT/benchmarks/results/phase-11/zc-slotcheck/verify-$CACHE_MB}"
BIN="$ROOT/llama.cpp/build-metal/bin/llama-cli"
MODEL="$ROOT/models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf"
PROMPT="$ROOT/benchmarks/prompts/phase-03-coding-lru.md"
mkdir -p "$OUT"
env KIMI_STREAM_EXPERTS=naive KIMI_EXPERT_CACHE_MODE=zerocopy KIMI_EXPERT_CACHE_MB="$CACHE_MB" \
    KIMI_STREAM_METAL_STAGE=1 KIMI_STREAM_E2_DIRECT_PLACE=1 \
    KIMI_DX_ZC_VERIFY=1 \
    $BIN -m "$MODEL" -c 4096 -f "$PROMPT" -n 64 --temp 0 --seed 7 \
    --no-display-prompt --no-conversation --single-turn -ngl 999 \
    < /dev/null > "$OUT/run.log" 2>&1
rc=$?
echo "EXIT=$rc zc-slotcheck cache=${CACHE_MB}MiB" >> "$OUT/run.log"
echo "zc-slotcheck: EXIT=$rc (cache=${CACHE_MB}MiB)"
echo "--- [zc-verify] lines ---"
grep -c "\[zc-verify\]" "$OUT/run.log" || true
echo "--- failures ---"
grep "\[zc-verify\] FAIL" "$OUT/run.log" | head -5 || true
echo "--- summary ---"
grep "\[zc-verify\] SUMMARY" "$OUT/run.log" || true
exit $rc
