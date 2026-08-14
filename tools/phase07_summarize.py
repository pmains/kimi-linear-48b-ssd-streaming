#!/usr/bin/env python3
"""Phase 7 run-dir summarizer + instrumentation validator.

Consumes one or more Phase 4/6/6B-style capture directories (stats.csv with
the Phase 7 columns, optional mem.csv, cache_layers.csv, retr.csv, moe.csv,
manifest.json) and produces:

  - a per-phase (prefill/decode) throughput and component-latency summary;
  - cache behavior with the Phase 7 hit-class split (zero-copy hits vs
    placement hits vs misses) — never a single collapsed hit rate;
  - per-layer cache capacity/occupancy from cache_layers.csv;
  - memory summary from mem.csv (prefill peak, decode steady phys_footprint);
  - SSD traffic per generated token;
  - validation of the instrumentation invariants (report + nonzero exit).

Usage:
    python3 tools/phase07_summarize.py DIR [DIR...] [--json OUT.json]
    python3 tools/phase07_summarize.py --ladder ROOT_DIR   # all rung dirs + summary.csv

Exit code 0 = all invariants hold, 1 = invariant violation found.
"""

import argparse
import csv
import json
import os
import sys

# stats.csv columns (1-indexed, Phase 7 layout)
C = {
    "step": 0, "phase": 1, "n_tokens": 2, "pread_calls": 3, "pread_bytes": 4,
    "pread_us": 5, "copy_us": 6, "sync_us": 7, "route_us": 8, "expert_us": 9,
    "build_us": 10, "total_us": 11, "n_slots": 12, "lookups": 13, "hits": 14,
    "misses": 15, "evictions": 16, "hit_bytes": 17, "cache_bytes_used": 18,
    "budget": 19, "repack_us": 20, "placement_us": 21, "repack_bytes": 22,
    "placement_bytes": 23, "zc_hits": 24, "ph_hits": 25, "zc_hit_bytes": 26,
    "build_measured_us": 27, "other_us": 28, "n_unique": 29,
}
NCOLS = 30

WARMUP_DECODE_STEPS = 8  # steady-state decode starts here (6B ladder convention)


def read_stats(path):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(",")
            if len(parts) < NCOLS:
                continue
            try:
                row = [int(x) for x in parts[:C["phase"]]]
                row.append(parts[C["phase"]])            # phase (string)
                row.extend(int(x) for x in parts[C["n_tokens"]:NCOLS])
            except ValueError:
                continue  # header row
            rows.append(row)
    return rows


def read_mem(path):
    rows = []
    if not path or not os.path.exists(path):
        return rows
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split(",")
            if len(p) < 7:
                continue
            try:
                row = {
                    "step": int(p[0]), "point": p[1], "phase": p[2],
                    "n_tokens": int(p[3]), "phys": float(p[4]), "resident": float(p[5]),
                    "malloc_in_use": float(p[6]),
                    "malloc_max": float(p[7]) if len(p) > 7 else 0.0,
                    "sched_cpu": float(p[8]) if len(p) > 8 else 0.0,
                }
            except ValueError:
                continue  # header row
            rows.append(row)
    return rows


