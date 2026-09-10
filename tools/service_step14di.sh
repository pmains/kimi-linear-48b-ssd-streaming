#!/bin/bash
# Step 14D(i) — persistent slot save/restore characterization.
# Retained launcher. Measurement-only; uses the live production llama-server
# slot save/load mechanism. See tools/service_step14di.py and
# SERVICE-ROADMAP.md §14D(i).
#
# Usage:
#   bash tools/service_step14di.sh                 # full run (48k/100k/150k + restart)
#   SMOKE=1 DO_RESTART=0 bash tools/service_step14di.sh   # cheap flow validation
#   DO_RESTART=0 bash tools/service_step14di.sh    # skip the controlled restart
set -u
cd /Users/pmains/Code/openclaw/kimi
mkdir -p benchmarks/results/service-step-14/14di
exec python3 tools/service_step14di.py
