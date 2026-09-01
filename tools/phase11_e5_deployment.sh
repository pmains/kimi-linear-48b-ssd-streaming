#!/bin/bash
# Phase 11 E5 — Live OpenClaw Deployment Qualification driver (retained).
#
# Measures the user-visible performance of the corrected Metal Kimi runtime
# through the actual modified OpenClaw (provider kimi-local -> live
# llama-server on 127.0.0.1:18080), cold and warm, per Pete's E5 directive.
#
# Runs:
#   A1  cold ordinary turn (fresh server, no /prefill, realistic prompt)
#   B1  immediate warm follow-up (same server, prefix-reuse prompt)
#   A2  cold repeat (server restarted again)   [C: reproducibility]
#   B2  warm repeat                            [C: reproducibility]
#
# Per-run capture (see run_turn):
#   client wall + client TTFT (tools/phase11_e5_ttft.py)
#   server log timing block: prompt eval ms/tokens, eval ms/tokens, cached
#   stats.csv delta: cache lookups/hits/misses/evictions, pread bytes (SSD)
#   retr.csv delta: expert-read bytes
#   server RSS peak during the turn (ps sampling)
#   covariates (memory pressure, loadavg) before/after the whole sequence
#
# Usage: tools/phase11_e5_deployment.sh [OUTDIR]
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/benchmarks/results/phase-11/e5-deployment}"
RUNS="${@:2}"
[ -z "$RUNS" ] && RUNS="A1 B1 A2 B2"   # default full sequence; pass a subset to resume
mkdir -p "$OUT"

SRVLOG=/tmp/kimi-llama-server.launchd.log
PIDFILE=/tmp/kimi-llama-server.pid
STATS="$OUT/stats.csv"
RETR="$OUT/retr.csv"
MEM="$OUT/mem.csv"
CLAYERS="$OUT/cache_layers.csv"
PROMPT_A="${PROMPT_A:-$ROOT/benchmarks/prompts/phase-11-e5-engineering.md}"
PROMPT_B="${PROMPT_B:-$ROOT/benchmarks/prompts/phase-11-e5-engineering-followup.md}"
TTFT="$ROOT/tools/phase11_e5_ttft.py"
MODEL_ID="kimi-local/kimi-linear-48b"

now() { date +%s.%N; }

cov() { # cov <tag>
    local tag="$1"
    {
        echo "=== $tag $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
        echo "--- loadavg ---"; sysctl -n vm.loadavg
        echo "--- memory_pressure ---"; memory_pressure -Q 2>/dev/null | head -3
        echo "--- vm_stat (free/inactive/compressed) ---"; vm_stat | grep -E "Pages free|Pages inactive|Pages occupied by compressor"
        echo "--- server pid ---"; cat "$PIDFILE" 2>/dev/null || echo none
        echo "--- server rss KB ---"; ps -o rss= -p "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null | tr -d ' ' || echo none
    } > "$OUT/cov-$tag.txt"
}

restart_server() { # cold: kill via launchctl kickstart (KeepAlive respawns), wait healthy
    local old_pid="$(cat "$PIDFILE" 2>/dev/null || echo 0)"
    launchctl kickstart -k "gui/$(id -u)/com.openclaw.kimi-llama-server" >/dev/null 2>&1
    local pid=""
    for i in $(seq 1 90); do
        pid="$(cat "$PIDFILE" 2>/dev/null || echo "")"
        if [ -n "$pid" ] && [ "$pid" != "$old_pid" ] && curl -sf -m 2 http://127.0.0.1:18080/health >/dev/null 2>&1; then
            echo "server restarted: pid $old_pid -> $pid (healthy after ~${i}s)"
            sleep 1
            return 0
        fi
        sleep 1
    done
    echo "ERROR: server did not restart healthy; pid=$pid" >&2
    return 1
}

snapshot_lines() { # <file> -> echo count (or 0)
    if [ -f "$1" ]; then wc -l < "$1" | tr -d ' '; else echo 0; fi
}

