#!/usr/bin/env bash
# Step 10 — real-path reference leg (Leg 3) driver (retained, 2026-09-04).
#
# Runs the P1/P2 probes through the REAL OpenClaw agent path (openclaw agent,
# pinned llama-server/kimi-linear-48b, NO --deliver) in fresh sessions under a
# NEW session namespace (step10), STEP10_REPS (default 3) reps per probe, then
# classifies every reply under the frozen 9B/9C family semantics and the frozen
# Step 9 rubric (service_step09d_classify.py — identical code semantics to the
# 9D verify leg).
#
# The Step 10 analyzer merges these rows with the FROZEN 9D verify rows
# (benchmarks/results/service-step-09d-verify/classified.csv, n=3/probe) to
# form the n=6/probe real-path distribution required by the pre-registered
# design. Do NOT copy 9D rows into this directory; the merge happens in
# service_step10_analyze.py.
#
# Precondition (enforced): the gateway must have been started AFTER the 9D
# dist patch (daemon caches dist modules at start). The guard compares the
# gateway process start time against the patched dist file mtimes and aborts
# on a stale gateway unless STEP10_ALLOW_STALE=1.
#
# Usage: tools/service_step10_realpath.sh [OUTDIR]
#   OUTDIR default: benchmarks/results/service-step-10/realpath
# Env: STEP10_REPS (default 3), STEP10_KEYSUFFIX (extra namespace suffix),
#      STEP10_ALLOW_STALE=1 to bypass the stale-gateway guard.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/benchmarks/results/service-step-10/realpath}"
SUITE="$ROOT/benchmarks/results/service-step-09"
DIST=/opt/homebrew/lib/node_modules/openclaw/dist
TURN="$ROOT/tools/service_step09_turn.py"
CLASSIFY="$ROOT/tools/service_step09d_classify.py"
MANIFEST="$ROOT/service-progress/step-09d-dist-diffs/MANIFEST.sha256"
AGENT=kimi
REPS="${STEP10_REPS:-3}"
SFX="${STEP10_KEYSUFFIX:-}"
PROBES="P1 P2"

mkdir -p "$OUT"

# ---- stale-gateway guard ------------------------------------------------
GWPID=$(launchctl list 2>/dev/null | awk '$3=="ai.openclaw.gateway" {print $1}' | head -1)
if [ -n "$GWPID" ] && [ "$GWPID" != "-" ]; then
    GW_START=$(ps -o lstart= -p "$GWPID" 2>/dev/null)
    GW_EPOCH=$(date -j -f "%a %b %d %T %Y" "$GW_START" +%s 2>/dev/null || echo 0)
else
    GW_EPOCH=0
fi
PATCH_EPOCH=0
for f in "$DIST/system-prompt-params-7goAPY5o.js" "$DIST/builtin-openclaw-nX1NfpmQ.js"; do
    m=$(stat -f %m "$f" 2>/dev/null || echo 0)
    [ "$m" -gt "$PATCH_EPOCH" ] && PATCH_EPOCH=$m
done
if [ "$GW_EPOCH" -lt "$PATCH_EPOCH" ] && [ "${STEP10_ALLOW_STALE:-0}" != "1" ]; then
    echo "ABORT: gateway PID $GWPID started $GW_START — BEFORE the 9D dist patch ($(date -r $PATCH_EPOCH)). Restart ai.openclaw.gateway between turns, then rerun (or set STEP10_ALLOW_STALE=1)." >&2
    exit 4
fi

# ---- environment snapshot for the report --------------------------------
SP_SHA=$(shasum -a 256 "$DIST/system-prompt-params-7goAPY5o.js" | cut -d' ' -f1)
BO_SHA=$(shasum -a 256 "$DIST/builtin-openclaw-nX1NfpmQ.js" | cut -d' ' -f1)
{
    echo "captured_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "gateway_pid=$GWPID"
    echo "gateway_start=$GW_START"
    echo "patched_sp_sha=$SP_SHA"
    echo "patched_bo_sha=$BO_SHA"
    echo "manifest_pat_sp=$(awk '/patched   system-prompt-params/{print $3}' "$MANIFEST")"
    echo "manifest_pat_bo=$(awk '/patched   builtin-openclaw/{print $3}' "$MANIFEST")"
    echo "server_health=$(curl -s -m 3 http://127.0.0.1:18080/health)"
} > "$OUT/env.txt"

# ---- per-rep real-path turns (fresh session per rep, step10 namespace) ---
for p in $PROBES; do
    prompt="$SUITE/prompts/$p.md"
    dir="$OUT/$p"
    mkdir -p "$dir"
    i=1
    while [ "$i" -le "$REPS" ]; do
        label="$p-r$i"
        key="agent:$AGENT:step10${SFX:+-$SFX}-$label"
        if [ -f "$dir/$label.reply.txt" ]; then
            echo "[$label] cached"
        else
            echo "[$label] running (real path, no --deliver, 64K pinned) $(date -u +%H:%M:%SZ)"
            python3 "$TURN" "$dir" "$label" "$prompt" "$key" --agent "$AGENT" --timeout 1200 \
                > "$dir/$label.turn.stdout" 2> "$dir/$label.turn.stderr"
            echo "[$label] rc=$(python3 -c "import json;print(json.load(open('$dir/$label.client.json'))['rc'])" 2>/dev/null) wall=$(python3 -c "import json;print(json.load(open('$dir/$label.client.json'))['wall_s'])" 2>/dev/null)s"
        fi
        i=$((i + 1))
    done
done

# ---- classification + summary (frozen 9D semantics) ---------------------
python3 "$CLASSIFY" "$OUT" "$SUITE" | tee "$OUT/summary.txt"
echo "classified.csv -> $OUT/classified.csv"
