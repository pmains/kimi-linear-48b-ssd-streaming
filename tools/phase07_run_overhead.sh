#!/bin/bash
# Phase 7: quantify instrumentation overhead (A/B on the same binary).
#
# The Phase 7 additions over the Phase 6B state are: (1) per-step counter/
# timer splits (repack vs placement, hit classes) — pure CPU; (2) ten new
# stats.csv columns; (3) mem.csv (KIMI_PHASE7_INSTR=1); (4) cache_layers.csv
# (KIMI_PHASE7_INSTR=1, cache enabled only).
#
# Config A (baseline = Phase 6B behavior): phase04_run_streamed.sh without
# KIMI_PHASE7_INSTR (still writes retr.csv + stats.csv, as Phase 6B did).
# Config B (full Phase 7): KIMI_PHASE7_INSTR=1.
# Each config runs twice; the summary takes the min (least-throttled) decode
# mean, matching the ladder convention.
#
# Usage:
#   KIMI_EXPERT_CACHE_MB=<mib> KIMI_EXPERT_CACHE_MODE=zerocopy \
#       tools/phase07_run_overhead.sh OUTDIR [N_TOKENS]
set -euo pipefail

OUT="${1:?outdir}"
N="${2:-64}"
SEED=1
PROMPT="benchmarks/prompts/phase-04-ref.md"

export CTX=4096
CACHE_ENV=()
if [ -n "${KIMI_EXPERT_CACHE_MB:-}" ]; then
    CACHE_ENV=(KIMI_EXPERT_CACHE_MB="$KIMI_EXPERT_CACHE_MB")
    if [ -n "${KIMI_EXPERT_CACHE_MODE:-}" ]; then
        CACHE_ENV+=(KIMI_EXPERT_CACHE_MODE="$KIMI_EXPERT_CACHE_MODE")
    fi
fi

mkdir -p "$OUT"

run_one() {
    local tag="$1" instr="$2" suffix="$3"
    local dir="$OUT/${tag}-${suffix}"
    # bash 3.2-safe empty-array expansion (${arr[@]+...})
    if [ "$instr" = "1" ]; then
        env ${CACHE_ENV[@]+"${CACHE_ENV[@]}"} KIMI_PHASE7_INSTR=1 \
            tools/phase04_run_streamed.sh "$dir" "$PROMPT" "$N" "$SEED" naive > /dev/null 2>&1
    else
        env ${CACHE_ENV[@]+"${CACHE_ENV[@]}"} \
            tools/phase04_run_streamed.sh "$dir" "$PROMPT" "$N" "$SEED" naive > /dev/null 2>&1
    fi
}

for suffix in a b; do run_one base 0 "$suffix"; done
for suffix in a b; do run_one instr 1 "$suffix"; done

decode_mean() { # file
    awk -F, -v n="$N" 'NR>1 && $2=="decode" && $1>=6 { t+=$12; c++ }
        END { if (c>0) printf "%.3f", 1e6/(t/c) }' "$1"
}

B_A=$(decode_mean "$OUT/base-a/stats.csv")
B_B=$(decode_mean "$OUT/base-b/stats.csv")
I_A=$(decode_mean "$OUT/instr-a/stats.csv")
I_B=$(decode_mean "$OUT/instr-b/stats.csv")
# min-of-2 = least-throttled
B=$(python3 -c "print(min($B_A,$B_B))")
I=$(python3 -c "print(min($I_A,$I_B))")
OVH=$(python3 -c "print(100.0*($I-$B)/$B if $B>0 else 0.0)")

{
    echo "config,run_a_tok_s,run_b_tok_s,min_tok_s"
    echo "base(no-instr),$B_A,$B_B,$B"
    echo "instr(full),$I_A,$I_B,$I"
    echo "overhead_pct,na,na,$OVH"
} > "$OUT/overhead.csv"

cat "$OUT/overhead.csv"
echo "cache: ${KIMI_EXPERT_CACHE_MB:-uncached} MiB ${KIMI_EXPERT_CACHE_MODE:-} mode, ctx 4096, $N tokens, min-of-2 decode (steps >= 6)"
