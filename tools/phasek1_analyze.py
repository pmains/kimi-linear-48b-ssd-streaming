#!/usr/bin/env python3
"""Phase K1 analyzer: MXFP4 vs frozen Q4_K_M control under the frozen 9G
bracket protocol.

Preserves the frozen 9G statistical structure and gates:
  - A->B->A bracket execution, seeded order verification
  - paired speedup S_i = tok/s(B_i) / mean(tok/s(A_before,i), tok/s(A_after,i))
  - bootstrap 95% CI of median S, A-spread disclosure, position-bias /
    warmup / drift checks, env covariates, raw artifact retention
  - phase07 per-step invariants on every run

Scoped differently for cross-MODEL comparison (documented deviation,
K1 report): byte-identity / frozen-baseline checks apply to A runs only
(A = Q4_K_M must reproduce the frozen routing and retrieval traces). B
(MXFP4) is checked for presence, clean logs, and invariants; its moe.csv
divergence from the A baseline is reported as a routing-change metric
(quality-relevant), not a failure.

Adds K1-specific per-run metrics from the Phase 4/7 artifacts:
  - SSD MB/token (pread bytes / decode tokens)
  - decode cache hit rate
  - decode steady resident MB (mem.csv)
  - cpu_pct (driver sampler)
  - model provenance (k1-model.json sidecar)
"""
import argparse
import csv
import json
import math
import os
import statistics
import subprocess
import sys

PHASE07 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phase07_summarize.py")
WARMUP = 3  # frozen 9G convention: skip first WARMUP decode rows

FROZEN_BASELINE_MD5 = {
    "coding-cap4": "da45ab777b0fdd99be62f6c46a642e5c",
    "reasoning-cap8": "e26205ed69e8466120c9f199347ca2b3",
    "uncached": None,
}
EXPECTED_ARTIFACTS = [
    "stats.csv", "retr.csv", "moe.csv", "mem.csv", "cache_layers.csv",
    "act.bin", "manifest.json",
]


def md5(path):
    import hashlib
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


def read_mem_csv(path):
    """decode-steady resident MB from the phase-07 mem log (if present)."""
    vals = []
    with open(path) as f:
        for r in csv.DictReader(f):
            if not r or not r.get("step"):
                continue
            if r.get("phase") == "decode":
                try:
                    vals.append(float(r["resident_mb"]))
                except (ValueError, KeyError):
                    pass
    return statistics.median(vals) if vals else None


def run_metrics(dirpath):
    res = {"dir": dirpath, "error": None}
    sp = os.path.join(dirpath, "stats.csv")
    if not os.path.exists(sp):
        res["error"] = "no stats.csv"
        return res
    rows = read_stats(sp)
    dec = [r for r in rows if r["phase"] == "decode"][WARMUP:]
    if not dec:
        res["error"] = "no steady decode rows"
        return res
    tok = sum(r["n_tokens"] for r in dec)
    total_us = sum(r["total_us"] for r in dec)
    res["decode_steps"] = len(dec)
    res["decode_tokens"] = tok
    res["tok_per_s"] = tok * 1e6 / total_us if total_us else 0.0
    res["ms_per_step"] = total_us / len(dec) / 1000.0
    res["pread_wall_ms"] = sum(r.get("pread_wall_us", r["pread_us"]) for r in dec) / len(dec) / 1000.0
    pread_bytes = sum(r.get("pread_bytes", 0) for r in dec)
    res["ssd_mb_per_token"] = (pread_bytes / tok / (1024 * 1024)) if tok else None
    hits = sum(r.get("cache_hits", 0) for r in dec)
    lookups = sum(r.get("cache_lookups", 0) for r in dec)
    res["decode_hit_rate"] = (hits / lookups) if lookups else None
    mp = os.path.join(dirpath, "mem.csv")
    if os.path.exists(mp):
        res["resident_decode_mb"] = read_mem_csv(mp)
    res["moe_md5"] = md5(os.path.join(dirpath, "moe.csv")) if os.path.exists(os.path.join(dirpath, "moe.csv")) else None
    res["retr_md5"] = md5(os.path.join(dirpath, "retr.csv")) if os.path.exists(os.path.join(dirpath, "retr.csv")) else None
    k1p = os.path.join(dirpath, "k1-model.json")
    if os.path.exists(k1p):
        try:
            with open(k1p) as f:
                res["model"] = json.load(f)
        except json.JSONDecodeError:
            res["model"] = {"error": "unparseable k1-model.json"}
    mp2 = os.path.join(dirpath, "manifest.json")
    if os.path.exists(mp2):
        try:
            with open(mp2) as f:
                res["manifest"] = json.load(f)
        except json.JSONDecodeError:
            res["manifest"] = {"error": "unparseable"}
    cp = os.path.join(dirpath, "cpu.csv")
    if os.path.exists(cp):
        vals = []
        with open(cp) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        vals.append(float(line))
                    except ValueError:
                        pass
        if vals:
            res["cpu_pct"] = statistics.mean(vals)
    return res


