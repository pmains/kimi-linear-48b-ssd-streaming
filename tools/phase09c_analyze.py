#!/usr/bin/env python3
"""Phase 9C analysis: pipeline decomposition + overlap bounds.

Consumes the instrumented Phase 9C artifacts:
  <outdir>/p9c-layer.csv     per-(step, layer) phase timings (route/readback/
                             load split/compute; kinds pre|dense|moe|epi)
  <outdir>/p9c-layer.csv.preads  per-pread records (callseq,il,kind,off,len,us)
  <outdir>/stats.csv         existing per-step aggregates (validation)

and optionally the I/O probe results (JSON) for the parallel-bandwidth inputs.

Writes <outdir>/p9c-analysis.json with:
  - per-step serial-chain decomposition (current)
  - per-step phase aggregation (absolute ms and % of decode wall)
  - I/O workload characterization (reads/token, bytes/token, size/latency
    distributions, offset scatter, achieved bandwidth)
  - perfect-overlap bound (hide all pread)
  - dependency-constrained bound (parallel pread at measured ceiling BW +
    pipelined repack behind the read tail, per the probe)
  - predicted tok/s under current / perfect / dependency-constrained
"""
import argparse
import csv
import json
import os
import statistics
from collections import defaultdict

MiB = 1048576.0


