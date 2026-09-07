#!/usr/bin/env bash
# Step 12 - decode-throughput workload battery driver (retained 2026-09-06).
# Runs representative Step 9-11 tasks through the REAL OpenClaw agent path
# (openclaw agent --agent kimi --model llama-server/kimi-linear-48b), one
# fresh session per turn, capturing llama-server + gateway log windows,
# /slots, client timing and the final CLI doc via the retained instrumented
# runner (tools/service_step10a_turn.py). Resumable: existing
# <label>.client.json skips that turn.
#
#   bash tools/service_step12_leg.sh [label ...]
#
# Workload labels (default = all):
#   short-p3   Step 9 P3: single-number answer (minimal decode)
#   res-r1     Step 11 R1: read AGENTS.md + TOOLS.md, 3-bullet summary
#   eng-c1s12  Step 11 C1 (step-12 paths): write text_stats.py + py_compile
#   long-g1    Step 11 G1: read SERVICE-ROADMAP.md, 3-bullet summary
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_ROOT="${STEP12_OUT:-$ROOT/benchmarks/results/service-step-12}"
RUNNER="$ROOT/tools/service_step10a_turn.py"
PROMPTS="$ROOT/benchmarks/results/service-step-12/prompts"
LLAMA_LOG=/tmp/kimi-llama-server.launchd.log
GATEWAY_LOG="$HOME/Library/Logs/openclaw/gateway.log"
TIMEOUT=900
mkdir -p "$OUT_ROOT"

run_one() {
  local label="$1" prompt="$2"
  local outdir="$OUT_ROOT/$label"
  mkdir -p "$outdir"
  if [ -f "$outdir/$label-r1.client.json" ]; then
    echo "skip $label (exists)"
    return 0
  fi
  local key="agent:kimi:step12-$label-r1"
  echo "=== $label start $(date '+%F %T %z') ==="
  python3 "$RUNNER" "$outdir" "$label-r1" "$prompt" "$key" \
    --agent kimi --timeout "$TIMEOUT" \
    --llama-log "$LLAMA_LOG" --gateway-log "$GATEWAY_LOG"
  echo "=== $label end rc=$? $(date '+%F %T %z') ==="
}

if [ "$#" -gt 0 ]; then
  for l in "$@"; do
    case "$l" in
      short-p3)  run_one short-p3  "$PROMPTS/P3.md" ;;
      res-r1)    run_one res-r1    "$PROMPTS/R1.md" ;;
      eng-c1s12) run_one eng-c1s12 "$PROMPTS/C1-s12.md" ;;
      long-g1)   run_one long-g1   "$PROMPTS/G1.md" ;;
      *) echo "unknown label $l" ;;
    esac
  done
else
  run_one short-p3  "$PROMPTS/P3.md"
  run_one res-r1    "$PROMPTS/R1.md"
  run_one eng-c1s12 "$PROMPTS/C1-s12.md"
  run_one long-g1   "$PROMPTS/G1.md"
fi
echo "battery complete $(date '+%F %T %z')"
