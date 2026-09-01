#!/usr/bin/env python3
"""Phase 11 E3A — corrected-Metal decode regression decomposition.

Reproduces the per-step decode-timer decomposition from retained stats.csv
captures (no live runs required):

  - corrected Metal: benchmarks/results/phase-11/e5-deployment/stats.csv
    (E5 server capture, 360 decode steps, 1 tok/step)
  - CPU reference:   benchmarks/results/phase-k1/full/coding-cap4/b8-A-after/stats.csv
    (frozen K1 CPU cached MXFP4, 127 decode steps)
  - pre-fix degenerate baseline (no stats.csv retained): E1 report
    progress/phase-11-e1-report.md — decode steady hit_rate 1.000, SSD
    0.00 MB/token (all-expert-0 path; 24.3 tok/s).

Columns (stats.csv): pread_calls/bytes/us, copy_us, sync_us,
route_compute_us (dense trunk + routing), expert_compute_us, build_us,
total_us; pread_wall_us == pread_us at workers=1.

Usage: python3 tools/phase11_e3a_analyze.py [metal.csv] [cpu.csv]
"""
import csv
import statistics as st
import sys

METAL = sys.argv[1] if len(sys.argv) > 1 else \
    "benchmarks/results/phase-11/e5-deployment/stats.csv"
CPU = sys.argv[2] if len(sys.argv) > 2 else \
    "benchmarks/results/phase-k1/full/coding-cap4/b8-A-after/stats.csv"


def load(p):
    rows = []
    with open(p) as f:
        for r in csv.DictReader(f):
            if r.get("step") == "step":
                continue
            rows.append(r)
    return [r for r in rows if r.get("phase") == "decode"]


def m(rows, c):
    return st.mean([float(r[c]) for r in rows])


def summ(name, rows):
    tot = m(rows, "total_us")
    print(f"{name}: {len(rows)} decode steps, {m(rows,'n_tokens'):.2f} tok/step")
    for c in ["pread_us", "copy_us", "sync_us", "route_compute_us",
              "expert_compute_us", "build_us", "total_us"]:
        v = m(rows, c)
        print(f"  {c:22s} {v/1000:9.2f} ms/tok  {100*v/tot:5.1f} %")
    print(f"  {'pread_bytes':22s} {m(rows,'pread_bytes')/1e6:9.1f} MB/step")
    print(f"  {'pread_calls':22s} {m(rows,'pread_calls'):9.0f} /step  "
          f"eff BW {m(rows,'pread_bytes')/m(rows,'pread_us')*1e6/1e9:.2f} GB/s")
    print(f"  {'cache':22s} lookups={m(rows,'cache_lookups'):.0f} "
          f"hits={m(rows,'cache_hits'):.0f} misses={m(rows,'cache_misses'):.0f} "
          f"slots={m(rows,'n_slots'):.0f}")
    print(f"  -> decode {1/(tot/1e6):.2f} tok/s")


metal = load(METAL)
cpu = load(CPU)
print("=== E3A decode decomposition ===")
summ("corrected Metal (E5)", metal)
print()
summ("CPU cached reference (K1 b8-A-after)", cpu)