def read_cache_layers(path):
    rows = []
    if not path or not os.path.exists(path):
        return rows
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split(",")
            if len(p) < 8:
                continue
            try:
                row = {
                    "step": int(p[0]), "il": int(p[1]), "cap": int(p[2]),
                    "used": int(p[3]), "occupied": int(p[4]),
                    "up": int(p[5]), "gate": int(p[6]), "down": int(p[7]),
                }
            except ValueError:
                continue  # header row
            rows.append(row)
    return rows


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def median(xs):
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def summarize_dir(d):
    stats = read_stats(os.path.join(d, "stats.csv"))
    mem = read_mem(os.path.join(d, "mem.csv"))
    cl = read_cache_layers(os.path.join(d, "cache_layers.csv"))

    if not stats:
        return {"dir": d, "error": "no stats.csv rows"}

    res = {"dir": d, "steps": len(stats), "invariants": {}}
    violations = []

    def phase_rows(phase):
        return [r for r in stats if r[C["phase"]] == phase]

    per_phase = {}
    for ph in ("prefill", "decode"):
        rs = phase_rows(ph)
        if not rs:
            continue
        totals = [r[C["total_us"]] for r in rs]
        tokens = sum(r[C["n_tokens"]] for r in rs)
        info = {
            "steps": len(rs),
            "tokens": tokens,
            "tok_per_s_mean": tokens * 1e6 / sum(totals) if totals else 0.0,
            "tok_per_s_median": 1e6 / median(totals) if totals else 0.0,
            "avg_total_ms": mean(totals) / 1000.0,
        }
        for key, col in [("pread_ms", "pread_us"), ("repack_ms", "repack_us"),
                         ("placement_ms", "placement_us"), ("copy_ms", "copy_us"),
                         ("sync_ms", "sync_us"), ("route_ms", "route_us"),
                         ("expert_ms", "expert_us"), ("build_ms", "build_us"),
                         ("build_measured_ms", "build_measured_us"),
                         ("other_ms", "other_us")]:
            info[key] = mean([r[C[col]] for r in rs]) / 1000.0
        per_phase[ph] = info

    # cache aggregates (decode steady + all steps)
    def cache_agg(rs):
        lookups = sum(r[C["lookups"]] for r in rs)
        hits = sum(r[C["hits"]] for r in rs)
        misses = sum(r[C["misses"]] for r in rs)
        zc = sum(r[C["zc_hits"]] for r in rs)
        ph_ = sum(r[C["ph_hits"]] for r in rs)
        ev = sum(r[C["evictions"]] for r in rs)
        return {
            "lookups": lookups, "hits": hits, "misses": misses,
            "zc_hits": zc, "ph_hits": ph_, "evictions": ev,
            "hit_rate": hits / lookups if lookups else 0.0,
            "zc_hit_rate": zc / lookups if lookups else 0.0,
            "ph_hit_rate": ph_ / lookups if lookups else 0.0,
            "pread_bytes": sum(r[C["pread_bytes"]] for r in rs),
            "repack_bytes": sum(r[C["repack_bytes"]] for r in rs),
            "placement_bytes": sum(r[C["placement_bytes"]] for r in rs),
            "hit_bytes": sum(r[C["hit_bytes"]] for r in rs),
            "zc_hit_bytes": sum(r[C["zc_hit_bytes"]] for r in rs),
            "unique_ranges": sum(r[C["n_unique"]] for r in rs),
        }

    dec = phase_rows("decode")
    steady = [r for r in dec if r[C["step"]] >= WARMUP_DECODE_STEPS]
    res["cache_all"] = cache_agg(stats)
    res["cache_decode_steady"] = cache_agg(steady if steady else dec)
    res["cache_bytes_used_mb"] = (stats[-1][C["cache_bytes_used"]] if stats else 0) / 1048576.0
    res["cache_budget_mb"] = (stats[-1][C["budget"]] if stats else 0) / 1048576.0

    # SSD traffic per generated token (decode steady)
    if steady:
        tok = sum(r[C["n_tokens"]] for r in steady)
        res["ssd_mb_per_token_decode"] = (
            sum(r[C["pread_bytes"]] for r in steady) / 1048576.0 / tok if tok else 0.0)
    else:
        res["ssd_mb_per_token_decode"] = 0.0

    # memory
    if mem:
        prefill_peaks = [m["phys"] for m in mem
                         if m["phase"] == "prefill" and m["point"] == "end"]
        dec_ends = [m["phys"] for m in mem
                    if m["phase"] == "decode" and m["point"] == "end"
                    and m["step"] >= WARMUP_DECODE_STEPS]
        res["mem_prefill_peak_phys_mb"] = max(prefill_peaks) if prefill_peaks else 0.0
        res["mem_decode_steady_phys_mb"] = mean(dec_ends) if dec_ends else 0.0
        res["mem_decode_steady_resident_mb"] = mean(
            [m["resident"] for m in mem if m["phase"] == "decode" and m["point"] == "end"
             and m["step"] >= WARMUP_DECODE_STEPS])
        res["mem_max_sched_mb"] = max((m["sched_cpu"] for m in mem), default=0.0)
    else:
        res["mem_prefill_peak_phys_mb"] = 0.0
        res["mem_decode_steady_phys_mb"] = 0.0

    # per-layer cache (last step of cache_layers.csv)
    layers = []
    if cl:
        last_step = max(r["step"] for r in cl)
        for r in cl:
            if r["step"] == last_step:
                layers.append({"il": r["il"], "cap_slots": r["cap"],
                               "used_slots": r["used"],
                               "occupied_bytes": r["occupied"]})
        res["cache_layers"] = layers
        res["cache_layers_total_cap"] = sum(r["cap_slots"] for r in layers if r["cap_slots"] > 0)
        res["cache_layers_total_used"] = sum(r["used_slots"] for r in layers)
        res["cache_layers_total_occupied_mb"] = sum(r["occupied_bytes"] for r in layers) / 1048576.0

    # ---- instrumentation invariants (per step) ----
    # zc mode mixes two paths within a step: layers whose unique count fits the
    # persistent capacity use zero-copy placement (placement == pread for those
    # layers); oversized prefill layers fall back to legacy placement (bulk
    # repack, placement 0). So the hard invariant in zc mode is
    # placement_bytes <= pread_bytes; a pure-zc step has exact equality.
    zc_mode = any(r[C["ph_hits"]] == 0 and r[C["zc_hits"]] > 0 for r in stats)
    bad = 0
    n_pure_zc_decode = 0
    n_mixed_steps = 0
    for i, r in enumerate(stats):
        l, h, m = r[C["lookups"]], r[C["hits"]], r[C["misses"]]
        if l != h + m:
            violations.append(f"step {r[C['step']]}: lookups {l} != hits {h} + misses {m}")
            bad += 1
        if h != r[C["zc_hits"]] + r[C["ph_hits"]]:
            violations.append(f"step {r[C['step']]}: hits {h} != zc {r[C['zc_hits']]} + ph {r[C['ph_hits']]}")
            bad += 1
        if r[C["copy_us"]] != r[C["repack_us"]] + r[C["placement_us"]]:
            violations.append(f"step {r[C['step']]}: copy_us {r[C['copy_us']]} != repack {r[C['repack_us']]} + placement {r[C['placement_us']]}")
            bad += 1
        if r[C["repack_bytes"]] != r[C["pread_bytes"]]:
            # a miss repacks exactly the bytes it read (both modes; legacy
            # bulk repack == read bytes)
            violations.append(f"step {r[C['step']]}: repack_bytes {r[C['repack_bytes']]} != pread_bytes {r[C['pread_bytes']]}")
            bad += 1
        if zc_mode:
            if r[C["placement_bytes"]] > r[C["pread_bytes"]]:
                violations.append(f"step {r[C['step']]}: zc placement_bytes {r[C['placement_bytes']]} > pread_bytes {r[C['pread_bytes']]}")
                bad += 1
            if r[C["zc_hits"]] > 0 and r[C["placement_bytes"]] == r[C["pread_bytes"]]:
                n_pure_zc_decode += 1
            elif r[C["zc_hits"]] > 0:
                n_mixed_steps += 1  # prefill fallback layers in the same step
        else:
            if r[C["zc_hit_bytes"]] != 0:
                violations.append(f"step {r[C['step']]}: placement mode reports zc_hit_bytes {r[C['zc_hit_bytes']]} != 0")
                bad += 1
        # measured components must not exceed the total
        comp = (r[C["pread_us"]] + r[C["copy_us"]] + r[C["sync_us"]]
                + r[C["route_us"]] + r[C["expert_us"]] + r[C["build_measured_us"]])
        if comp > r[C["total_us"]] + 1000:  # 1 ms slack for clock granularity
            violations.append(f"step {r[C['step']]}: component sum {comp} > total {r[C['total_us']]}")
            bad += 1
    res["zc_mode"] = zc_mode
    res["pure_zc_decode_steps"] = n_pure_zc_decode
    res["mixed_fallback_steps"] = n_mixed_steps
    res["invariants"]["violations"] = violations
    res["invariants"]["ok"] = bad == 0
    res["per_phase"] = per_phase
    return res


