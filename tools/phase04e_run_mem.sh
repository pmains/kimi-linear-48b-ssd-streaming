#!/bin/bash
# Phase 4E memory-capture runner: streamed run with live-allocation log.
#
# Usage:
#   tools/phase04e_run_mem.sh OUTDIR PROMPT_FILE N_TOKENS [SEED] [MODE]
#   MODE: naive (default) | coalesced
#
# Emits:
#   OUTDIR/mem.csv      per-step live allocations (phys_footprint, resident,
#                       malloc in-use/max, scheduler pool bytes) — start+end rows
#   OUTDIR/stats.csv    per-step streamer timing
#   OUTDIR/retr.csv     retrieval log
#   OUTDIR/act.bin, moe.csv  oracle traces (for the phase05 comparator)
#   OUTDIR/run.log/.err, mem.txt (ru_maxrss reference)
#
# Always --no-mmap: mmap mode is polluted by the loader's MADV_WILLNEED file
# prefetch, which attributes shared file pages to process RSS.
set -euo pipefail

OUTDIR="${1:?outdir}"
PROMPT="${2:?prompt file}"
N="${3:?n tokens}"
SEED="${4:-1}"
MODE="${5:-naive}"
# Pin the KV cache: without --ctx-size, llama.cpp defaults n_ctx to
# n_ctx_train (1,048,576) and allocates a ~8 GB KV cache (7 attention
# layers x 1M cells x f16), which is shared by conventional and streamed
# paths and would confound the 4E residency comparison. CTX=0 restores
# the library default.
CTX="${CTX:-4096}"

MODEL="/Users/pmains/Code/openclaw/kimi/models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf"
CLI="/Users/pmains/Code/openclaw/kimi/llama.cpp/build-metal/bin/llama-cli"

mkdir -p "$OUTDIR"

CTX_ARGS=()
if [ "$CTX" != "0" ]; then CTX_ARGS=(--ctx-size "$CTX"); fi

PROMPT_TEXT="$(cat "$PROMPT")"
KIMI_STREAM_EXPERTS="$MODE" \
KIMI_STREAM_RETR_FILE="$OUTDIR/retr.csv" \
KIMI_STREAM_STATS_FILE="$OUTDIR/stats.csv" \
KIMI_STREAM_MEM_FILE="$OUTDIR/mem.csv" \
KIMI_TRACE_ACT=1 \
KIMI_TRACE_ACT_FILE="$OUTDIR/act.bin" \
KIMI_TRACE_MOE=1 \
KIMI_TRACE_MOE_FILE="$OUTDIR/moe.csv" \
/usr/bin/time -l "$CLI" -m "$MODEL" -ngl 0 --no-mmap "${CTX_ARGS[@]}" -p "$PROMPT_TEXT" -n "$N" --temp 0 --seed "$SEED" \
    --no-display-prompt --no-conversation --single-turn < /dev/null > "$OUTDIR/run.log" 2> "$OUTDIR/run.err" || true

if [ -f "$OUTDIR/run.err" ]; then
    grep -v "maximum resident set size\|page reclaims\|voluntary context switches\|involuntary context switches\|file system inputs\|file system outputs\|socket messages sent\|socket messages received\|signals delivered\|page size\|mean shared text size\|mean unshared data size\|mean stack size\|mean unshared stack size\|% of cpu" "$OUTDIR/run.err" >> "$OUTDIR/run.log" || true
    grep "maximum resident set size" "$OUTDIR/run.err" > "$OUTDIR/mem.txt" || true
fi

WORKTREE_DIRTY="false"
if [ -n "$(git -C /Users/pmains/Code/openclaw/kimi/llama.cpp status --porcelain)" ]; then
    WORKTREE_DIRTY="true"
fi

# environment + commit record (reproducibility)
{
    echo "# phase-04e mem capture"
    echo "# date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# host: $(sysctl -n hw.model 2>/dev/null || true) $(sysctl -n machdep.cpu.brand_string 2>/dev/null || true)"
    echo "# ram: $(($(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1024 / 1024 / 1024)) GiB unified"
    echo "# os: $(sw_vers -productName 2>/dev/null || true) $(sw_vers -productVersion 2>/dev/null || true)"
    echo "# llama.cpp commit: $(git -C /Users/pmains/Code/openclaw/kimi/llama.cpp rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "# llama.cpp worktree dirty: $WORKTREE_DIRTY"
    echo "# model: $MODEL"
    echo "# cli: $CLI"
    echo "# mode: $MODE  seed: $SEED  n_tokens: $N"
    echo "# flags: -ngl 0 --no-mmap --ctx-size $CTX"
} > "$OUTDIR/env.txt"

echo "capture done: $OUTDIR"
