#!/bin/bash
# Phase 7B baseline runner: revised Phase 7 performance ladder under
# controlled idle-machine conditions. Performance-baseline validation
# only -- no oracle, no correctness runs, no code changes.
#
# Protocol differences vs tools/phase07_run_ladder.sh (min-of-2, single
# uncached control at the start):
#   - REP rounds (default 3) per capped rung, each round led by an
#     uncached control (controls interleaved, not front-loaded);
#   - cap order rotated per round (ascending / descending / seeded
#     shuffle) to decorrelate thermal drift from capacity;
#   - optional settle delay between runs (fanless Air cooldown);
#   - 10/12 GB rungs included by default to confirm the memory-pressure
#     cliff (Phase 6B/7 finding: route+expert compute degrades ~10x at
#     >= 10 GB on this 24 GB machine);
#   - act.bin discarded per run (reproducible via
#     tools/phase04_run_streamed.sh; Phase 7 retained it only for the
#     oracle pair). All other Phase 7 telemetry is preserved: stats.csv
#     (30 cols), mem.csv, cache_layers.csv, retr.csv, moe.csv,
#     manifest.json, run.log.
#
# Usage:
#   tools/phase07b_run_baseline.sh OUTDIR_ROOT [CAPS_GB...] [--reps N] \
#       [--prompt FILE] [--n-tokens N]
#     default caps: 1 2 4 6 8 10 12  (uncached control always included)
#     default reps: 3
#     default prompt: benchmarks/prompts/phase-04-ref.md (Phase 7B ref)
#     default n_tokens: 64
#   Phase 8: pass a realistic coding workload, e.g.
#     --prompt benchmarks/prompts/phase-03-coding-lru.md --n-tokens 128
# Env:
#   KIMI_EXPERT_CACHE_MODE=zerocopy (default) | placement
#   PHASE07B_SETTLE_S=<seconds>      (default 15; 0 disables)
set -euo pipefail

export CTX=4096
export KIMI_EXPERT_CACHE_MODE="${KIMI_EXPERT_CACHE_MODE:-zerocopy}"

ROOT="${1:?outdir root}"
shift || true
REPS=3
CAPS_GB=()
N=64
SEED=1
PROMPT="benchmarks/prompts/phase-04-ref.md"
while [ $# -gt 0 ]; do
    case "$1" in
        --reps) REPS="${2:?--reps needs a value}"; shift 2 ;;
        --prompt) PROMPT="${2:?--prompt needs a value}"; shift 2 ;;
        --n-tokens) N="${2:?--n-tokens needs a value}"; shift 2 ;;
        *) CAPS_GB+=("$1"); shift ;;
    esac
done
if [ ${#CAPS_GB[@]} -eq 0 ]; then CAPS_GB=(1 2 4 6 8 10 12); fi
SETTLE_S="${PHASE07B_SETTLE_S:-15}"
LOG="$ROOT/baseline-run.log"
mkdir -p "$ROOT"

echo "phase-07b baseline: $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"
echo "caps: ${CAPS_GB[*]}  reps: $REPS  mode: $KIMI_EXPERT_CACHE_MODE  settle: ${SETTLE_S}s" | tee -a "$LOG"
echo "prompt: $PROMPT  n_tokens: $N  seed: $SEED  ctx: $CTX" | tee -a "$LOG"
echo "idle check at start:" >> "$LOG"
uptime >> "$LOG" 2>&1
ps -A -o %cpu,comm | sort -rn | head -5 >> "$LOG" 2>&1 || true

# ambient-load sampler: top-3 CPU procs + loadavg every 20s for the whole
# run, so the report can show the load envelope (and catch any video call
# that starts mid-run).
SAMPLE_LOG="$ROOT/load-samples.log"
(
    i=0
    while [ $i -lt 300 ]; do
        echo "$(date +%H:%M:%S) load=$(sysctl -n vm.loadavg | tr -d '{}')  $(ps -A -o %cpu,comm | sort -rn | head -3 | tr '\n' ';')"
        sleep 20
        i=$((i + 1))
    done
) > "$SAMPLE_LOG" 2>&1 &
SAMPLER_PID=$!
trap 'kill $SAMPLER_PID 2>/dev/null || true' EXIT

run_one() {
    local label="$1"
    local dir="$ROOT/$label"
    if [ -f "$dir/stats.csv" ]; then
        echo "  [skip] $label (already captured)" | tee -a "$LOG"
        return
    fi
    echo "  [run]  $label  $(date +%H:%M:%S)" | tee -a "$LOG"
    if [ -n "${KIMI_EXPERT_CACHE_MB:-}" ]; then
        KIMI_PHASE7_INSTR=1 KIMI_EXPERT_CACHE_MB="$KIMI_EXPERT_CACHE_MB" \
            tools/phase04_run_streamed.sh "$dir" "$PROMPT" "$N" "$SEED" naive > /dev/null 2>&1
    else
        KIMI_PHASE7_INSTR=1 \
            tools/phase04_run_streamed.sh "$dir" "$PROMPT" "$N" "$SEED" naive > /dev/null 2>&1
    fi
    rm -f "$dir/act.bin"   # reproducible; see header comment
    echo "  [done] $label  $(date +%H:%M:%S)" | tee -a "$LOG"
    if [ "$SETTLE_S" -gt 0 ]; then sleep "$SETTLE_S"; fi
}

# per-round cap order: ascending / descending / seeded shuffle (bash 3.2-safe)
order_for() {
    local r="$1" caps="$2"
    case "$r" in
        1) echo "$caps" ;;
        2) python3 -c 'import sys; print(" ".join(reversed(sys.argv[1].split())))' "$caps" ;;
        *) python3 -c 'import random,sys; c=sys.argv[1].split(); random.Random(7).shuffle(c); print(" ".join(c))' "$caps" ;;
    esac
}

for ((r = 1; r <= REPS; r++)); do
    echo "== round $r ==" | tee -a "$LOG"
    run_one "uncached-r$r"
    local_ord=$(order_for "$r" "${CAPS_GB[*]}")
    echo "   cap order: $local_ord" | tee -a "$LOG"
    for gb in $local_ord; do
        mib=$(python3 -c "print(int(round($gb * 1024)))")
        KIMI_EXPERT_CACHE_MB="$mib" run_one "cap-${gb}-r${r}"
    done
done

echo "baseline complete: $ROOT" | tee -a "$LOG"
echo "idle check at end:" >> "$LOG"
uptime >> "$LOG" 2>&1
echo "summarize with: python3 tools/phase07b_summarize.py --root $ROOT"
