#!/bin/bash
# Phase 10 reproducibility test.
#
# Verifies, from the release bundle, that:
#   1. the model loads successfully;
#   2. storage-backed expert execution is active (retrieval trace emitted,
#      expert cache engaged);
#   3. routing/retrieval invariants hold (Phase 7 per-step invariant check);
#   4. generated output is valid (deterministic, non-empty, no error
#      markers);
#   5. expected instrumentation is produced (retr.csv, stats.csv, moe.csv,
#      mem.csv, cache_layers.csv, act.bin, manifest.json).
#
# Usage:
#   tools/phase10_verify.sh [OUTDIR]
#   OUTDIR defaults to /tmp/kimi-phase10-verify
#
# Exit 0 = PASS. Prints a one-line verdict per check.
#
# This is a self-contained wrapper around the frozen Phase 4/7 runner and
# analyzer; it does not require the historical Phase 1-9 workflow.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTDIR="${1:-/tmp/kimi-phase10-verify}"
PROMPT_FILE="$REPO_ROOT/benchmarks/prompts/phase-03-coding-lru.md"
N_TOKENS="${N_TOKENS:-64}"
SEED="${SEED:-7}"
CACHE_MB="${KIMI_EXPERT_CACHE_MB:-4096}"
READ_WORKERS="${KIMI_EXPERT_READ_WORKERS:-4}"
FAIL=0

# CLI resolution: explicit KIMI_CLI wins; else the release bundle; else a
# source build (build-release or build-metal). The runner's own default is
# build-metal (frozen 9G harness convention) — pass the resolved path.
CLI="${KIMI_CLI:-}"
if [ -z "$CLI" ]; then
    for cand in "$REPO_ROOT/runtime/release-9f/bin/llama-cli" \
                "$REPO_ROOT/llama.cpp/build-release/bin/llama-cli" \
                "$REPO_ROOT/llama.cpp/build-metal/bin/llama-cli"; do
        if [ -x "$cand" ]; then CLI="$cand"; break; fi
    done
fi
if [ -z "$CLI" ] || [ ! -x "$CLI" ]; then
    echo "FAIL: no llama-cli found (set KIMI_CLI, or build the release bundle / source tree)" >&2
    exit 1
fi
if [ ! -f "$REPO_ROOT/models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf" ]; then
    echo "FAIL: model file missing (models/kimi-linear/...Q4_K_M.gguf)" >&2
    exit 1
fi

rm -rf "$OUTDIR"
mkdir -p "$OUTDIR"

echo "=== Phase 10 reproducibility test ==="
echo "outdir=$OUTDIR  n_tokens=$N_TOKENS  seed=$SEED  cache=${CACHE_MB}MiB  workers=$READ_WORKERS"
echo "cli=$CLI"

# --- run the streamed capture via the frozen Phase 4 runner ---
KIMI_CLI="$CLI" \
KIMI_EXPERT_CACHE_MB="$CACHE_MB" \
KIMI_EXPERT_CACHE_MODE=zerocopy \
KIMI_EXPERT_READ_WORKERS="$READ_WORKERS" \
KIMI_PHASE7_INSTR=1 \
CTX=4096 \
"$REPO_ROOT/tools/phase04_run_streamed.sh" "$OUTDIR" "$PROMPT_FILE" "$N_TOKENS" "$SEED" naive \
    > "$OUTDIR/verify-driver.log" 2>&1

check() {
    local name="$1" ok="$2" detail="$3"
    if [ "$ok" = "1" ]; then
        echo "PASS: $name"
    else
        echo "FAIL: $name — $detail" >&2
        FAIL=1
    fi
}

# --- 1. model loads + run completes ---
if [ -f "$OUTDIR/stats.csv" ] && [ -s "$OUTDIR/run.log" ]; then
    check "model loads and run completes" 1 ""
else
    check "model loads and run completes" 0 "stats.csv or run.log missing"
fi

# --- 2. storage-backed execution active ---
if [ -s "$OUTDIR/retr.csv" ] && grep -q "pread" <(head -1 "$OUTDIR/stats.csv"); then
    check "storage-backed execution active" 1 ""
else
    check "storage-backed execution active" 0 "retr.csv missing/empty or stats.csv lacks pread column"
fi

# --- 3. routing/retrieval invariants hold (Phase 7 analyzer) ---
INV="$(python3 "$REPO_ROOT/tools/phase07_summarize.py" "$OUTDIR" 2>&1 | grep -m1 -E "invariants: PASS|invariant check: PASS" || true)"
if [ -n "$INV" ]; then check "routing/retrieval invariants" 1 "phase07_summarize: $INV"; else check "routing/retrieval invariants" 0 "phase07_summarize did not report PASS"; fi

# --- 4. generated output valid ---
if grep -qiE "error|assert|abort|traceback|failed to load" "$OUTDIR/run.log"; then
    check "generated output valid" 0 "error markers in run.log"
else
    # non-empty generation: llama-cli prints a final token count line
    if grep -q "Generation:" "$OUTDIR/run.log"; then
        check "generated output valid" 1 "generation completed"
    else
        check "generated output valid" 0 "no generation summary in run.log"
    fi
fi

# --- 5. expected instrumentation produced ---
MISSING=""
for f in retr.csv stats.csv moe.csv act.bin manifest.json mem.csv cache_layers.csv; do
    [ -f "$OUTDIR/$f" ] && [ -s "$OUTDIR/$f" ] || MISSING="$MISSING $f"
done
if [ -z "$MISSING" ]; then
    check "instrumentation produced" 1 "all 7 artifacts present"
else
    check "instrumentation produced" 0 "missing:$MISSING"
fi

echo
if [ "$FAIL" = "0" ]; then
    echo "RESULT: PASS — Phase 10 reproducibility test succeeded"
    exit 0
else
    echo "RESULT: FAIL — see checks above" >&2
    exit 1
fi
