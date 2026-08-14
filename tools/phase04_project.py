#!/usr/bin/env python3
"""Phase 4 acceptance-3/4: miss-path decomposition + cache-ladder tok/s projection.

Combines three artifacts, all already produced by other tools:

  1. stats.csv          — per-step delta timings from the streamed executor
                          (KIMI_STREAM_STATS_FILE). Columns (µs unless noted):
                          step,phase,n_tokens,pread_calls,pread_bytes,pread_us,
                          copy_us,sync_us,route_compute_us,expert_compute_us,
                          build_us,total_us,n_slots
  2. phase-03-locality.json — LRU/OPT hit rates at the Phase 8 ladder capacities,
                          produced by tools/expert_cache_sim.py.
  3. Phase 2 Metal budget — hard-coded context below (from progress/phase-02-*:
                          ~17.8 GiB working set for the full model; ~14.6 GB
                          practical GPU-side expert-cache headroom at 8K ctx).

Output:
  benchmarks/results/phase-04-miss-path.json   (decomposition, machine-readable)
  benchmarks/results/phase-04-projection.csv   (tok/s per ladder capacity)
  stdout human-readable summary

Projection model (documented lower bound, no overlap/prefetch assumed):

  Per decode step, uncached (measured):
      t_step(0) = total_us
        = pread_us + copy_us + sync_us          (storage miss path)
        + route_compute_us + expert_compute_us  (trunk + MoE kernel)
        + build_us                              (graph build / sched / readback)

  With an expert cache of capacity C and LRU hit rate h(C):
      t_step(C) = t_step(0) - pread_us * h(C)
  i.e. a hit eliminates only the SSD pread; the RAM->compute copy, sync,
  kernel, and trunk all still occur. This is a conservative lower bound:
  it ignores prefetch/overlap and any copy elision a Phase 6 cache could add.

  tok/s(C) = 1e6 / t_step(C)   (decode is one token per step).

Usage:
    python3 tools/phase04_project.py \
        benchmarks/results/traces/phase-04-fix-stream/stats.csv \
        benchmarks/results/phase-03-locality.json
"""

import csv
import json
import sys
from collections import defaultdict

LADDER_GB = [1, 2, 4, 6, 8, 10, 12]
EXTRA_GB = [14.6]
METAL_WORKING_SET_GIB = 17.8     # Phase 2: full-model Metal working set
METAL_CACHE_HEADROOM_GB = 14.6   # practical GPU-side expert cache at 8K ctx


def load_stats(path):
    rows = []
    with open(path) as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or row[0].startswith("#"):
                continue
            if row[0] == "step":
                continue
            rows.append({
                "step": int(row[0]),
                "phase": row[1],
                "n_tokens": int(row[2]),
                "pread_calls": int(row[3]),
                "pread_bytes": int(row[4]),
                "pread_us": int(row[5]),
                "copy_us": int(row[6]),
                "sync_us": int(row[7]),
                "route_compute_us": int(row[8]),
                "expert_compute_us": int(row[9]),
                "build_us": int(row[10]),
                "total_us": int(row[11]),
                "n_slots": int(row[12]),
            })
    return rows


def mean(vals):
    return sum(vals) / len(vals) if vals else 0.0


