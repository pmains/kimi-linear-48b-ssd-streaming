#!/usr/bin/env python3
"""Phase 7B baseline summarizer: per-rung dispersion across repetitions.

Consumes a tools/phase07b_run_baseline.sh root
(uncached-rN + cap-<GB>-rN run dirs), reuses
phase07_summarize.summarize_dir per run (full Phase 7 per-component
telemetry + per-step invariant validation), then aggregates per rung:

  median / min / max (+ mean / sd) across runs for every Phase 7
  metric: decode tok/s (all-decode and steady-state), ms/step, the
  per-component latencies (pread / repack / placement / copy / sync /
  route(trunk) / expert / build / build_measured / other), hit classes
  (zc / placement), misses, evictions, SSD MB/token, phys footprint,
  prefill peak, cache residency.

Also produces a thermal-drift view: each capped run's tok/s ratio vs
the nearest uncached control in run order (interleaved controls), so a
rung's cached-vs-uncached advantage is separable from machine drift.

Deterministic inputs (seed 1, temp 0, fixed prompt) mean hit rates and
byte counters are expected to be identical across reps; any dispersion
in those columns is reported as a red flag.

Usage:
    python3 tools/phase07b_summarize.py --root ROOT [--json OUT.json]
    (writes baseline-summary.csv, baseline-runs.csv, baseline-ratios.csv
     into ROOT; exit 0 = all invariants hold)

Performance-baseline validation only: no correctness/oracle assertions.
"""

import argparse
import csv
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from phase07_summarize import (  # noqa: E402
    summarize_dir, read_stats, C, WARMUP_DECODE_STEPS,
)

RUN_RE = re.compile(r"^(uncached|cap-(\d+))-r(\d+)$")


def steady_rows(stats):
    return [r for r in stats if r[C["phase"]] == "decode" and r[C["step"]] >= WARMUP_DECODE_STEPS]


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def median(xs):
    return statistics.median(xs) if xs else 0.0


def per_run_metrics(d):
    """One dict of scalar metrics per run dir, steady-state decode primary."""
    res = summarize_dir(d)
    if "error" in res:
        return {"dir": d, "error": res["error"]}, None
    stats = read_stats(os.path.join(d, "stats.csv"))
    dec = [r for r in stats if r[C["phase"]] == "decode"]
    steady = steady_rows(stats)
    cs = res["cache_decode_steady"]

    def steady_ms(col):
        return mean([r[C[col]] for r in steady]) / 1000.0 if steady else 0.0

    m = {
        "dir": d,
        "steps": len(stats),
        "decode_steps": len(dec),
        "steady_steps": len(steady),
        # tok/s: all-decode (ladder-summary.csv convention) and steady
        "decode_tps": (sum(r[C["n_tokens"]] for r in dec) * 1e6 / sum(r[C["total_us"]] for r in dec))
        if dec else 0.0,
        "steady_tps_mean": (sum(r[C["n_tokens"]] for r in steady) * 1e6
                            / sum(r[C["total_us"]] for r in steady)) if steady else 0.0,
        "steady_tps_median": 1e6 / median([r[C["total_us"]] for r in steady]) if steady else 0.0,
        "steady_ms": median([r[C["total_us"]] for r in steady]) / 1000.0 if steady else 0.0,
        "pread_ms": steady_ms("pread_us"),
        "repack_ms": steady_ms("repack_us"),
        "placement_ms": steady_ms("placement_us"),
        "copy_ms": steady_ms("copy_us"),
        "sync_ms": steady_ms("sync_us"),
        "route_ms": steady_ms("route_us"),
        "expert_ms": steady_ms("expert_us"),
        "build_ms": steady_ms("build_us"),
        "build_measured_ms": steady_ms("build_measured_us"),
        "other_ms": steady_ms("other_us"),
        "hit_rate": cs["hit_rate"],
        "zc_hit_rate": cs["zc_hit_rate"],
        "ph_hit_rate": cs["ph_hit_rate"],
        "misses": cs["misses"],
        "evictions": cs["evictions"],
        "lookups": cs["lookups"],
        "ssd_mb_per_token": res["ssd_mb_per_token_decode"],
        "phys_decode_mb": res["mem_decode_steady_phys_mb"],
        "prefill_peak_mb": res["mem_prefill_peak_phys_mb"],
        "cache_mb": res["cache_bytes_used_mb"],
        "budget_mb": res["cache_budget_mb"],
        "zc_mode": res["zc_mode"],
        "pure_zc_steps": res["pure_zc_decode_steps"],
        "mixed_steps": res["mixed_fallback_steps"],
        "invariants_ok": res["invariants"]["ok"],
        "n_violations": len(res["invariants"]["violations"]),
    }
    return m, res["invariants"]["violations"]


