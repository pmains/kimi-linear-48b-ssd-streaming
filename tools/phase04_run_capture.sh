#!/bin/bash
# Phase 4 conventional-reference capture runner.
#
# Runs deterministic conventional CPU inference with the TCAT activation
# trace and MoE router trace enabled, writing both to OUTDIR. stdin is
# closed so llama-cli cannot drop into its interactive prompt.
#
# Usage:
#   tools/phase04_run_capture.sh OUTDIR PROMPT_FILE N_TOKENS [SEED]
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
"$CLI" -m "$MODEL" -ngl 0 -p "$PROMPT_TEXT" -n "$N" --temp 0 --seed "$SEED" \
    --no-display-prompt --no-conversation --single-turn < /dev/null > "$OUTDIR/run.log" 2>&1

cat > "$OUTDIR/manifest.json" <<EOF
{
  "prompt": "$PROMPT",
  "n_tokens": $N,
  "seed": $SEED,
  "backend": "cpu",
  "llama_commit": "$(git -C /Users/pmains/Code/openclaw/kimi/llama.cpp rev-parse HEAD)",
  "captured_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
echo "capture complete: $OUTDIR"
