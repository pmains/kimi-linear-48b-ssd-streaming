#!/bin/bash
# Phase 4 streamed-capture runner.
#
# Runs deterministic streamed CPU inference (KIMI_STREAM_EXPERTS=naive or
# coalesced) with the TCAT activation trace, MoE router trace, retrieval
# log, and per-step stats enabled, writing everything to OUTDIR. stdin is
# closed so llama-cli cannot drop into its interactive prompt.
#
# Usage:
#   tools/phase04_run_streamed.sh OUTDIR PROMPT_FILE N_TOKENS [SEED] [MODE]
#   MODE: naive (default) | coalesced
set -euo pipefail

OUTDIR="${1:?outdir}"
PROMPT="${2:?prompt file}"
N="${3:?n tokens}"
SEED="${4:-1}"
MODE="${5:-naive}"

# Repo-relative defaults with env overrides (Phase 10 release path):
#   KIMI_MODEL  -> GGUF path (default: models/kimi-linear/...)
#   KIMI_CLI    -> llama-cli binary (default: runtime/release-9f/bin/llama-cli)
#   KIMI_LLAMA_GIT -> path to the llama.cpp checkout used for provenance
#   KIMI_REPO   -> repo root (default: resolved from this script's location)
REPO_ROOT="${KIMI_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
MODEL="${KIMI_MODEL:-$REPO_ROOT/models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf}"
CLI="${KIMI_CLI:-$REPO_ROOT/runtime/release-9f/bin/llama-cli}"
LLAMA_GIT="${KIMI_LLAMA_GIT:-$REPO_ROOT/llama.cpp}"

mkdir -p "$OUTDIR"

PROMPT_TEXT="$(cat "$PROMPT")"
# Optional KV-cache pin (Phase 4E convention): without --ctx-size, n_ctx
# defaults to n_ctx_train (1,048,576) and Kimi Linear's 7 attention layers
# allocate an 8 GB f16 KV cache — fine for oracle runs (both paths pay it
# identically) but a confound for memory-pressure-sensitive benchmarks like
# the Phase 6 ladder. Set CTX=4096 (or any >0) to pin; CTX=0/unset keeps
# the library default.
CTX_ARGS=()
if [ -n "${CTX:-}" ] && [ "$CTX" != "0" ]; then CTX_ARGS=(--ctx-size "$CTX"); fi
# Phase 7: optional full instrumentation (KIMI_PHASE7_INSTR=1). Adds the
# per-step memory log and the per-layer cache dump; the stats/retr files are
# always written (pre-existing Phase 4 behavior).
if [ "${KIMI_PHASE7_INSTR:-0}" = "1" ]; then
    export KIMI_STREAM_MEM_FILE="$OUTDIR/mem.csv"
    export KIMI_STREAM_CACHE_LAYERS_FILE="$OUTDIR/cache_layers.csv"
fi
KIMI_STREAM_EXPERTS="$MODE" \
KIMI_STREAM_RETR_FILE="$OUTDIR/retr.csv" \
KIMI_STREAM_STATS_FILE="$OUTDIR/stats.csv" \
KIMI_TRACE_ACT=1 \
KIMI_TRACE_ACT_FILE="$OUTDIR/act.bin" \
KIMI_TRACE_MOE=1 \
KIMI_TRACE_MOE_FILE="$OUTDIR/moe.csv" \
"$CLI" -m "$MODEL" -ngl 0 ${CTX_ARGS[@]+"${CTX_ARGS[@]}"} -p "$PROMPT_TEXT" -n "$N" --temp 0 --seed "$SEED" \
    --no-display-prompt --no-conversation --single-turn < /dev/null > "$OUTDIR/run.log" 2>&1

WORKTREE_DIRTY="false"
if [ -d "$LLAMA_GIT/.git" ] && [ -n "$(git -C "$LLAMA_GIT" status --porcelain 2>/dev/null)" ]; then
    WORKTREE_DIRTY="true"
fi
LLAMA_COMMIT="$(git -C "$LLAMA_GIT" rev-parse HEAD 2>/dev/null || echo unknown)"
cat > "$OUTDIR/manifest.json" <<EOF
{
  "prompt": "$PROMPT",
  "n_tokens": $N,
  "seed": $SEED,
  "backend": "cpu",
  "stream_mode": "$MODE",
  "llama_commit": "$LLAMA_COMMIT",
  "llama_worktree_dirty": $WORKTREE_DIRTY,
  "phase7_instrumentation": ${KIMI_PHASE7_INSTR:-0},
  "read_workers": ${KIMI_EXPERT_READ_WORKERS:-1},
  "captured_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
echo "capture complete: $OUTDIR"
