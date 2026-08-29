#!/bin/bash
# Phase 9G Session 1 driver (frozen protocol, 2026-08-28).
# Sequential: null phase then positive-control phase, then analysis.
# Usage: bash run_session2.sh  (from repo root)
set -u
cd "$(dirname "$0")/../../../.." || exit 99   # repo root (4 levels up from session-2/)
echo "cwd: $(pwd)"
SEED="${SEED:-1788018038}"

echo "=== SESSION 2 start: $(date -u +%Y-%m-%dT%H:%M:%SZ) seed=$SEED ==="

echo "--- phase: null ---"
CONFIG=coding-cap4 MODE=null SEED="$SEED" \
    tools/phase09g_run_brackets.sh benchmarks/results/phase-09g/session-2/null 10 128 \
    > benchmarks/results/phase-09g/session-2/null.session.log 2>&1
N_EXIT=$?
echo "--- null driver exit=$N_EXIT ---"
if [ "$N_EXIT" -eq 0 ]; then
    python3 tools/phase09g_analyze.py benchmarks/results/phase-09g/session-2/null \
        >> benchmarks/results/phase-09g/session-2/null.session.log 2>&1
    N_A_EXIT=$?
    echo "--- null analyze exit=$N_A_EXIT ---"
fi

echo "--- phase: positive (W4 vs W1) ---"
CONFIG=coding-cap4 MODE=positive SEED="$SEED" \
    tools/phase09g_run_brackets.sh benchmarks/results/phase-09g/session-2/positive 10 128 \
    > benchmarks/results/phase-09g/session-2/positive.session.log 2>&1
P_EXIT=$?
echo "--- positive driver exit=$P_EXIT ---"
if [ "$P_EXIT" -eq 0 ]; then
    python3 tools/phase09g_analyze.py benchmarks/results/phase-09g/session-2/positive \
        >> benchmarks/results/phase-09g/session-2/positive.session.log 2>&1
    P_A_EXIT=$?
    echo "--- positive analyze exit=$P_A_EXIT ---"
fi

echo "=== SESSION 2 done: $(date -u +%Y-%m-%dT%H:%M:%SZ) null_exit=$N_EXIT null_analyze=${N_A_EXIT:-na} positive_exit=$P_EXIT positive_analyze=${P_A_EXIT:-na} ==="