def per_phase(rows):
    out = {}
    for phase in ("prefill", "decode"):
        p = [r for r in rows if r["phase"] == phase]
        if not p:
            continue
        out[phase] = {
            "n_steps": len(p),
            "pread_calls": mean([r["pread_calls"] for r in p]),
            "pread_bytes": mean([r["pread_bytes"] for r in p]),
            "pread_us": mean([r["pread_us"] for r in p]),
            "copy_us": mean([r["copy_us"] for r in p]),
            "sync_us": mean([r["sync_us"] for r in p]),
            "route_compute_us": mean([r["route_compute_us"] for r in p]),
            "expert_compute_us": mean([r["expert_compute_us"] for r in p]),
            "build_us": mean([r["build_us"] for r in p]),
            "total_us": mean([r["total_us"] for r in p]),
            "n_slots": mean([r["n_slots"] for r in p]),
        }
    return out


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    stats_path, loc_path = sys.argv[1], sys.argv[2]

    rows = load_stats(stats_path)
    pp = per_phase(rows)
    dec = pp.get("decode")
    if not dec:
        raise SystemExit("no decode steps in stats.csv; cannot project decode tok/s")

    with open(loc_path) as f:
        loc = json.load(f)
    sims = loc["simulations"]

    # miss-path decomposition (decode, per step)
    pread = dec["pread_us"]
    copy = dec["copy_us"]
    sync = dec["sync_us"]
    route = dec["route_compute_us"]
    kernel = dec["expert_compute_us"]
    build = dec["build_us"]
    total = dec["total_us"]

    miss_path = {
        "storage_pread_us": pread,
        "storage_pread_mib_s": (dec["pread_bytes"] / 1024 / 1024) / (pread / 1e6) if pread else 0.0,
        "buffer_prep_copy_us": copy,
        "sync_us": sync,
        "kernel_expert_compute_us": kernel,
        "trunk_route_compute_us": route,
        "build_residual_us": build,
        "total_us": total,
        "effective_GB_per_step": dec["pread_bytes"] / 1e9,
        "n_slots": dec["n_slots"],
    }

    # projection: t_step(C) = total - pread * h(C)
    proj = []
    for s in sims:
        gb = s["capacity_gb"]
        if gb not in LADDER_GB + EXTRA_GB:
            continue
        h = s.get("global_lru_steady")
        if h is None:
            h = s.get("global_lru_overall")
        t_step = total - pread * h
        tok_s = 1e6 / t_step if t_step > 0 else float("inf")
        proj.append({
            "capacity_gb": gb,
            "global_lru_steady_hit_rate": h,
            "t_step_us": t_step,
            "decode_tok_per_s": tok_s,
        })

    # threshold: capacity needed for a given tok/s (interpolate from ladder)
    targets = [2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0]
    thresholds = {}
    for tgt in targets:
        cap = None
        for p in proj:
            if p["decode_tok_per_s"] >= tgt:
                cap = p["capacity_gb"]
                break
        thresholds[f"{tgt:g}_tok_s"] = cap

    result = {
        "stats_csv": stats_path,
        "locality_json": loc_path,
        "model": "conservative lower bound: t_step(C) = total - pread*h(C); hit removes SSD pread only",
        "phase2_metal_working_set_gib": METAL_WORKING_SET_GIB,
        "phase2_metal_cache_headroom_gb": METAL_CACHE_HEADROOM_GB,
        "decode_step_means_us": {k: round(v, 1) for k, v in pp["decode"].items()},
        "prefill_step_means_us": {k: round(v, 1) for k, v in pp.get("prefill", {}).items()},
        "miss_path_decomposition_us": {k: round(v, 1) for k, v in miss_path.items()},
        "projection": [
            {k: (round(v, 4) if isinstance(v, float) else v) for k, v in p.items()}
            for p in proj
        ],
        "threshold_capacities_gb": thresholds,
    }

    with open("benchmarks/results/phase-04-miss-path.json", "w") as f:
        json.dump(result, f, indent=2)
    with open("benchmarks/results/phase-04-projection.csv", "w") as f:
        f.write("capacity_gb,hit_rate_steady,t_step_us,decode_tok_per_s\n")
        for p in proj:
            f.write(f"{p['capacity_gb']},{p['global_lru_steady_hit_rate']:.4f},"
                    f"{p['t_step_us']:.1f},{p['decode_tok_per_s']:.3f}\n")

    # ---- human summary ----
    print("=== Phase 4 miss-path decomposition (decode, per step) ===")
    print(f"storage pread : {pread:8.0f} us  ({miss_path['storage_pread_mib_s']:.0f} MiB/s, "
          f"{dec['pread_bytes']/1e9:.2f} GB/step)")
    print(f"buffer prep   : {copy:8.0f} us")
    print(f"sync          : {sync:8.0f} us")
    print(f"kernel (MoE)  : {kernel:8.0f} us")
    print(f"trunk (route) : {route:8.0f} us")
    print(f"build resid   : {build:8.0f} us")
    print(f"TOTAL         : {total:8.0f} us  ({1e6/total:.2f} tok/s uncached)")
    print()
    print("=== Projected decode tok/s (global LRU steady) ===")
    print(f"{'GB':>5} {'hit%':>6} {'t_step_us':>10} {'tok/s':>8}")
    for p in proj:
        print(f"{p['capacity_gb']:>5} {p['global_lru_steady_hit_rate']*100:>6.1f} "
              f"{p['t_step_us']:>10.0f} {p['decode_tok_per_s']:>8.2f}")
    print()
    print(f"Phase 2 Metal budget: {METAL_WORKING_SET_GIB} GiB full-model working set; "
          f"~{METAL_CACHE_HEADROOM_GB} GB expert-cache headroom at 8K ctx.")
    print(f"Wrote benchmarks/results/phase-04-miss-path.json and "
          f"benchmarks/results/phase-04-projection.csv")


if __name__ == "__main__":
    main()