def print_summary(res):
    if "error" in res:
        print(f"  {res['dir']}: ERROR {res['error']}")
        return
    d = res["dir"]
    print(f"\n=== {d} ===")
    print(f"  zc_mode={res.get('zc_mode')}  steps={res['steps']}  "
          f"budget={res['cache_budget_mb']:.1f} MB  resident={res['cache_bytes_used_mb']:.1f} MB")
    for ph, info in res["per_phase"].items():
        print(f"  [{ph}] {info['steps']} steps / {info['tokens']} tok: "
              f"{info['tok_per_s_mean']:.3f} tok/s (median {info['tok_per_s_median']:.3f}), "
              f"{info['avg_total_ms']:.0f} ms/step")
        print(f"      pread {info['pread_ms']:.0f} | repack {info['repack_ms']:.0f} | "
              f"placement {info['placement_ms']:.0f} | copy {info['copy_ms']:.0f} | "
              f"sync {info['sync_ms']:.0f} | route(trunk) {info['route_ms']:.0f} | "
              f"expert {info['expert_ms']:.0f} | build {info['build_ms']:.0f} "
              f"(measured {info['build_measured_ms']:.0f}) | other {info['other_ms']:.0f} ms")
    ca = res["cache_all"]
    cs = res["cache_decode_steady"]
    print(f"  cache (all steps): {ca['lookups']} lookups, {ca['hits']} hits "
          f"({ca['zc_hits']} zc + {ca['ph_hits']} ph), {ca['misses']} misses, "
          f"{ca['evictions']} evictions; hit_rate {ca['hit_rate']:.3f} "
          f"(zc {ca['zc_hit_rate']:.3f}, ph {ca['ph_hit_rate']:.3f})")
    print(f"  cache (decode steady): hit_rate {cs['hit_rate']:.3f} "
          f"(zc {cs['zc_hit_rate']:.3f}), {cs['misses']} misses/step avg "
          f"{cs['misses'] / max(1, res['per_phase'].get('decode', {}).get('steps', 1)):.1f}")
    print(f"  bytes (all): pread {ca['pread_bytes']/1048576:.1f} MB, "
          f"repack {ca['repack_bytes']/1048576:.1f} MB, "
          f"placement {ca['placement_bytes']/1048576:.1f} MB, "
          f"zc_hit_elided {ca['zc_hit_bytes']/1048576:.1f} MB")
    print(f"  SSD: {res['ssd_mb_per_token_decode']:.2f} MB/token (decode steady)")
    print(f"  mem: prefill peak phys {res['mem_prefill_peak_phys_mb']:.0f} MB, "
          f"decode steady phys {res['mem_decode_steady_phys_mb']:.0f} MB, "
          f"sched max {res['mem_max_sched_mb']:.0f} MB")
    if res.get("cache_layers"):
        used = [r for r in res["cache_layers"] if r["used_slots"] > 0 or r["cap_slots"] > 0]
        print(f"  per-layer (last step): total cap {res['cache_layers_total_cap']} slots, "
              f"used {res['cache_layers_total_used']}, "
              f"occupied {res['cache_layers_total_occupied_mb']:.1f} MB; "
              f"layers with entries: {len(used)}/26")
        if used:
            caps = [r["cap_slots"] for r in used if r["cap_slots"] > 0]
            uses = [r["used_slots"] for r in used]
            capstr = f"cap range {min(caps)}..{max(caps)}, " if caps else "cap n/a (placement mode), "
            print(f"      {capstr}used range {min(uses)}..{max(uses)}")
    iv = res["invariants"]
    print(f"  invariants: {'PASS' if iv['ok'] else 'FAIL'} "
          f"({len(iv['violations'])} violation(s))")
    for v in iv["violations"][:8]:
        print(f"      ! {v}")