METRICS = [
    "decode_tps", "steady_tps_mean", "steady_tps_median", "steady_ms",
    "pread_ms", "repack_ms", "placement_ms", "copy_ms", "sync_ms",
    "route_ms", "expert_ms", "build_ms", "build_measured_ms", "other_ms",
    "hit_rate", "zc_hit_rate", "ph_hit_rate", "misses", "evictions",
    "ssd_mb_per_token", "phys_decode_mb", "prefill_peak_mb",
    "cache_mb", "budget_mb",
]


def agg(vals):
    if not vals:
        return {"median": 0.0, "min": 0.0, "max": 0.0, "mean": 0.0, "sd": 0.0}
    return {
        "median": median(vals), "min": min(vals), "max": max(vals),
        "mean": mean(vals),
        "sd": statistics.stdev(vals) if len(vals) > 1 else 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, metavar="ROOT")
    ap.add_argument("--json", metavar="OUT")
    args = ap.parse_args()
    root = args.root

    runs = []          # (label, rung, metrics, violations)
    for name in sorted(os.listdir(root)):
        mm = RUN_RE.match(name)
        if not mm:
            continue
        d = os.path.join(root, name)
        if not os.path.isfile(os.path.join(d, "stats.csv")):
            continue
        m, viol = per_run_metrics(d)
        if "error" in m:
            print(f"  {name}: ERROR {m['error']}")
            continue
        rung = "uncached" if mm.group(1) == "uncached" else f"cap-{mm.group(2)}"
        runs.append((name, rung, m, viol))

    if not runs:
        print("no run dirs found (expect uncached-rN / cap-<GB>-rN)")
        sys.exit(2)

    # timeline: order by captured_at (manifest), fallback to dir name
    def capture_key(name):
        man = os.path.join(root, name, "manifest.json")
        try:
            with open(man) as f:
                return json.load(f).get("captured_at", name)
        except Exception:
            return name

    runs.sort(key=lambda t: capture_key(t[0]))

    # ---- per-rung aggregation ----
    rungs = {}
    for name, rung, m, viol in runs:
        rungs.setdefault(rung, []).append(m)

    rung_order = ["uncached"] + [f"cap-{g}" for g in sorted(
        int(r[4:]) for r in rungs if r.startswith("cap-"))]
    summary_rows = []
    per_rung_json = {}
    for rung in rung_order:
        ms = rungs[rung]
        row = {"rung": rung, "n_runs": len(ms)}
        per_rung_json[rung] = {"n_runs": len(ms)}
        for metric in METRICS:
            a = agg([m[metric] for m in ms])
            row[f"{metric}_median"] = round(a["median"], 6)
            row[f"{metric}_min"] = round(a["min"], 6)
            row[f"{metric}_max"] = round(a["max"], 6)
            row[f"{metric}_mean"] = round(a["mean"], 6)
            row[f"{metric}_sd"] = round(a["sd"], 6)
            per_rung_json[rung][metric] = a
        # determinism flags (expect zero dispersion on these)
        row["hit_rate_dispersion"] = max(m["hit_rate"] for m in ms) - min(m["hit_rate"] for m in ms)
        row["ssd_dispersion"] = max(m["ssd_mb_per_token"] for m in ms) - min(m["ssd_mb_per_token"] for m in ms)
        row["invariants_ok_all"] = all(m["invariants_ok"] for m in ms)
        row["n_violations_total"] = sum(m["n_violations"] for m in ms)
        summary_rows.append(row)

    # ---- thermal-drift view: ratio vs nearest uncached control ----
    ratio_rows = []
    control = None
    for idx, (name, rung, m, viol) in enumerate(runs):
        if rung == "uncached":
            control = m["steady_tps_median"]
            continue
        if control is None or control <= 0:
            continue
        ratio_rows.append({
            "run": name, "rung": rung,
            "steady_tps": m["steady_tps_median"],
            "control_tps": control,
            "ratio": m["steady_tps_median"] / control,
        })

    def write_csv(path, rows, fields):
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {path}")

    sum_fields = list(summary_rows[0].keys())
    write_csv(os.path.join(root, "baseline-summary.csv"), summary_rows, sum_fields)

    run_fields = ["run", "rung"] + METRICS + [
        "steps", "steady_steps", "pure_zc_steps", "mixed_steps",
        "invariants_ok", "n_violations"]
    run_rows = [{"run": name, "rung": rung, **{k: m.get(k) for k in METRICS},
                 "steps": m["steps"], "steady_steps": m["steady_steps"],
                 "pure_zc_steps": m["pure_zc_steps"], "mixed_steps": m["mixed_steps"],
                 "invariants_ok": m["invariants_ok"], "n_violations": m["n_violations"]}
                for name, rung, m, viol in runs]
    write_csv(os.path.join(root, "baseline-runs.csv"), run_rows, run_fields)

    if ratio_rows:
        write_csv(os.path.join(root, "baseline-ratios.csv"), ratio_rows,
                  ["run", "rung", "steady_tps", "control_tps", "ratio"])
        for rung in rung_order[1:]:
            rs = [r["ratio"] for r in ratio_rows if r["rung"] == rung]
            if rs:
                per_rung_json[rung]["ratio_vs_uncached"] = agg(rs)

    # ---- print ----
    print(f"\n{'rung':>9} {'n':>2} {'tok/s med(min..max)':>22} {'ms/step med':>11} "
          f"{'pread':>6} {'repack':>6} {'place':>6} {'route':>6} {'expert':>6} "
          f"{'hit':>5} {'SSD MB/t':>8} {'phys MB':>7}  ratios")
    for row in summary_rows:
        rung = row["rung"]
        tps_med, tps_min, tps_max = row["steady_tps_median_median"], row["steady_tps_median_min"], row["steady_tps_median_max"]
        ratios = [r["ratio"] for r in ratio_rows if r["rung"] == rung] if rung != "uncached" else []
        rat = f"{median(ratios):.2f}x" if ratios else "—"
        print(f"{rung:>9} {row['n_runs']:>2} {f'{tps_med:.2f} ({tps_min:.2f}..{tps_max:.2f})':>22} "
              f"{row['steady_ms_median']:>11.0f} {row['pread_ms_median']:>6.0f} "
              f"{row['repack_ms_median']:>6.0f} {row['placement_ms_median']:>6.0f} "
              f"{row['route_ms_median']:>6.0f} {row['expert_ms_median']:>6.0f} "
              f"{row['hit_rate_median']:>5.3f} {row['ssd_mb_per_token_median']:>8.2f} "
              f"{row['phys_decode_mb_median']:>7.0f}  {rat}")

    print("\ncomponent medians (ms/step, decode steady) per rung:")
    print(f"{'rung':>9} {'pread':>6} {'repack':>6} {'place':>6} {'copy':>6} {'sync':>5} "
          f"{'route':>6} {'expert':>6} {'buildM':>6} {'other':>6}")
    for row in summary_rows:
        print(f"{row['rung']:>9} {row['pread_ms_median']:>6.0f} {row['repack_ms_median']:>6.0f} "
              f"{row['placement_ms_median']:>6.0f} {row['copy_ms_median']:>6.0f} "
              f"{row['sync_ms_median']:>5.0f} {row['route_ms_median']:>6.0f} "
              f"{row['expert_ms_median']:>6.0f} {row['build_measured_ms_median']:>6.0f} "
              f"{row['other_ms_median']:>6.0f}")

    # determinism red flags
    for row in summary_rows:
        if row["hit_rate_dispersion"] > 0 or row["ssd_dispersion"] > 0:
            print(f"  ! {row['rung']}: hit-rate dispersion {row['hit_rate_dispersion']:.4f}, "
                  f"SSD dispersion {row['ssd_dispersion']:.4f} (expected 0 for deterministic input)")

    n_bad = sum(1 for _, _, m, _ in runs if not m["invariants_ok"])
    if n_bad:
        print(f"\n  ! {n_bad}/{len(runs)} runs have invariant violations")
        for name, rung, m, viol in runs:
            if not m["invariants_ok"]:
                print(f"    {name}: {m['n_violations']} violation(s)")
                for v in viol[:6]:
                    print(f"      ! {v}")

    if args.json:
        out = {
            "root": root,
            "rungs": per_rung_json,
            "runs": run_rows,
            "ratios": ratio_rows,
        }
        with open(args.json, "w") as f:
            json.dump(out, f, indent=2)
        print(f"wrote {args.json}")

    print(f"\ninvariant check: {'PASS' if n_bad == 0 else 'FAIL'} "
          f"({len(runs)} runs, {sum(1 for _,_,m,_ in runs if m['invariants_ok'])} clean)")
    sys.exit(0 if n_bad == 0 else 1)


if __name__ == "__main__":
    main()
