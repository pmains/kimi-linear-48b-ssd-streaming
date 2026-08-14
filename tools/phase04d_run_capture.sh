#!/bin/bash
# Phase 4D conventional-reference capture runner (mirrors
# tools/phase04_run_capture.sh + peak-RSS measurement via /usr/bin/time -l).
#
# Usage:
#   tools/phase04d_run_capture.sh OUTDIR PROMPT_FILE N_TOKENS [SEED]
set -euo pipefail

OUTDIR="${1:?outdir}"
PROMPT="${2:?prompt file}"
N="${3:?n tokens}"
SEED="${4:-1}"

MODEL="/Users/pmains/Code/openclaw/kimi/models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf"
CLI="/Users/pmains/Code/openclaw/kimi/llama.cpp/build-metal/bin/llama-cli"

mkdir -p "$OUTDIR"

PROMPT_TEXT="$(cat "$PROMPT")"
KIMI_TRACE_ACT=1 \
KIMI_TRACE_ACT_FILE="$OUTDIR/act.bin" \
KIMI_TRACE_MOE=1 \
KIMI_TRACE_MOE_FILE="$OUTDIR/moe.csv" \
/usr/bin/time -l "$CLI" -m "$MODEL" -ngl 0 -p "$PROMPT_TEXT" -n "$N" --temp 0 --seed "$SEED" \
    --no-display-prompt --no-conversation --single-turn < /dev/null > "$OUTDIR/run.log" 2> "$OUTDIR/run.err"

# /usr/bin/time -l report goes to stderr; llama-cli logs also go to stderr.
# Keep the time report separate for parsing and append the CLI log to run.log
# for parity with the phase-04 runners.
if [ -f "$OUTDIR/run.err" ]; then
    grep -v "maximum resident set size\|page reclaims\|voluntary context switches\|involuntary context switches\|file system inputs\|file system outputs\|socket messages sent\|socket messages received\|signals delivered\|page size\|mean shared text size\|mean unshared data size\|mean stack size\|mean unshared stack size\|% of cpu" "$OUTDIR/run.err" >> "$OUTDIR/run.log" || true
    grep "maximum resident set size" "$OUTDIR/run.err" > "$OUTDIR/mem.txt" || true
fi

WORKTREE_DIRTY="false"
if [ -n "$(git -C /Users/pmains/Code/openclaw/kimi/llama.cpp status --porcelain)" ]; then
    WORKTREE_DIRTY="true"
fi
cat > "$OUTDIR/manifest.json" <<EOF
{
  "prompt": "$PROMPT",
  "n_tokens": $N,
  "seed": $SEED,
  "backend": "cpu",
  "llama_commit": "$(git -C /Users/pmains/Code/openclaw/kimi/llama.cpp rev-parse HEAD)",
  "llama_worktree_dirty": $WORKTREE_DIRTY,
  "captured_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
echo "capture complete: $OUTDIR"
