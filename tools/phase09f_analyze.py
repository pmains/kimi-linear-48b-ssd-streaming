#!/usr/bin/env python3
"""Phase 9F analysis: pipelined repack worker-count comparison.

Consumes the Phase 9F ladder layout:
  <root>/<config>/w1-before-W<W>/, <root>/<config>/w<W>/, <root>/<config>/w1-after-W<W>/
for config in {coding-cap4, reasoning-cap8, uncached} and W in {2,4,8}, plus
the correctness pair (w1/w4) when given.

For each (config, W): computes the workers=1 baseline as the mean of the two
contemporaneous bracketing runs, then reports:
  - decode tok/s (steady state after the 8-step warmup)
  - read wall ms/step (pread_wall_us) and pread syscall-sum ms/step
  - repack ms/step, placement ms/step
  - MEASURED overlap: hidden_us/step (stats.csv col 31) and per-layer
    load_hidden_us summed per step from the p9c trace
  - total load wall ms/step (sum of per-layer load_wall_us from p9c)
  - SSD traffic MB/token
  - CPU % (cpu.csv sampler) and memory (decode steady phys from mem.csv)
  - correctness: moe.csv md5 vs frozen Phase 8 baseline, retr.csv
    byte-identity vs the bracketing baseline

Also loads the Phase 9D ladder summary (--d9) for a 9F-vs-9D side-by-side,
and prints the 9E gate predictions for comparison.

Writes <root>/phase-09f-summary.json.
"""
import argparse
import csv
import hashlib
import json
import os
import statistics

WARMUP = 8  # steady-state decode starts here (Phase 6B convention)

# frozen Phase 8 baseline moe.csv md5s (Phase 9C report)
BASELINE_MD5 = {
    "coding-cap4":    "da45ab777b0fdd99be62f6c46a642e5c",
    "reasoning-cap8": "e26205ed69e8466120c9f199347ca2b3",
    "uncached":       None,
}

# Phase 9E gate predictions (ms/step saved by pipelined repack)
P9E_PREDICTION_MS = {
    "coding-cap4":    {2: 29.7, 4: 20.4, 8: 10.7},
    "reasoning-cap8": {2: 27.5, 4: 19.0, 8: 9.6},
    "uncached":       {2: 58.7, 4: 47.1, 8: 39.8},
}