def bytes_identical(paths):
    if not paths or any(not os.path.exists(p) for p in paths):
        return None
    with open(paths[0], "rb") as ref:
        ref_data = ref.read()
    for p in paths[1:]:
        with open(p, "rb") as f:
            if f.read() != ref_data:
                return False
    return True


def invariant_check(dirpath):
    try:
        r = subprocess.run(
            [sys.executable, PHASE07, dirpath, "--json",
             os.path.join(dirpath, "..", ".invariant-%s.json" % os.path.basename(dirpath))],
            capture_output=True, text=True, timeout=180)
        ok = "invariants: PASS" in r.stdout or "all invariants hold" in r.stdout.lower()
        violations = None
        for line in r.stdout.splitlines():
            if "violation" in line.lower():
                violations = line.strip()
        return ok, violations, r.returncode
    except Exception as e:
        return False, str(e), -1


def bootstrap_ci(xs, n_resamples=10000, alpha=0.05, seed=9):
    import random
    rng = random.Random(seed)
    if len(xs) < 2:
        return None
    med = statistics.median
    boot = []
    for _ in range(n_resamples):
        sample = [rng.choice(xs) for _ in range(len(xs))]
        boot.append(med(sample))
    boot.sort()
    lo = boot[int(round(alpha / 2 * len(boot)))]
    hi = boot[int(round((1 - alpha / 2) * len(boot)))]
    return [lo, hi]


def sign_test(xs):
    pos = sum(1 for x in xs if x > 0)
    n = len(xs)
    if n == 0:
        return 1.0
    from math import comb
    p = sum(comb(n, k) for k in range(pos, n + 1)) / (2 ** n)
    return min(p, 1.0)


def linear_slope(xs):
    n = len(xs)
    if n < 2:
        return None
    mx = statistics.mean(range(n))
    my = statistics.mean(xs)
    num = sum((i - mx) * (y - my) for i, y in enumerate(xs))
    den = sum((i - mx) ** 2 for i in range(n))
    return num / den if den else None


