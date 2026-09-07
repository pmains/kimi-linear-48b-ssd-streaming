#!/usr/bin/env python3
"""Step 9C — narrow 128K validation driver (retained).

Runs the UNCHANGED Step 9B harness module (service_step09b_ablate.py) against
the live llama-server while it is temporarily configured at ctx 131072, and
redirects ONLY the output root to benchmarks/results/service-step-09c-verify/.
Phase-0 payload artifacts (system text, tools-full-29.json) and the frozen
suite are read from their original 9B locations; nothing is modified.

Authorized 9C scope (owner letter 2026-09-04):
  - conditions: C0 (bare) and C1a-D (verbatim system minus reply-directives) only
  - probes: P1 (mandated) + P3 (prose-family representative runnable without
    tool content; P4/P5/P7 need tool results -> replay-only, out of scope here)
    + P1b/P2 as 64K-clean controls to bound the ctx-swap interpretation
  - 3 reps per cell, agent-path sampler profile (no sampler fields), frozen
    prompts/expected untouched.

The 128K server configuration is temporary: the launchd job is booted out
before the run and re-bootstrapped (plist still says KIMI_CTX=65536) after.

Usage:
  python3 tools/service_step09c_ablate.py --conditions C0,C1a-D --probes P1,P3,P1b,P2 --reps 3
"""
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)

RESULTS9C = os.path.join(BASE, "benchmarks/results/service-step-09c-verify")

# Load the retained 9B harness module verbatim.
spec = importlib.util.spec_from_file_location(
    "service_step09b_ablate",
    os.path.join(HERE, "service_step09b_ablate.py"),
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# Redirect ONLY the output root. PHASE0/SUITE were bound at import time and
# keep pointing at the 9B phase0 artifacts and the frozen suite.
m.RESULTS = RESULTS9C

def main():
    os.makedirs(RESULTS9C, exist_ok=True)
    sys.argv[0] = "service_step09b_ablate.py"
    t0 = time.time()
    m.main()
    ctx = os.environ.get("KIMI_CTX", "")
    manifest9c = {
        "step": "9C",
        "objective": "narrow 128K validation: C0 + C1a-D at ctx 131072 vs 64K 9B baselines",
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
        "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "server_ctx_requested": ctx or "(KIMI_CTX unset; expected 131072 for this run)",
        "model_native_ctx_gguf": 1048576,
        "previous_ctx_9b": 65536,
        "probe_note": "P4/P5/P7 excluded: content requires tool results (replay-only in 9B); "
                      "P3 is the runnable prose-family representative; P1b/P2 are 64K-clean controls.",
        "changes": "temporary llama-server --ctx-size 131072 only; launchd plist untouched "
                   "(KIMI_CTX=65536 restored after run); harness module loaded unmodified.",
    }
    with open(os.path.join(RESULTS9C, "step09c-manifest.json"), "w") as f:
        json.dump(manifest9c, f, indent=1)
    print("9C manifest ->", os.path.join(RESULTS9C, "step09c-manifest.json"), flush=True)

if __name__ == "__main__":
    main()