def load_layers(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            if r["step"].startswith("#"):
                continue
            rows.append({k: int(v) if k not in ("kind",) else v
                         for k, v in r.items()})
    return rows


def load_preads(path):
    recs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split(",")
            if len(p) != 6:
                continue
            recs.append({"seq": int(p[0]), "il": int(p[1]), "kind": p[2],
                         "off": int(p[3]), "len": int(p[4]), "us": int(p[5])})
    return recs


def load_stats(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            if r["step"].startswith("#") or r["step"].startswith("step"):
                continue
            rows.append({k: (v if k in ("phase",) else int(v))
                         for k, v in r.items()})
    return rows


def pct(x, total):
    return 100.0 * x / total if total else 0.0


def analyze(outdir, probe=None):
    layer_path = os.path.join(outdir, "p9c-layer.csv")
    pread_path = layer_path + ".preads"
    stats_path = os.path.join(outdir, "stats.csv")
    layers = load_layers(layer_path)
    preads = load_preads(pread_path)
    stats = load_stats(stats_path)

    # ---- group layers by step ----
    steps = defaultdict(list)
    for r in layers:
        steps[r["step"]].append(r)

    decode_step_nums = [r["step"] for r in stats if r["phase"] == "decode"]
    decode_steps = sorted(s for s in decode_step_nums if s in steps)

    per_step = {}
    for s in sorted(steps):
        rows = steps[s]
        pre = next((r for r in rows if r["kind"] == "pre"), None)
        epi = next((r for r in rows if r["kind"] == "epi"), None)
        moe = [r for r in rows if r["kind"] == "moe"]
        dense = [r for r in rows if r["kind"] == "dense"]
        ag = {
            "step": s,
            "preamble_us": pre["route_us"] if pre else 0,
            "epilogue_us": epi["compute_us"] if epi else 0,
            "n_moe": len(moe),
            "route_us": sum(r["route_us"] for r in moe + dense),
            "readback_us": sum(r["readback_us"] for r in moe + dense),
            "load_wall_us": sum(r["load_wall_us"] for r in moe),
            "pread_us": sum(r["pread_us"] for r in moe),
            "repack_us": sum(r["repack_us"] for r in moe),
            "placement_us": sum(r["placement_us"] for r in moe),
            "sync_us": sum(r["sync_us"] for r in moe),
            "load_other_us": sum(r["load_other_us"] for r in moe),
            "compute_us": sum(r["compute_us"] for r in moe),
            "lookups": sum(r["lookups"] for r in moe),
            "hits": sum(r["hits"] for r in moe),
            "misses": sum(r["misses"] for r in moe),
        }
        ag["serial_us"] = (ag["preamble_us"] + ag["epilogue_us"] +
                           ag["route_us"] + ag["readback_us"] +
                           ag["load_wall_us"] + ag["compute_us"])
        ag["measured_total_us"] = next((r["total_us"] for r in stats
                                        if r["step"] == s), None)
        per_step[s] = ag

    # ---- I/O characterization (decode only) ----
    io = {}
    # map pread seq ranges to steps using stats.csv cumulative pread calls:
    # trace_pread stamps seq = cumulative n_pread_calls after increment, so
    # records with cum_{s-1} < seq <= cum_s belong to step s.
    decode_step_nums = [r["step"] for r in stats if r["phase"] == "decode"]
    cum = 0
    step_cum = {}   # step -> (cum_before, cum_after)
    for r in stats:
        step_cum[r["step"]] = (cum, cum + r["pread_calls"])
        cum += r["pread_calls"]
    dec_recs = []
    for p in preads:
        for s in decode_step_nums:
            lo, hi = step_cum[s]
            if lo < p["seq"] <= hi:
                p = dict(p, step=s)
                dec_recs.append(p)
                break
    io_recs = dec_recs
    io["n_decode_steps"] = len(decode_step_nums)
    io["reads_per_token"] = (len(io_recs) / len(decode_step_nums)
                              if decode_step_nums else 0)
    io["bytes_per_token_mb"] = (sum(p["len"] for p in io_recs) /
                                 len(decode_step_nums) / MiB
                                 if decode_step_nums else 0)
    io["n_reads"] = len(io_recs)
    io["bytes"] = sum(p["len"] for p in io_recs)
    io["read_sizes"] = {
        "min": min((p["len"] for p in io_recs), default=0),
        "p50": int(statistics.median(sorted(p["len"] for p in io_recs))),
        "p90": sorted(p["len"] for p in io_recs)[
            int(len(io_recs) * 0.90)] if io_recs else 0,
        "max": max((p["len"] for p in io_recs), default=0),
    }
    lats = sorted(p["us"] for p in io_recs)
    io["lat_us"] = {
        "min": lats[0] if lats else 0,
        "p50": lats[int(len(lats) * 0.50)] if lats else 0,
        "p90": lats[int(len(lats) * 0.90)] if lats else 0,
        "p99": lats[int(len(lats) * 0.99)] if lats else 0,
        "max": lats[-1] if lats else 0,
        "mean": int(statistics.mean(lats)) if lats else 0,
    }
    io["total_pread_us"] = sum(p["us"] for p in io_recs)
    io["achieved_gbps"] = (io["bytes"] / 1e9) / (io["total_pread_us"] / 1e6) \
        if io["total_pread_us"] else 0
    # offset scatter: within each (il, kind) group, fraction of consecutive
    # reads (sorted by offset) that are contiguous or overlapping (gap <= 0,
    # i.e. mergeable without layout change) and near (gap <= 1 slice)
    groups = defaultdict(list)
    for p in io_recs:
        groups[(p["il"], p["kind"])].append(p["off"])
    gaps = []
    for g in groups.values():
        g = sorted(g)
        for a, b in zip(g, g[1:]):
            gaps.append(b - a)  # byte distance between starts
    io["n_offset_groups"] = len(groups)
    io["mergeable_frac"] = sum(1 for g in gaps if g <= 0) / len(gaps) \
        if gaps else 0
    io["near_1slice_frac"] = sum(
        1 for g in gaps if 0 < g <= io["read_sizes"]["p50"]) / len(gaps) \
        if gaps else 0
    io["near_4slice_frac"] = sum(
        1 for g in gaps if 0 < g <= 4 * io["read_sizes"]["p50"]) / len(gaps) \
        if gaps else 0

    # ---- per-step aggregates over decode ----
    dec = [per_step[s] for s in decode_steps]
    if not dec:
        print("no decode steps found")
        return
    n = len(dec)
    agg = {}
    for k in ("preamble_us", "epilogue_us", "route_us", "readback_us",
              "load_wall_us", "pread_us", "repack_us", "placement_us",
              "sync_us", "load_other_us", "compute_us", "serial_us"):
        agg[k + "_ms"] = statistics.median(d[k] for d in dec) / 1000.0
    agg["serial_us_mean"] = statistics.mean(d["serial_us"] for d in dec)
    total = agg["serial_us_mean"]
    agg["total_ms"] = total / 1000.0
    for k in ("preamble_us", "epilogue_us", "route_us", "readback_us",
              "pread_us", "repack_us", "placement_us", "sync_us",
              "load_other_us", "compute_us"):
        agg[k + "_pct"] = pct(statistics.mean(d[k] for d in dec), total)
    agg["n_decode_steps"] = n
    agg["decode_tokens"] = n
    agg["current_tokps"] = 1e6 / total if total else 0

    # ---- bounds ----
    # perfect overlap: hide all pread behind other work
    perfect_serial = statistics.mean(
        d["serial_us"] - d["pread_us"] for d in dec)
    agg["perfect_overlap_us"] = perfect_serial
    agg["perfect_tokps"] = 1e6 / perfect_serial
    agg["perfect_speedup"] = total / perfect_serial

    # dependency-constrained: parallel pread at probe ceiling BW; repack stays
    # serial after the read batch (it needs the bytes), but repack can stream
    # behind the read tail; conservative: pread_par + repack + placement + sync
    par_bw = None
    if probe and "par16_gbps" in probe:
        par_bw = probe["par16_gbps"]
    if probe and "full_decode" in probe and "par16_gbps" in probe["full_decode"]:
        par_bw = probe["full_decode"]["par16_gbps"]
    # fallback: scale by measured achievable
    if not par_bw:
        par_bw = max(io["achieved_gbps"] * 1.5, io["achieved_gbps"] + 1.0)
    dep_serial = statistics.mean(
        d["serial_us"] - d["pread_us"] +
        (d["pread_us"] * (io["achieved_gbps"] / par_bw if par_bw else 1.0))
        for d in dec)
    # per-step parallel pread estimate using the probe's per-step seq->par
    # ratios where available, else the full-schedule ratio
    step_par = {}
    if probe and "per_step" in probe:
        for sk, v in probe["per_step"].items():
            if v["seq_ms"] > 0:
                step_par[int(sk)] = v["par16_ms"] / v["seq_ms"]
    full_ratio = 1.0 / 2.26
    if probe and "seq_to_par_ratio_full" in probe:
        full_ratio = 1.0 / probe["seq_to_par_ratio_full"]
    dep_serial_step = statistics.mean(
        d["serial_us"] - d["pread_us"] + d["pread_us"] *
        step_par.get(d["step"], full_ratio)
        for d in dec)
    agg["dep_par_bw_gbps"] = par_bw
    agg["dep_constrained_us"] = dep_serial
    agg["dep_tokps"] = 1e6 / dep_serial
    agg["dep_speedup"] = total / dep_serial
    agg["dep_step_ratio_us"] = dep_serial_step
    agg["dep_step_ratio_tokps"] = 1e6 / dep_serial_step
    agg["dep_step_ratio_speedup"] = total / dep_serial_step
    # cold-bound variant: pread at rawseq/coalesce (true SSD) bandwidth
    cold_bw = 8.27
    if probe and "full_decode" in probe and "rawseq_gbps" in probe["full_decode"]:
        cold_bw = probe["full_decode"]["rawseq_gbps"]
    dep_cold = statistics.mean(
        d["serial_us"] - d["pread_us"] +
        d["pread_us"] * (io["achieved_gbps"] / cold_bw)
        for d in dec)
    agg["dep_cold_bw_gbps"] = cold_bw
    agg["dep_cold_us"] = dep_cold
    agg["dep_cold_tokps"] = 1e6 / dep_cold
    agg["dep_cold_speedup"] = total / dep_cold

    # also: perfect-with-parallel-io (pread at par BW, fully hidden where possible)
    pread_par_total = statistics.mean(
        d["pread_us"] * (io["achieved_gbps"] / par_bw if par_bw else 1.0)
        for d in dec)
    agg["pread_par_ms"] = pread_par_total / 1000.0

    out = {
        "io": io,
        "per_step": per_step,
        "decode_aggregate": agg,
        "probe_input": probe,
    }
    with open(os.path.join(outdir, "p9c-analysis.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)
    print(json.dumps(agg, indent=1, default=str))
    print("I/O:", json.dumps(io, indent=1, default=str))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--probe", default=None, help="probe JSON (optional)")
    a = ap.parse_args()
    probe = None
    if a.probe:
        with open(a.probe) as f:
            probe = json.load(f)
    analyze(a.outdir, probe)