CONFIGS = ["coding-cap4", "reasoning-cap8", "uncached"]
WORKERS = [2, 4, 8]


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_stats(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            if not r or not r.get("step") or r["step"].startswith("#"):
                continue
            try:
                rows.append({k: (v if k == "phase" else int(v)) for k, v in r.items()})
            except ValueError:
                continue
    return rows


def read_layers(path):
    rows = []
    if not path or not os.path.exists(path):
        return rows
    with open(path) as f:
        for r in csv.DictReader(f):
            if not r or not r.get("step") or r["step"].startswith("#"):
                continue
            try:
                rows.append({k: (v if k == "kind" else int(v)) for k, v in r.items()})
            except ValueError:
                continue
    return rows


def decode_steady(rows):
    return [r for r in rows if r["phase"] == "decode"][WARMUP:]


def run_metrics(dirpath):
    res = {"dir": dirpath, "error": None}
    sp = os.path.join(dirpath, "stats.csv")
    if not os.path.exists(sp):
        res["error"] = "no stats.csv"
        return res
    rows = read_stats(sp)
    dec = decode_steady(rows)
    if not dec:
        res["error"] = "no decode rows"
        return res
    tok = sum(r["n_tokens"] for r in dec)
    total_us = sum(r["total_us"] for r in dec)
    res["decode_steps"] = len(dec)
    res["decode_tokens"] = tok
    res["tok_per_s"] = tok * 1e6 / total_us if total_us else 0.0
    res["ms_per_step"] = total_us / len(dec) / 1000.0
    res["pread_wall_ms"] = sum(r.get("pread_wall_us", r["pread_us"]) for r in dec) / len(dec) / 1000.0
    res["pread_sum_ms"] = sum(r["pread_us"] for r in dec) / len(dec) / 1000.0
    res["repack_ms"] = sum(r["repack_us"] for r in dec) / len(dec) / 1000.0
    res["placement_ms"] = sum(r["placement_us"] for r in dec) / len(dec) / 1000.0
    res["hidden_ms"] = sum(r.get("hidden_us", 0) for r in dec) / len(dec) / 1000.0
    res["ssd_mb_per_token"] = sum(r["pread_bytes"] for r in dec) / 1048576.0 / tok
    res["hit_rate"] = (sum(r["cache_hits"] for r in dec) / sum(r["cache_lookups"] for r in dec)
                       if sum(r["cache_lookups"] for r in dec) else 0.0)
    # per-layer load wall + measured overlap from the p9c trace
    lp = os.path.join(dirpath, "p9c-layer.csv")
    layers = read_layers(lp)
    if layers:
        step_nums = {r["step"] for r in dec}
        moe = [r for r in layers if r["kind"] == "moe" and r["step"] in step_nums]
        if moe:
            res["load_wall_ms"] = sum(r["load_wall_us"] for r in moe) / len(dec) / 1000.0
            res["pread_wall_layer_ms"] = sum(r["pread_wall_us"] for r in moe) / len(dec) / 1000.0
            res["layer_hidden_ms"] = sum(r.get("load_hidden_us", 0) for r in moe) / len(dec) / 1000.0
            res["layers_with_overlap_pct"] = (
                100.0 * sum(1 for r in moe if r.get("load_hidden_us", 0) > 0) / len(moe))
    # memory
    mp = os.path.join(dirpath, "mem.csv")
    if os.path.exists(mp):
        phys = []
        with open(mp) as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                p = line.split(",")
                if len(p) >= 5 and p[1] == "end" and p[2] == "decode":
                    try:
                        phys.append(float(p[4]))
                    except ValueError:
                        pass
        if phys:
            res["decode_phys_mb"] = statistics.mean(phys)
    cp = os.path.join(dirpath, "cpu.csv")
    if os.path.exists(cp):
        vals = []
        with open(cp) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    vals.append(float(line))
                except ValueError:
                    continue
        if vals:
            res["cpu_pct"] = statistics.mean(vals)
    mpp = os.path.join(dirpath, "moe.csv")
    if os.path.exists(mpp):
        res["moe_md5"] = md5(mpp)
    rp = os.path.join(dirpath, "retr.csv")
    if os.path.exists(rp):
        res["retr_md5"] = md5(rp)
    return res


def compare_bytes(a, b):
    if not a or not b or not os.path.exists(a) or not os.path.exists(b):
        return None
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            ca, cb = fa.read(1 << 20), fb.read(1 << 20)
            if ca != cb:
                return False
            if not ca:
                return True


def load_d9(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--correctness", default="benchmarks/results/phase-09f/correctness")
    ap.add_argument("--d9", default="benchmarks/results/phase-09d/ladder/phase-09d-summary.json")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    d9 = load_d9(args.d9)
    out = {"configs": {}}
    for cfg in CONFIGS:
        base = os.path.join(args.root, cfg)
        entry = {"baseline_md5": BASELINE_MD5.get(cfg), "p9e_prediction_ms": P9E_PREDICTION_MS[cfg],
                 "workers": {}}
        for W in WORKERS:
            b1 = run_metrics(os.path.join(base, f"w1-before-W{W}"))
            b2 = run_metrics(os.path.join(base, f"w1-after-W{W}"))
            wr = run_metrics(os.path.join(base, f"w{W}"))
            e = {"W": W, "run": wr, "baseline1": b1, "baseline2": b2}
            if not b1.get("error") and not b2.get("error"):
                e["baseline_tok_per_s"] = (b1["tok_per_s"] + b2["tok_per_s"]) / 2.0
                e["baseline_ms_per_step"] = (b1["ms_per_step"] + b2["ms_per_step"]) / 2.0
                if wr.get("tok_per_s"):
                    e["speedup_vs_frozen"] = wr["tok_per_s"] / e["baseline_tok_per_s"]
                e["moe_matches_frozen"] = (BASELINE_MD5.get(cfg) is not None
                                           and wr.get("moe_md5") == BASELINE_MD5[cfg])
                e["moe_matches_baseline_md5"] = (wr.get("moe_md5") == b1.get("moe_md5")
                                                 and wr.get("moe_md5") == b2.get("moe_md5"))
                e["retr_identical_w1"] = compare_bytes(
                    os.path.join(base, f"w1-before-W{W}", "retr.csv"),
                    os.path.join(base, f"w{W}", "retr.csv"))
                # 9D comparison (same config/W, if the 9D summary exists)
                d9w = d9.get("configs", {}).get(cfg, {}).get("workers", {}).get(str(W), {})
                if d9w.get("run", {}).get("tok_per_s"):
                    e["d9_tok_per_s"] = d9w["run"]["tok_per_s"]
                    e["speedup_vs_d9"] = (wr["tok_per_s"] / d9w["run"]["tok_per_s"]
                                          if wr.get("tok_per_s") else None)
                # 9E prediction: expected hidden ms/step vs measured
                pred = P9E_PREDICTION_MS[cfg][W]
                e["p9e_prediction_ms"] = pred
                e["hidden_ms"] = wr.get("hidden_ms")
                e["layer_hidden_ms"] = wr.get("layer_hidden_ms")
                e["p9e_achieved_pct"] = (100.0 * (wr.get("hidden_ms") or 0) / pred
                                         if pred else None)
            entry["workers"][str(W)] = e
        if args.correctness and os.path.exists(os.path.join(args.correctness, "w1")):
            c1 = run_metrics(os.path.join(args.correctness, "w1"))
            c4 = run_metrics(os.path.join(args.correctness, "w4"))
            entry["correctness_pair"] = {
                "w1": c1, "w4": c4,
                "moe_md5_identical": c1.get("moe_md5") == c4.get("moe_md5"),
                "retr_identical": compare_bytes(
                    os.path.join(args.correctness, "w1", "retr.csv"),
                    os.path.join(args.correctness, "w4", "retr.csv")),
            }
        out["configs"][cfg] = entry

    for cfg, entry in out["configs"].items():
        print(f"\n=== {cfg} (frozen moe md5: {entry['baseline_md5'] or 'n/a'}) ===")
        hdr = (f"{'W':>2} | {'tok/s':>6} | {'base':>6} | {'vs9D':>6} | {'vsFrz':>6} | "
               f"{'ms/step':>7} | {'rdWall':>6} | {'repack':>6} | {'hidden':>6} | {'lHide':>6} | "
               f"{'loadW':>6} | {'MB/tok':>6} | {'CPU%':>5} | {'hit%':>5} | {'moe':>4} | {'retr':>4}")
        print(hdr)
        print("-" * len(hdr))
        for W in WORKERS:
            e = entry["workers"][str(W)]
            wr = e["run"]
            if wr.get("error"):
                print(f"{W:>2} | ERROR: {wr['error']}")
                continue
            vs9 = f"{e['speedup_vs_d9']:.2f}x" if e.get("speedup_vs_d9") else "n/a"
            vsf = f"{e['speedup_vs_frozen']:.2f}x" if e.get("speedup_vs_frozen") else "n/a"
            m = "Y" if e.get("moe_matches_frozen") else ("y" if e.get("moe_matches_baseline_md5") else "N")
            r = "Y" if e.get("retr_identical_w1") else "N"
            print(f"{W:>2} | {wr.get('tok_per_s',0):6.2f} | {e.get('baseline_tok_per_s',0):6.2f} | {vs9:>6} | {vsf:>6} | "
                  f"{wr.get('ms_per_step',0):7.1f} | {wr.get('pread_wall_ms',0):6.1f} | {wr.get('repack_ms',0):6.1f} | "
                  f"{wr.get('hidden_ms',0):6.1f} | {wr.get('layer_hidden_ms',0):6.1f} | "
                  f"{wr.get('load_wall_ms',0):6.1f} | {wr.get('ssd_mb_per_token',0):6.2f} | "
                  f"{wr.get('cpu_pct',0):5.0f} | {wr.get('hit_rate',0)*100:5.1f} | {m:>4} | {r:>4}")
    cp = out["configs"].get("coding-cap4", {}).get("correctness_pair")
    if cp:
        print("\n=== correctness pair (coding-cap4, w1 vs w4) ===")
        print(f"  w1: {cp['w1'].get('tok_per_s',0):.3f} tok/s  w4: {cp['w4'].get('tok_per_s',0):.3f} tok/s")
        print(f"  moe md5 identical: {cp['moe_md5_identical']}  retr identical: {cp['retr_identical']}")

    jp = args.json or os.path.join(args.root, "phase-09f-summary.json")
    with open(jp, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nwrote {jp}")


if __name__ == "__main__":
    main()
