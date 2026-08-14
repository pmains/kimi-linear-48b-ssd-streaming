#!/bin/bash
# Phase 6 ladder benchmark: streamed decode tok/s vs cache budget.
#
# Runs the deterministic 64-token streamed capture at each ladder capacity
# (1, 2, 4, 6, 8, 10, 12, 14.6 GB) plus a no-cache baseline, then writes a
# summary CSV. Steady decode = steps >= 8 (after cache warm-up / compression
# settle).
#
# Usage: tools/phase06_run_ladder.sh OUTDIR_ROOT [N_TOKENS] [SEED]
set -euo pipefail

# Pin the KV cache (Phase 4E convention): without --ctx-size the default
# n_ctx=1,048,576 allocates an 8 GB f16 KV cache, which pushes the big
# ladder rungs past the 24 GB machine and confounds timing with memory
# pressure. All rungs run at ctx 4096 (KV = 31.5 MiB).
export CTX=4096

ROOT="${1:?outdir root}"
N="${2:-64}"
SEED="${3:-1}"
PROMPT="benchmarks/prompts/phase-04-ref.md"
# ladder capacities in GB (roadmap §Phase 6); KIMI_EXPERT_CACHE_MB takes MiB
CAPS_GB=(1 2 4 6 8 10 12 14.6)
OUT="$ROOT/summary.csv"

mkdir -p "$ROOT"
echo "capacity_gb,tok_per_s,avg_total_ms,avg_pread_ms,avg_copy_ms,avg_build_ms,hit_rate,lookups,hits,misses,evictions,cache_bytes_used_mb,budget_mb" > "$OUT"

run_cap() {
    local gb="$1"
    local mib
    mib=$(python3 -c "print(int(round($gb * 1024)))")
    # two runs per rung; the summary takes the min (least-throttled) timing
    for suffix in a b; do
        local dir="$ROOT/cap-${gb}-${suffix}"
        if [ ! -f "$dir/stats.csv" ]; then
            KIMI_EXPERT_CACHE_MB="$mib" tools/phase04_run_streamed.sh "$dir" "$PROMPT" "$N" "$SEED" naive > /dev/null 2>&1
        fi
    done
    local f1="$ROOT/cap-${gb}-a/stats.csv"
    local f2="$ROOT/cap-${gb}-b/stats.csv"
    awk -F, -v cap="$gb" -v f1="$f1" -v f2="$f2" '
        function snap(f, out) {
            while ((getline line < f) > 0) {
                n=split(line, a, ",");
                if (a[2]=="decode" && a[1]>=8) {
                    out[1]+=a[15]; out[2]+=a[16]; out[3]+=a[14]; out[4]+=a[17];
                    out[5]+=a[12]; out[6]+=a[6]; out[7]+=a[7]; out[8]+=a[11]; out[9]++;
                    out[10]=a[19]; out[11]=a[20]
                }
            }
            close(f)
        }
        BEGIN {
            split("", x); split("", y);
            snap(f1, x); snap(f2, y);
            # min total per step across the two runs
            tx = (x[9] ? x[5]/x[9] : 0); ty = (y[9] ? y[5]/y[9] : 0);
            t = (tx < ty ? tx : ty);
            n = (tx < ty ? x[9] : y[9]);
            h = (tx < ty ? x[1] : y[1]); m = (tx < ty ? x[2] : y[2]);
            l = (tx < ty ? x[3] : y[3]); e = (tx < ty ? x[4] : y[4]);
            p = (tx < ty ? x[6] : y[6]); c = (tx < ty ? x[7] : y[7]);
            b = (tx < ty ? x[8] : y[8]); cb = (tx < ty ? x[10] : y[10]); bud = (tx < ty ? x[11] : y[11]);
            printf "%s,%.3f,%.0f,%.0f,%.0f,%.0f,%.3f,%d,%d,%d,%d,%.1f,%.1f\n",
                cap, 1e6/t, t/1000, p/n/1000, c/n/1000, b/n/1000,
                (l>0 ? h/l : 0), l, h, m, e, cb/1048576, bud/1048576
        }
    ' >> "$OUT"
    echo "  cap ${gb}GB done -> $(tail -1 "$OUT")"
}

# uncached baseline (no KIMI_EXPERT_CACHE_MB)
BASE="$ROOT/uncached"
if [ ! -f "$BASE/stats.csv" ]; then
    tools/phase04_run_streamed.sh "$BASE" "$PROMPT" "$N" "$SEED" naive > /dev/null 2>&1
fi
awk -F, '
    NR>1 && $2=="decode" && $1>=8 { t+=$12; n++ }
    END { printf "uncached,%.3f,%.0f,0,0,0,0,0,0,0,0,0,0\n", n/(t/1e6), t/n/1000 }' "$BASE/stats.csv" >> "$OUT"
echo "  uncached done"

# run in reverse order to decorrelate thermal drift from capacity
for ((i = ${#CAPS_GB[@]} - 1; i >= 0; i--)); do
    run_cap "${CAPS_GB[i]}"
done
echo "ladder complete: $OUT"
