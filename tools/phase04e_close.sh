#!/bin/bash
# Phase 4E close runner: two-pass in-process memory probe + attribution.
#
# Usage:
#   tools/phase04e_close.sh OUTDIR [N_DECODE] [HOLD_MS] [STACKLOG]
#     N_DECODE  decode steps per pass (default 64)
#     HOLD_MS   sleep at pass-2 mid-decode for external attach (default 0)
#     STACKLOG  "1" => run with MallocStackLogging=1 for malloc_history
#
# Emits:
#   OUTDIR/probe_mem.csv      harness rows (pass,step,phase,phys,resident,
#                             compressed,malloc default/other,n_zones)
#   OUTDIR/exec_mem.csv       llama.cpp executor rows (cross-check; only when
#                             KIMI_STREAM_MEM_FILE set — always here)
#   OUTDIR/stats.csv, retr.csv
#   OUTDIR/vmmap-summary.txt, vmmap.txt, malloc_hist/*.txt   (when HOLD_MS>0)
#   OUTDIR/env.txt, run.log, run.err
set -euo pipefail

OUTDIR="${1:?outdir}"
N="${2:-64}"
HOLD="${3:-0}"
STACKLOG="${4:-0}"

MODEL="/Users/pmains/Code/openclaw/kimi/models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf"
PROMPT="/Users/pmains/Code/openclaw/kimi/benchmarks/prompts/phase-04-ref.md"
PROBE="/Users/pmains/Code/openclaw/kimi/llama.cpp/build-metal/bin/phase04e_probe"

mkdir -p "$OUTDIR/malloc_hist"

if [ "$STACKLOG" = "1" ]; then
    export MallocStackLogging=1
fi
export KIMI_STREAM_EXPERTS=naive
export KIMI_STREAM_RETR_FILE="$OUTDIR/retr.csv"
export KIMI_STREAM_MEM_FILE="$OUTDIR/exec_mem.csv"
export KIMI_STREAM_STATS_FILE="$OUTDIR/stats.csv"
export KIMI_PROBE_HOLD_MS="$HOLD"

"$PROBE" "$MODEL" "$PROMPT" "$N" "$OUTDIR" > "$OUTDIR/run.log" 2> "$OUTDIR/run.err" &
PID=$!

if [ "$HOLD" != "0" ]; then
    # wait for the hold marker, then attach
    for i in $(seq 1 600); do
        if grep -q "HOLD" "$OUTDIR/run.err" 2>/dev/null; then break; fi
        if ! kill -0 "$PID" 2>/dev/null; then break; fi
        sleep 0.5
    done
    sleep 1
    if kill -0 "$PID" 2>/dev/null; then
        echo "== vmmap -summary (pid $PID)" > "$OUTDIR/vmmap-summary.txt"
        vmmap -summary "$PID" >> "$OUTDIR/vmmap-summary.txt" 2>&1 || true
        vmmap "$PID" > "$OUTDIR/vmmap.txt" 2>&1 || true
        # targeted malloc_history for the largest live MALLOC_LARGE regions
        grep "MALLOC_LARGE " "$OUTDIR/vmmap.txt" 2>/dev/null | grep -v "(empty)" | head -12 \
            | awk '{print $1, $2}' | while read -r ADDR SZ; do
                NAME="malloc_hist/$(echo "$ADDR" | tr -cd 'x0-9a-fA-F').txt"
                { echo "== malloc_history $PID $ADDR ($SZ)"; \
                  malloc_history "$PID" "$ADDR" 2>&1 | head -60; } > "$OUTDIR/$NAME" || true
            done
    fi
fi

wait "$PID" || true

# environment + commit record
{
    echo "# phase-04e close (two-pass probe)"
    echo "# date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# host: $(sysctl -n hw.model 2>/dev/null || true) $(sysctl -n machdep.cpu.brand_string 2>/dev/null || true)"
    echo "# ram: $(($(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1024 / 1024 / 1024)) GiB unified"
    echo "# os: $(sw_vers -productName 2>/dev/null || true) $(sw_vers -productVersion 2>/dev/null || true)"
    echo "# llama.cpp commit: $(git -C /Users/pmains/Code/openclaw/kimi/llama.cpp rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "# kimi commit: $(git -C /Users/pmains/Code/openclaw/kimi rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "# model: $MODEL"
    echo "# probe: $PROBE"
    echo "# n_decode: $N  hold_ms: $HOLD  stacklog: $STACKLOG"
    echo "# flags: n_gpu_layers=0 load_mode=NONE n_ctx=4096 (pinned)"
} > "$OUTDIR/env.txt"

echo "done: $OUTDIR"