run_turn() { # run_turn <label> <promptfile>
    local label="$1" promptfile="$2"
    local dir="$OUT/$label"
    mkdir -p "$dir"
    local pid="$(cat "$PIDFILE" 2>/dev/null || echo 0)"

    # pre-run snapshots
    local s0 r0 m0 c0 l0
    s0=$(snapshot_lines "$STATS"); r0=$(snapshot_lines "$RETR"); m0=$(snapshot_lines "$MEM"); c0=$(snapshot_lines "$CLAYERS"); l0=$(snapshot_lines "$SRVLOG")

    # RSS sampler (server pid)
    if [ "$pid" != "0" ]; then
        ( while kill -0 "$pid" 2>/dev/null; do ps -o rss= -p "$pid" 2>/dev/null | tr -d ' ' >> "$dir/rss.samples"; sleep 0.2; done ) &
        local SAMPLER=$!
    fi

    local T0 T1
    T0=$(now)
    python3 "$TTFT" "$dir" "$label" "$promptfile" > "$dir/ttft.stdout" 2>"$dir/ttft.stderr"
    local RC=$?
    T1=$(now)

    [ -n "${SAMPLER:-}" ] && kill "$SAMPLER" 2>/dev/null

    # server log delta
    local l1; l1=$(snapshot_lines "$SRVLOG")
    if [ "$l1" -gt "$l0" ]; then
        sed -n "$((l0+1)),${l1}p" "$SRVLOG" > "$dir/srvlog.delta"
    else
        : > "$dir/srvlog.delta"
    fi

    # stats/retr deltas
    local s1 r1; s1=$(snapshot_lines "$STATS"); r1=$(snapshot_lines "$RETR")
    if [ "$s1" -gt "$s0" ]; then tail -n "$((s1-s0))" "$STATS" | grep -v '^#' > "$dir/stats.delta"; else : > "$dir/stats.delta"; fi
    if [ "$r1" -gt "$r0" ]; then tail -n "$((r1-r0))" "$RETR" | grep -v '^#' > "$dir/retr.delta"; else : > "$dir/retr.delta"; fi

    # parse server timing (llama-server print_timing block)
    local pe_ms pe_tok ev_ms ev_tok cached
    pe_ms=$(grep -oE "prompt eval time = +[0-9.]+ ms / +[0-9]+ tokens" "$dir/srvlog.delta" | tail -1 | grep -oE "[0-9.]+" | head -1)
    pe_tok=$(grep -oE "prompt eval time = +[0-9.]+ ms / +[0-9]+ tokens" "$dir/srvlog.delta" | tail -1 | grep -oE "[0-9.]+" | tail -1)
    ev_ms=$(grep -oE "eval time = +[0-9.]+ ms / +[0-9]+ tokens" "$dir/srvlog.delta" | tail -1 | grep -oE "[0-9.]+" | head -1)
    ev_tok=$(grep -oE "eval time = +[0-9.]+ ms / +[0-9]+ tokens" "$dir/srvlog.delta" | tail -1 | grep -oE "[0-9.]+" | tail -1)
    cached=$(grep -oE "cached_tokens = +[0-9]+" "$dir/srvlog.delta" | tail -1 | grep -oE "[0-9]+" | head -1)

    # stats aggregates (columns: step,phase,n_tokens,pread_calls,pread_bytes,...cache_lookups,hits,misses,evictions,...)
    local n_stats lookups hits misses evicts pread_bytes zc
    n_stats=$(wc -l < "$dir/stats.delta" | tr -d ' ')
    lookups=$(awk -F, 'NF>=14 {s+=$14} END{print s+0}' "$dir/stats.delta")
    hits=$(awk -F, 'NF>=15 {s+=$15} END{print s+0}' "$dir/stats.delta")
    misses=$(awk -F, 'NF>=16 {s+=$16} END{print s+0}' "$dir/stats.delta")
    evicts=$(awk -F, 'NF>=17 {s+=$17} END{print s+0}' "$dir/stats.delta")
    pread_bytes=$(awk -F, 'NF>=6 {s+=$6} END{print s+0}' "$dir/stats.delta")
    # retr bytes (col 7) and rows
    local n_retr retr_bytes
    n_retr=$(wc -l < "$dir/retr.delta" | tr -d ' ')
    retr_bytes=$(awk -F, 'NF>=7 {s+=$7} END{print s+0}' "$dir/retr.delta")

    # RSS peak
    local rss_peak=0
    if [ -f "$dir/rss.samples" ]; then rss_peak=$(sort -n "$dir/rss.samples" | tail -1 | tr -d ' '); fi

    # client timings
    local wall ttft
    wall=$(python3 -c "import json;print(json.load(open('$dir/$label.client.json'))['wall_s'])" 2>/dev/null || echo 0)
    ttft=$(python3 -c "import json;d=json.load(open('$dir/$label.client.json'));print(d['ttft_client_s'] if d['ttft_client_s'] is not None else 'null')" 2>/dev/null || echo null)

    local cold_bool
    case "$label" in A*) cold_bool=true ;; *) cold_bool=false ;; esac
    cat > "$dir/summary.json" <<EOF
{
  "label": "$label",
  "cold": $cold_bool,
  "client_rc": $RC,
  "client_wall_s": $wall,
  "client_ttft_s": $ttft,
  "server_prompt_eval_ms": ${pe_ms:-null},
  "server_prompt_tokens": ${pe_tok:-null},
  "server_eval_ms": ${ev_ms:-null},
  "server_eval_tokens": ${ev_tok:-null},
  "server_cached_tokens": ${cached:-null},
  "stats_rows": $n_stats,
  "cache_lookups": $lookups,
  "cache_hits": $hits,
  "cache_misses": $misses,
  "cache_evictions": $evicts,
  "pread_bytes": $pread_bytes,
  "retr_rows": $n_retr,
  "retr_bytes": $retr_bytes,
  "server_rss_peak_kb": $rss_peak,
  "wall_s": $(echo "$T1 $T0" | awk '{printf "%.3f", $1-$2}')
}
EOF
    echo "[$label] rc=$RC wall=${wall}s ttft_client=${ttft}s prefill=${pe_ms:-?}ms/${pe_tok:-?}tok decode=${ev_ms:-?}ms/${ev_tok:-?}tok cached=${cached:-0} rss_peak=${rss_peak}KB stats_rows=$n_stats lookups=$lookups hits=$hits misses=$misses evicts=$evicts pread_B=$pread_bytes retr_rows=$n_retr retr_B=$retr_bytes"
}