def ladder_mode(root):
    rungs = {}
    for name in sorted(os.listdir(root)):
        if not name.startswith("cap-") and name != "uncached":
            continue
        d = os.path.join(root, name)
        if os.path.isfile(os.path.join(d, "stats.csv")):
            res = summarize_dir(d)
            if "error" in res:
                continue
            key = "uncached" if name == "uncached" else name.replace("-a", "").replace("-b", "")
            rungs.setdefault(key, []).append((name, res))
    out_rows = []
    for key in sorted(rungs):
        runs = rungs[key]
        # min-of-2 convention: the least-throttled run per rung
        best = min(runs, key=lambda t: t[1]["per_phase"].get("decode", {}).get("avg_total_ms", 1e18))
        name, res = best
        dec = res["per_phase"].get("decode", {})
        cs = res["cache_decode_steady"]
        out_rows.append({
            "rung": key, "run": name, "tok_per_s": dec.get("tok_per_s_mean", 0.0),
            "avg_total_ms": dec.get("avg_total_ms", 0.0),
            "pread_ms": dec.get("pread_ms", 0.0), "repack_ms": dec.get("repack_ms", 0.0),
            "placement_ms": dec.get("placement_ms", 0.0), "copy_ms": dec.get("copy_ms", 0.0),
            "sync_ms": dec.get("sync_ms", 0.0), "route_ms": dec.get("route_ms", 0.0),
            "expert_ms": dec.get("expert_ms", 0.0), "build_ms": dec.get("build_ms", 0.0),
            "hit_rate": cs["hit_rate"], "zc_hit_rate": cs["zc_hit_rate"],
            "ph_hit_rate": cs["ph_hit_rate"], "lookups": cs["lookups"],
            "hits": cs["hits"], "zc_hits": cs["zc_hits"], "ph_hits": cs["ph_hits"],
            "misses": cs["misses"], "evictions": cs["evictions"],
            "ssd_mb_per_token": res["ssd_mb_per_token_decode"],
            "cache_mb": res["cache_bytes_used_mb"], "budget_mb": res["cache_budget_mb"],
            "phys_decode_mb": res["mem_decode_steady_phys_mb"],
            "prefill_peak_mb": res["mem_prefill_peak_phys_mb"],
            "zc_mode": res["zc_mode"],
            "invariants_ok": res["invariants"]["ok"],
        })
    path = os.path.join(root, "ladder-summary.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()) if out_rows else [])
        w.writeheader()
        w.writerows(out_rows)
    print(f"\nwrote {path}")
    if out_rows:
        print(f"  {'rung':>6} {'tok/s':>7} {'tot':>5} {'pread':>6} {'repack':>6} {'place':>6} "
              f"{'route':>6} {'expert':>6} {'build':>6} {'hit':>5} {'zc':>5} {'misses':>6} "
              f"{'SSD MB/t':>8} {'phys MB':>7}")
        for r in out_rows:
            print(f"  {r['rung']:>6} {r['tok_per_s']:>7.2f} {r['avg_total_ms']:>5.0f} "
                  f"{r['pread_ms']:>6.0f} {r['repack_ms']:>6.0f} {r['placement_ms']:>6.0f} "
                  f"{r['route_ms']:>6.0f} {r['expert_ms']:>6.0f} {r['build_ms']:>6.0f} "
                  f"{r['hit_rate']:>5.3f} {r['zc_hit_rate']:>5.3f} {r['misses']:>6.0f} "
                  f"{r['ssd_mb_per_token']:>8.2f} {r['phys_decode_mb']:>7.0f}")
    return out_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("--ladder", metavar="ROOT")
    ap.add_argument("--json", metavar="OUT")
    args = ap.parse_args()

    ok = True
    if args.ladder:
        rows = ladder_mode(args.ladder)
        ok = all(r["invariants_ok"] for r in rows)
        if args.json:
            with open(args.json, "w") as f:
                json.dump(rows, f, indent=2)
            print(f"wrote {args.json}")
    else:
        summaries = []
        for d in args.dirs:
            res = summarize_dir(d)
            print_summary(res)
            ok = ok and res.get("invariants", {}).get("ok", False)
            summaries.append(res)
        if args.json:
            with open(args.json, "w") as f:
                json.dump(summaries, f, indent=2)
            print(f"wrote {args.json}")
    print(f"\ninvariant check: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