def moe_divergence(a_path, b_path):
    """Fraction of B moe.csv data rows differing from A (routing change).

    moe.csv's first line is a '# ' comment that doubles as the header
    row, so the '# ' prefix is stripped and the line kept as
    fieldnames; rows are filtered on the real 'phase' column (moe.csv
    has no 'step' column).
    """
    def load(p):
        rows = []
        with open(p) as f:
            lines = f.readlines()
        if lines and lines[0].startswith("# "):
            lines[0] = lines[0][2:]
        for r in csv.DictReader(lines):
            if r and r.get("phase") and not r["phase"].startswith("#"):
                rows.append(tuple(r.values()))
        return rows
    a, b = load(a_path), load(b_path)
    if not a or len(a) != len(b):
        return None
    diff = sum(1 for x, y in zip(a, b) if x != y)
    return diff / len(a)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", nargs="?", default="benchmarks/results/phase-k1/brackets")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    harness = {}
    hp = os.path.join(args.outdir, "harness.json")
    if os.path.exists(hp):
        with open(hp) as f:
            harness = json.load(f)
    config = args.config or harness.get("config", "coding-cap4")
    n_brackets = int(harness.get("n_brackets", 4))
    workers = int(harness.get("workers", 1))
    bracket_orders = harness.get("bracket_orders")
    a_model = harness.get("a_model", "?")
    b_model = harness.get("b_model", "?")

    base = os.path.join(args.outdir, config)
    checks = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    # --- 1. bracket execution ---
    brackets = []
    exec_errors = []
    for i in range(1, n_brackets + 1):
        names = {"A_before": f"b{i}-A-before", "B": f"b{i}-B", "A_after": f"b{i}-A-after"}
        entry = {"index": i, "dirs": names}
        for role, d in names.items():
            m = run_metrics(os.path.join(base, d))
            entry[role] = m
            if m.get("error"):
                exec_errors.append(f"{d}: {m['error']}")
        for role in ("A_before", "B", "A_after"):
            mf = entry[role].get("manifest") or {}
            entry[role]["workers_ok"] = (mf.get("read_workers") == workers)
        brackets.append(entry)
    check("bracket execution", not exec_errors and len(brackets) == n_brackets,
          "; ".join(exec_errors) or f"{n_brackets} brackets x 3 runs complete")
    worker_fail = [f"b{e['index']}-{role}" for e in brackets for role in ("A_before", "B", "A_after")
                   if not e[role].get("workers_ok")]
    check("manifest worker counts", not worker_fail, f"workers={workers}; bad: {','.join(worker_fail) or 'none'}")

    # --- 1b. recorded order vs driver-log order ---
    order_fail, order_notes = [], []
    for e in brackets:
        i = e["index"]
        rec = bracket_orders[i - 1] if bracket_orders and i - 1 < len(bracket_orders) else None
        if not rec:
            e["order_verified"] = "n/a (no recorded order)"
            order_notes.append(f"b{i}: n/a")
            continue
        mtimes = {}
        for slot in ("A-before", "B", "A-after"):
            dl = os.path.join(base, f"b{i}-{slot}.driver.log")
            if os.path.exists(dl):
                mtimes[slot] = os.path.getmtime(dl)
        if len(mtimes) < 3:
            e["order_verified"] = f"skipped ({3 - len(mtimes)} driver log(s) missing)"
            order_notes.append(f"b{i}: skipped")
            continue
        by_time = [s for s, _ in sorted(mtimes.items(), key=lambda kv: kv[1])]
        if by_time == rec:
            e["order_verified"] = "match"
        else:
            e["order_verified"] = f"MISMATCH recorded={rec} actual={by_time}"
            order_fail.append(f"b{i} recorded={rec} actual={by_time}")
    check("recorded bracket order matches execution order", not order_fail,
          "; ".join(order_notes) or "all brackets match")

    # --- 2. paired speedup ---
    speedups, a_means, a_spread = [], [], []
    for e in brackets:
        ab = e["A_before"]["tok_per_s"]
        aa = e["A_after"]["tok_per_s"]
        b = e["B"]["tok_per_s"]
        am = (ab + aa) / 2.0
        s = b / am if am else None
        e["paired_speedup"] = s
        e["a_spread_rel"] = abs(ab - aa) / am if am else None
        speedups.append(s)
        a_means.append(am)
        a_spread.append(e["a_spread_rel"])
    for e in brackets:
        i = e["index"]
        rec = bracket_orders[i - 1] if bracket_orders and i - 1 < len(bracket_orders) else None
        if rec and "B" in rec:
            e["b_position"] = ("first" if rec.index("B") == 0
                               else "last" if rec.index("B") == 2 else "middle")
        else:
            e["b_position"] = "middle"
    valid_s = [s for s in speedups if s is not None]
    med_s = q = ci = None
    if valid_s:
        med_s = statistics.median(valid_s)
        q = statistics.quantiles(valid_s, n=4) if len(valid_s) >= 4 else None
        ci = bootstrap_ci(valid_s)
    s_by_position = {}
    for pos in ("first", "middle", "last"):
        vals = [s for e, s in zip(brackets, speedups) if e["b_position"] == pos and s is not None]
        if vals:
            s_by_position[pos] = {"n": len(vals), "median": statistics.median(vals),
                                  "values": [round(v, 4) for v in vals]}
    speedup_stats = {
        "n": len(valid_s), "median": med_s, "q1_q3": q,
        "min": min(valid_s) if valid_s else None,
        "max": max(valid_s) if valid_s else None,
        "bootstrap95_ci_of_median": ci, "per_bracket": speedups,
        "b_positions": [e["b_position"] for e in brackets],
        "s_by_position": s_by_position,
    }
    check("paired speedup computed", len(valid_s) == n_brackets,
          "median S=%.3f, per-bracket %s" % (med_s or 0, [round(s, 3) if s else None for s in speedups]))

    # --- 3. correctness / invariants (A-only identity; B re-scoped) ---
    baseline = FROZEN_BASELINE_MD5.get(config)
    a_retr = [os.path.join(base, e["dirs"][r], "retr.csv") for e in brackets for r in ("A_before", "A_after")]
    retr_a_ident = bytes_identical(a_retr)
    moe_a_bad, retr_missing, moe_skipped = [], [], []
    harness_n = int(harness.get("n_tokens", 0))
    for e in brackets:
        for role in e["dirs"]:
            m = e[role]
            if role == "A_before" or role == "A_after":
                if baseline is not None and m.get("moe_md5") != baseline:
                    if harness_n and m.get("decode_tokens") != harness_n:
                        moe_skipped.append(f"b{e['index']}-{role}(len {m.get('decode_tokens')} != {harness_n})")
                    else:
                        moe_a_bad.append(f"b{e['index']}-{role}")
            if m.get("retr_md5") is None:
                retr_missing.append(f"b{e['index']}-{role}")
    check("A moe md5 == frozen baseline", not moe_a_bad,
          "baseline=%s; bad: %s; length-skipped: %s" % (baseline, ",".join(moe_a_bad) or "none",
                                                          ",".join(moe_skipped) or "none"))
    check("A retr byte-identity (A runs only)", retr_a_ident is True,
          "identical" if retr_a_ident else ("FAIL" if retr_a_ident is False else "n/a"))
    check("retr present everywhere", not retr_missing, ",".join(retr_missing) or "all present")

    # B routing divergence (quality-relevant; reported, not a gate)
    route_div = []
    for e in brackets:
        a0 = os.path.join(base, e["dirs"]["A_before"], "moe.csv")
        b0 = os.path.join(base, e["dirs"]["B"], "moe.csv")
        if os.path.exists(a0) and os.path.exists(b0):
            route_div.append(moe_divergence(a0, b0))
    route_div = [d for d in route_div if d is not None]

    inv_ok, inv_fail = [], []
    for e in brackets:
        for role in e["dirs"]:
            d = os.path.join(base, e["dirs"][role])
            ok, violations, rc = invariant_check(d)
            e[role]["invariants_ok"] = ok
            e[role]["invariants_violations"] = violations
            e[role]["invariants_exit"] = rc
            (inv_ok if ok else inv_fail).append(f"b{e['index']}-{role}")
    check("phase07 per-step invariants", not inv_fail,
          f"{len(inv_ok)}/{len(inv_ok)+len(inv_fail)} PASS; bad: {','.join(inv_fail) or 'none'}")

    log_bad = []
    for e in brackets:
        for role in e["dirs"]:
            lp = os.path.join(base, e["dirs"][role], "run.log")
            if os.path.exists(lp):
                txt = open(lp, errors="replace").read()
                if any(w in txt.lower() for w in ("error:", "assert", "abort", "failed")):
                    log_bad.append(f"b{e['index']}-{role}")
    check("run.log clean", not log_bad, ",".join(log_bad) or "no error/assert/abort markers")

    # --- 4. raw artifacts ---
    missing = []
    for e in brackets:
        for role in e["dirs"]:
            d = os.path.join(base, e["dirs"][role])
            for a in EXPECTED_ARTIFACTS:
                if not os.path.exists(os.path.join(d, a)):
                    missing.append(f"{os.path.basename(d)}:{a}")
    check("raw artifacts retained", not missing,
          ",".join(missing) or f"all artifacts in all {n_brackets * 3} runs")

    # --- 5. env covariates ---
    env_files = [f for i in range(1, n_brackets + 1) for f in
                 (os.path.join(base, "env", f"b{i}-before.json"),
                  os.path.join(base, "env", f"b{i}-after.json"))]
    env_bad = [f for f in env_files if not os.path.exists(f)]
    env_data = {}
    for f in env_files:
        if os.path.exists(f):
            try:
                with open(f) as fh:
                    env_data[os.path.basename(f)] = json.load(fh)
            except json.JSONDecodeError:
                env_bad.append(f + " (unparseable)")
    check("env covariates captured", not env_bad,
          ",".join(env_bad) or f"{len(env_files)} snapshots ({len(env_files)//2} brackets x before/after)")
    cov_summary = []
    for name, snap in env_data.items():
        mp_free = None
        for line in (snap.get("memory_pressure") or "").splitlines():
            if "free percentage" in line:
                try:
                    mp_free = float(line.split()[-1].rstrip("%"))
                except ValueError:
                    pass
        live_ok = '"ok"' in (snap.get("live_server_health") or "")
        cov_summary.append({"snapshot": name, "memory_free_pct": mp_free,
                            "live_server_ok": live_ok, "loadavg": snap.get("loadavg"),
                            "thermal": snap.get("cpu_thermal_level")})

    # --- 6. position bias / warmup / drift ---
    a_before = [e["A_before"]["tok_per_s"] for e in brackets]
    a_after = [e["A_after"]["tok_per_s"] for e in brackets]
    deltas = [b - a for a, b in zip(a_before, a_after)]
    rel_deltas = [d / a for d, a in zip(deltas, a_before) if a]
    st = sign_test(deltas)
    pos_bias = {
        "a_before": a_before, "a_after": a_after,
        "mean_delta_rel": (statistics.mean(rel_deltas) if rel_deltas else None),
        "sign_test_p": st,
        "flag": (st < 0.05 and all(d > 0 for d in deltas)) or (st < 0.05 and all(d < 0 for d in deltas)),
    }
    check("no position bias (A_before vs A_after)", not pos_bias["flag"],
          f"mean delta {pos_bias['mean_delta_rel']*100:+.2f}% rel, sign test p={st:.3f}")

    a_all = [e[role]["tok_per_s"] for e in brackets for role in ("A_before", "A_after")]
    first_mean = statistics.mean([e["A_before"]["tok_per_s"] for e in brackets[:1]] +
                                 [e["A_after"]["tok_per_s"] for e in brackets[:1]])
    rest_mean = (statistics.mean([e[role]["tok_per_s"] for e in brackets[1:]
                                  for role in ("A_before", "A_after")]) if n_brackets > 1 else None)
    warm = (rest_mean is not None and abs(first_mean - rest_mean) / rest_mean > 0.10)
    warmup_check = {
        "first_bracket_A_mean": first_mean, "rest_A_mean": rest_mean,
        "rel_diff": ((first_mean - rest_mean) / rest_mean) if rest_mean else None, "flag": warm,
    }
    check("no warmup artifact (first bracket vs rest)", not warm,
          (f"first {first_mean:.3f} vs rest {rest_mean:.3f} tok/s "
           f"({warmup_check['rel_diff']*100:+.1f}% rel)") if rest_mean else "n/a (single bracket)")

    a_means_by_index = [statistics.mean([e["A_before"]["tok_per_s"], e["A_after"]["tok_per_s"]])
                        for e in brackets]
    slope = linear_slope(a_means_by_index)
    drift = slope / statistics.mean(a_means_by_index) if slope is not None and a_means_by_index else None
    drift_check = {"slope_tok_per_s_per_bracket": slope, "rel_per_bracket": drift,
                   "flag": drift is not None and abs(drift) > 0.05}
    check("no strong drift across brackets", not drift_check["flag"],
          f"slope {slope:.4f} tok/s per bracket ({drift*100:+.2f}% rel/bracket)" if slope is not None else "slope n/a")

    # --- K1 model-level metrics (medians over runs) ---
    def med_of(role, key):
        vals = [e[role][key] for e in brackets if e[role].get(key) is not None]
        return statistics.median(vals) if vals else None

    k1_metrics = {
        "A": {
            "tok_per_s": med_of("A_before", "tok_per_s"),
            "ssd_mb_per_token": med_of("A_before", "ssd_mb_per_token"),
            "decode_hit_rate": med_of("A_before", "decode_hit_rate"),
            "resident_decode_mb": med_of("A_before", "resident_decode_mb"),
            "cpu_pct": med_of("A_before", "cpu_pct"),
        },
        "B": {
            "tok_per_s": med_of("B", "tok_per_s"),
            "ssd_mb_per_token": med_of("B", "ssd_mb_per_token"),
            "decode_hit_rate": med_of("B", "decode_hit_rate"),
            "resident_decode_mb": med_of("B", "resident_decode_mb"),
            "cpu_pct": med_of("B", "cpu_pct"),
        },
        "route_divergence_fraction": route_div,
        "a_model": a_model,
        "b_model": b_model,
    }

    # --- Assemble ---
    all_ok = all(c["ok"] for c in checks)
    summary = {
        "harness": harness, "config": config, "workers": workers,
        "mode": harness.get("mode", "model-ab"),
        "bracket_orders": bracket_orders,
        "checks": checks,
        "speedup": speedup_stats,
        "a_spread_rel": a_spread,
        "a_cv_pct": (statistics.stdev(a_all) / statistics.mean(a_all) * 100 if len(a_all) > 1 else None),
        "position_bias": pos_bias, "warmup_check": warmup_check, "drift_check": drift_check,
        "covariates": cov_summary,
        "k1_metrics": k1_metrics,
        "brackets": [{k: (v if k == "dirs" else
                      ({kk: vv for kk, vv in v.items() if kk in
                        ("tok_per_s", "ms_per_step", "pread_wall_ms", "ssd_mb_per_token",
                         "decode_hit_rate", "resident_decode_mb", "moe_md5", "retr_md5",
                         "workers_ok", "invariants_ok", "cpu_pct", "model", "error")}
                       if isinstance(v, dict) else v))
                      for k, v in e.items()} for e in brackets],
    }
    jp = os.path.join(args.outdir, f"phase-k1-{config}-summary.json")
    with open(jp, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    # --- Print ---
    print(f"=== Phase K1 analysis: {config} (A=Q4_K_M frozen, B=MXFP4, workers={workers}) ===")
    print(f"    A model: {os.path.basename(a_model)}")
    print(f"    B model: {os.path.basename(b_model)}")
    print(f"    bracket order: {[e['b_position'] for e in brackets]} (recorded seed {harness.get('seed')})")
    print(f"{'bracket':>7} | {'A_before':>8} | {'B':>8} | {'A_after':>8} | {'S_i':>6} | {'A spread':>8}")
    for e in brackets:
        print(f"{e['index']:>7} | {e['A_before']['tok_per_s']:8.3f} | {e['B']['tok_per_s']:8.3f} | "
              f"{e['A_after']['tok_per_s']:8.3f} | {(e['paired_speedup'] or 0):6.3f} | "
              f"{(e['a_spread_rel'] or 0)*100:7.2f}%")
    print(f"\npaired speedup: median {med_s:.3f}" + (f"  95% CI [{ci[0]:.3f}, {ci[1]:.3f}]" if ci else ""))
    if s_by_position:
        print("S by candidate position: " + "; ".join(
            f"{pos}={v['median']:.3f} (n={v['n']})" for pos, v in s_by_position.items()))
    print(f"A-bracket spread: CV {summary['a_cv_pct']:.1f}%  |  A mean {statistics.mean(a_all):.3f} tok/s")
    km = k1_metrics
    def fmt(v, spec):
        return format(v, spec) if v is not None else "n/a"
    print(f"\nK1 model metrics (medians):")
    print(f"  A: tok/s {km['A']['tok_per_s']:.3f}  SSD {km['A']['ssd_mb_per_token']:.1f} MB/tok  "
          f"hit {fmt(km['A']['decode_hit_rate'], '.3f')}  resident {fmt(km['A']['resident_decode_mb'], '.0f')} MB")
    print(f"  B: tok/s {km['B']['tok_per_s']:.3f}  SSD {km['B']['ssd_mb_per_token']:.1f} MB/tok  "
          f"hit {fmt(km['B']['decode_hit_rate'], '.3f')}  resident {fmt(km['B']['resident_decode_mb'], '.0f')} MB")
    if route_div:
        print(f"  routing divergence (B vs A moe.csv): median {statistics.median(route_div)*100:.2f}% of rows")
    print("\n--- checks ---")
    for c in checks:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}")
    verdict = "HARNESS PASS" if all_ok else "HARNESS FAIL"
    print(f"\noverall: {verdict}  (statistical outcomes judged in the K1 report, per frozen 9G convention)")
    print(f"wrote {jp}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