# ---- main ----
echo "=== E5 deployment qualification: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "model=$MODEL_ID live-commit=$(head -1 $ROOT/runtime/live/COMMIT)"
cov before

for run in $RUNS; do
    case "$run" in
        A*) echo "--- $run (cold) ---"; restart_server || exit 1; run_turn "$run" "$PROMPT_A" ;;
        B*) echo "--- $run (warm follow-up) ---"; run_turn "$run" "$PROMPT_B" ;;
        *) echo "unknown run: $run" >&2; exit 2 ;;
    esac
done

cov after
echo "=== E5 done: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# summary table
python3 - <<EOF
import json, glob, os
rows=[]
for f in sorted(glob.glob("$OUT/A*/summary.json"))+sorted(glob.glob("$OUT/B*/summary.json")):
    rows.append(json.load(open(f)))
hdr=f"{'run':4} {'wall_s':>7} {'ttft_s':>7} {'prefill_ms':>10} {'pToks':>5} {'pT/s':>6} {'decode_ms':>9} {'dToks':>5} {'dT/s':>6} {'cached':>6} {'RSS_MB':>7} {'lookups':>7} {'hits':>6} {'misses':>6} {'evict':>5} {'pread_MB':>8} {'retr_MB':>7}"
print(hdr)
for r in rows:
    p_ms=r['server_prompt_eval_ms']; p_tok=r['server_prompt_tokens']; d_ms=r['server_eval_ms']; d_tok=r['server_eval_tokens']
    pts = round(p_tok/(p_ms/1000),1) if (p_ms and p_tok) else None
    dts = round(d_tok/(d_ms/1000),1) if (d_ms and d_tok) else None
    print(f"{r['label']:4} {r['wall_s']:7.2f} {str(r['client_ttft_s']):>7} {str(p_ms):>10} {str(p_tok):>5} {str(pts):>6} {str(d_ms):>9} {str(d_tok):>5} {str(dts):>6} {str(r['server_cached_tokens']):>6} {round(r['server_rss_peak_kb']/1024,1):>7} {r['cache_lookups']:>7} {r['cache_hits']:>6} {r['cache_misses']:>6} {r['cache_evictions']:>5} {round(r['pread_bytes']/1e6,1):>8} {round(r['retr_bytes']/1e6,1):>7}")
EOF
