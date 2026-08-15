#!/usr/bin/env python3
"""Phase 8 analysis: merge phase-07b baseline + phase-08 workload summaries
into one comparison table for STREAMING_RESULTS.md / phase-08-report.md.

Reads the baseline-summary.csv files produced by tools/phase07b_summarize.py:
    --ref     benchmarks/results/phase-07b/baseline
    --w1      benchmarks/results/phase-08/coding-lru
    --w2      benchmarks/results/phase-08/reasoning   (optional)

Prints markdown tables: decode tok/s, hit rate, SSD MB/token, phys decode
MB, component ms/step, and speedup vs in-session uncached control.
"""
import argparse
import csv
import statistics
from collections import defaultdict

FIELDS = [
    "decode_tps", "steady_tps_mean", "steady_ms", "pread_ms", "repack_ms",
    "placement_ms", "copy_ms", "sync_ms", "route_ms", "expert_ms",
    "build_measured_ms", "other_ms", "hit_rate", "zc_hit_rate",
    "ph_hit_rate", "misses", "evictions", "ssd_mb_per_token",
    "phys_decode_mb", "prefill_peak_mb", "cache_mb", "budget_mb",
]


def load(root):
    rows = {}
    with open(f"{root}/baseline-summary.csv") as f:
        for r in csv.DictReader(f):
            rows[r["rung"]] = r
    return rows


def fmt(r, field, nd=3):
    v = r.get(field + "_median", "")
    if v == "":
        return "—"
    try:
        return f"{float(v):.{nd}f}"
    except ValueError:
        return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--w1", required=True)
    ap.add_argument("--w2", default=None)
    args = ap.parse_args()

    ref = load(args.ref)
    w1 = load(args.w1)
    w2 = load(args.w2) if args.w2 else None

    rungs = ["uncached", "cap-1", "cap-2", "cap-4", "cap-6", "cap-8", "cap-10", "cap-12"]

    def table(title, field, nd=3, rungs=rungs):
        print(f"\n### {title}\n")
        hdr = "| rung | 7B ref | W1 coding |" + (" W2 reasoning |" if w2 else "") + " |"
        sep = "|---:|---:|---:|" + ("---:|" if w2 else "")
        print(hdr)
        print(sep)
        for r in rungs:
            cells = [fmt(ref.get(r, {}), field, nd), fmt(w1.get(r, {}), field, nd)]
            if w2:
                cells.append(fmt(w2.get(r, {}), field, nd))
            print(f"| {r} | " + " | ".join(cells) + " |")

    table("Decode tok/s (median)", "decode_tps")
    table("Steady-state tok/s (mean, median)", "steady_tps_mean")
    table("Steady ms/step", "steady_ms", 1)
    table("Hit rate", "hit_rate")
    table("Zero-copy hit rate", "zc_hit_rate")
    table("SSD MB/token", "ssd_mb_per_token", 1)
    table("Physical decode MB", "phys_decode_mb", 1)
    table("Prefill peak MB", "prefill_peak_mb", 1)
    table("Cache MB used", "cache_mb", 1)

    for comp in ["pread_ms", "repack_ms", "placement_ms", "route_ms",
                 "expert_ms", "build_measured_ms", "other_ms"]:
        table(f"Component ms/step: {comp}", comp, 1)

    # Speedup vs in-session control from baseline-ratios.csv
    print("\n### Speedup vs in-session uncached control (median ratio)\n")
    for label, root in [("7B ref", args.ref), ("W1 coding", args.w1)] + (
            [("W2 reasoning", args.w2)] if w2 else []):
        d = defaultdict(list)
        with open(f"{root}/baseline-ratios.csv") as f:
            for r in csv.DictReader(f):
                d[r["rung"]].append(float(r["ratio"]))
        med = {k: statistics.median(v) for k, v in d.items()}
        row = " | ".join(f"{med.get(r, 0):.2f}" for r in rungs[1:])
        print(f"| {label} | {row} |")


if __name__ == "__main__":
    main()
