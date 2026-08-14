#!/bin/bash
# Phase 8 driver: realistic coding workloads through the Phase 7B ladder
# protocol. Runs each workload sequentially, logging to a driver log so
# the whole session is auditable from one file.
#
# Workloads (prompt, n_tokens):
#   W1 coding-lru    phase-03-coding-lru.md      128   (Rust LRU cache impl)
#   W2 reasoning     phase-03-reasoning-pumps.md 128   (multi-step reasoning)
#
# Same protocol as Phase 7B: caps 1 2 4 6 8 10 12, 3 reps, interleaved
# uncached controls, rotated cap order, 15s settle, zerocopy mode.
set -euo pipefail

ROOT="benchmarks/results/phase-08"
mkdir -p "$ROOT"
LOG="$ROOT/driver.log"
: > "$LOG"

run_workload() {
    local label="$1" prompt="$2" ntokens="$3"
    echo "==== workload $label ($(date +%H:%M:%S)) ====" | tee -a "$LOG"
    echo "prompt: $prompt  n_tokens: $ntokens" | tee -a "$LOG"
    tools/phase07b_run_baseline.sh "$ROOT/$label" \
        --prompt "$prompt" --n-tokens "$ntokens" --reps 3 1 2 4 6 8 10 12 \
        >> "$LOG" 2>&1
    echo "---- workload $label done ($(date +%H:%M:%S)) ----" | tee -a "$LOG"
    echo "summarize: python3 tools/phase07b_summarize.py --root $ROOT/$label" | tee -a "$LOG"
    python3 tools/phase07b_summarize.py --root "$ROOT/$label" >> "$LOG" 2>&1 || \
        echo "WARN: summarize failed for $label" | tee -a "$LOG"
}

echo "phase-08 driver start: $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"
uptime >> "$LOG" 2>&1
run_workload coding-lru    "benchmarks/prompts/phase-03-coding-lru.md"       128
run_workload reasoning     "benchmarks/prompts/phase-03-reasoning-pumps.md"  128
echo "phase-08 driver complete: $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"
uptime >> "$LOG" 2>&1
echo "ALL DONE"
