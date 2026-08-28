#!/usr/bin/env python3
"""Phase 9G pilot analysis: A->B->A paired brackets, harness validation.

Validates the Phase 9G bracket harness (not a performance experiment — do
not draw performance conclusions from n=4 brackets).

Checks, in order:
  1. A->B->A execution: all run dirs present with stats.csv; manifest
     confirms A runs used read_workers=A_WORKERS and B runs B_WORKERS.
  2. Paired speedup: S_i = tok/s(B_i) / mean(tok/s(A_before,i), tok/s(A_after,i));
     distribution over brackets (median, IQR, min/max, bootstrap CI).
  3. Correctness / invariants: moe.csv md5 == frozen Phase 8 baseline;
     retr.csv byte-identity across every run (deterministic routing);
     Phase 7 per-step invariants (phase07_summarize.py, exit 0);
     run.log scan for errors.
  4. Raw data retained: expected artifact files present in every run dir.
  5. Environmental covariates captured: env/*.json present and parseable.
  6. Order/warmup/state artifacts: A_before vs A_after position bias
     (sign test), first-bracket vs rest warmup check, linear drift of A
     tok/s across brackets.

Usage:
    python3 tools/phase09g_analyze.py [OUTDIR] [--config coding-cap4]

Output: <OUTDIR>/phase-09g-pilot-summary.json; exit 0 = PASS, 1 = FAIL.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import random
import statistics
import subprocess
import sys

WARMUP = 8  # steady-state decode starts here (Phase 6B convention)
FROZEN_BASELINE_MD5 = {
    "coding-cap4": "da45ab777b0fdd99be62f6c46a642e5c",
    "reasoning-cap8": "e26205ed69e8466120c9f199347ca2b3",
    "uncached": None,
}
EXPECTED_ARTIFACTS = [
    "stats.csv", "retr.csv", "moe.csv", "mem.csv", "cache_layers.csv",
    "p9c-layer.csv", "cpu.csv", "run.log", "manifest.json",
]

PHASE07 = "tools/phase07_summarize.py"


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
    res["moe_md5"] = md5(os.path.join(dirpath, "moe.csv")) if os.path.exists(os.path.join(dirpath, "moe.csv")) else None
    res["retr_md5"] = md5(os.path.join(dirpath, "retr.csv")) if os.path.exists(os.path.join(dirpath, "retr.csv")) else None
    mp = os.path.join(dirpath, "manifest.json")
    if os.path.exists(mp):
        try:
            with open(mp) as f:
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
    """True if all given files are byte-identical (None if any missing)."""
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
    """Run the Phase 7 per-step invariant validator; returns (ok, violations, exit_code)."""
    try:
        r = subprocess.run(
            [sys.executable, PHASE07, dirpath, "--json",
             os.path.join(dirpath, "..", ".invariant-%s.json" % os.path.basename(dirpath))],
            capture_output=True, text=True, timeout=120)
        ok = "invariants: PASS" in r.stdout or "all invariants hold" in r.stdout.lower()
        violations = None
        for line in r.stdout.splitlines():
            if "violation" in line.lower():
                violations = line.strip()
        return ok, violations, r.returncode
    except Exception as e:
        return False, str(e), -1


def bootstrap_ci(xs, n_resamples=10000, alpha=0.05, seed=9):
    """Percentile bootstrap CI of the median."""
    if len(xs) < 2:
        return None
    rng = random.Random(seed)
    meds = []
    for _ in range(n_resamples):
        s = [rng.choice(xs) for _ in range(len(xs))]
        meds.append(statistics.median(s))
    meds.sort()
    lo = meds[int(round(n_resamples * alpha / 2)) - 1]
    hi = meds[int(round(n_resamples * (1 - alpha / 2))) - 1]
    return [lo, hi]


def sign_test(xs):
    """Two-sided sign test p-value for median == 0 over nonzero deltas."""
    pos = sum(1 for x in xs if x > 0)
    neg = sum(1 for x in xs if x < 0)
    n = pos + neg
    if n == 0:
        return 1.0
    k = min(pos, neg)
    p = 0.0
    for i in range(k + 1):
        p += math.comb(n, i) * 0.5 ** n
    return 2 * p


def linear_slope(xs):
    """Least-squares slope of y vs index; returns slope_per_index."""
    n = len(xs)
    if n < 2:
        return None
    xbar = (n - 1) / 2.0
    ybar = sum(xs) / n
    num = sum((i - xbar) * (y - ybar) for i, y in enumerate(xs))
    den = sum((i - xbar) ** 2 for i in range(n))
    if den == 0:
        return None
    return num / den


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", nargs="?", default="benchmarks/results/phase-09g/pilot")
    ap.add_argument("--config", default="coding-cap4")
    args = ap.parse_args()

    base = os.path.join(args.outdir, args.config)
    harness = {}
    hp = os.path.join(args.outdir, "harness.json")
    if os.path.exists(hp):
        with open(hp) as f:
            harness = json.load(f)
    n_brackets = int(harness.get("n_brackets", 4))
    a_workers = int(harness.get("a_workers", 1))
    b_workers = int(harness.get("b_workers", 4))

    checks = []
    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    # --- 1. A->B->A execution ---
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
        # manifest worker check
        for role in ("A_before", "A_after"):
            mf = entry[role].get("manifest") or {}
            entry[role]["workers_ok"] = (mf.get("read_workers") == a_workers)
        entry["B"]["workers_ok"] = (entry["B"].get("manifest") or {}).get("read_workers") == b_workers
        brackets.append(entry)
    check("bracket execution", not exec_errors and len(brackets) == n_brackets,
          "; ".join(exec_errors) or f"{n_brackets} brackets x 3 runs complete")
    worker_fail = [f"b{e['index']}-{role}" for e in brackets for role in ("A_before", "A_after")
                   if not e[role].get("workers_ok")] + \
                  [f"b{e['index']}-B" for e in brackets if not e["B"].get("workers_ok")]
    check("manifest worker counts", not worker_fail,
          "A=%d B=%d; bad: %s" % (a_workers, b_workers, ",".join(worker_fail) or "none"))

    # --- 2. Paired speedup ---
    speedups, a_means, b_tok, a_spread = [], [], [], []
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
        b_tok.append(b)
        a_spread.append(e["a_spread_rel"])
    valid_s = [s for s in speedups if s is not None]
    if valid_s:
        med_s = statistics.median(valid_s)
        q = statistics.quantiles(valid_s, n=4) if len(valid_s) >= 4 else None
        ci = bootstrap_ci(valid_s)
    else:
        med_s = q = ci = None
    speedup_stats = {
        "n": len(valid_s),
        "median": med_s,
        "q1_q3": q,
        "min": min(valid_s) if valid_s else None,
        "max": max(valid_s) if valid_s else None,
        "bootstrap95_ci_of_median": ci,
        "per_bracket": speedups,
    }
    check("paired speedup computed", len(valid_s) == n_brackets,
          "median S=%.3f, per-bracket %s" % (med_s or 0, [round(s, 3) if s else None for s in speedups]))

    # --- 3. Correctness / invariants ---
    baseline = FROZEN_BASELINE_MD5.get(args.config)
    moe_bad, retr_bad = [], []
    all_retr = [os.path.join(base, e["dirs"][r], "retr.csv") for e in brackets for r in e["dirs"]]
    retr_ident = bytes_identical(all_retr)
    for e in brackets:
        for role in e["dirs"]:
            m = e[role]
            if baseline is not None and m.get("moe_md5") != baseline:
                moe_bad.append(f"b{e['index']}-{role}")
            if m.get("retr_md5") is None:
                retr_bad.append(f"b{e['index']}-{role}")
    check("moe md5 == frozen baseline", not moe_bad,
          "baseline=%s; bad: %s" % (baseline, ",".join(moe_bad) or "none"))
    check("retr byte-identity across all runs", retr_ident is True,
          "identical" if retr_ident else ("FAIL" if retr_ident is False else "n/a"))
    check("retr present everywhere", not retr_bad, ",".join(retr_bad) or "all present")

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

    # --- 4. Raw data retained ---
    missing = []
    for e in brackets:
        for role in e["dirs"]:
            d = os.path.join(base, e["dirs"][role])
            for a in EXPECTED_ARTIFACTS:
                if not os.path.exists(os.path.join(d, a)):
                    missing.append(f"{os.path.basename(d)}:{a}")
    check("raw artifacts retained", not missing, ",".join(missing) or "all artifacts in all 12 runs")

    # --- 5. Env covariates ---
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
    # summarize key covariates
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
                            "live_server_ok": live_ok,
                            "loadavg": snap.get("loadavg"),
                            "thermal": snap.get("cpu_thermal_level")})
    check("env covariates parseable", len(cov_summary) == len(env_files),
          f"{len(cov_summary)} snapshots summarized")

    # --- 6. Order / warmup / state artifacts ---
    a_before = [e["A_before"]["tok_per_s"] for e in brackets]
    a_after = [e["A_after"]["tok_per_s"] for e in brackets]
    deltas = [b - a for a, b in zip(a_before, a_after)]
    rel_deltas = [d / a for d, a in zip(deltas, a_before) if a]
    st = sign_test(deltas)
    pos_bias = {
        "a_before": a_before, "a_after": a_after,
        "mean_delta_rel": (statistics.mean(rel_deltas) if rel_deltas else None),
        "sign_test_p": st,
        "flag": st < 0.05 and all(d > 0 for d in deltas) or st < 0.05 and all(d < 0 for d in deltas),
    }
    check("no position bias (A_before vs A_after)", not pos_bias["flag"],
          f"mean delta {pos_bias['mean_delta_rel']*100:+.2f}% rel, sign test p={st:.3f}")

    a_all = [e[role]["tok_per_s"] for e in brackets for role in ("A_before", "A_after")]
    first_mean = statistics.mean([e["A_before"]["tok_per_s"] for e in brackets[:1]] + [e["A_after"]["tok_per_s"] for e in brackets[:1]])
    rest_mean = statistics.mean([e[role]["tok_per_s"] for e in brackets[1:] for role in ("A_before", "A_after")]) if n_brackets > 1 else None
    warm = (rest_mean is not None and abs(first_mean - rest_mean) / rest_mean > 0.10)
    warmup_check = {
        "first_bracket_A_mean": first_mean,
        "rest_A_mean": rest_mean,
        "rel_diff": ((first_mean - rest_mean) / rest_mean) if rest_mean else None,
        "flag": warm,
    }
    check("no warmup artifact (first bracket vs rest)", not warm,
          f"first {first_mean:.3f} vs rest {rest_mean:.3f} tok/s ({warmup_check['rel_diff']*100:+.1f}% rel)")

    a_means_by_index = [statistics.mean([e["A_before"]["tok_per_s"], e["A_after"]["tok_per_s"]]) for e in brackets]
    slope = linear_slope(a_means_by_index)
    drift = slope / statistics.mean(a_means_by_index) if slope is not None and a_means_by_index else None
    drift_check = {"slope_tok_per_s_per_bracket": slope, "rel_per_bracket": drift,
                   "flag": drift is not None and abs(drift) > 0.05}
    check("no strong drift across brackets", not drift_check["flag"],
          f"slope {slope:.4f} tok/s per bracket ({drift*100:+.2f}% rel/bracket)" if drift is not None else "slope n/a")

    # --- Assemble ---
    all_ok = all(c["ok"] for c in checks)
    summary = {
        "harness": harness,
        "config": args.config,
        "a_workers": a_workers,
        "b_workers": b_workers,
        "checks": checks,
        "speedup": speedup_stats,
        "a_spread_rel": a_spread,
        "a_cv_pct": (statistics.stdev(a_all) / statistics.mean(a_all) * 100 if len(a_all) > 1 else None),
        "position_bias": pos_bias,
        "warmup_check": warmup_check,
        "drift_check": drift_check,
        "covariates": cov_summary,
        "brackets": [{k: (v if k == "dirs" else
                      ({kk: vv for kk, vv in v.items() if kk in
                        ("tok_per_s", "ms_per_step", "pread_wall_ms", "moe_md5", "retr_md5",
                         "workers_ok", "invariants_ok", "cpu_pct", "error")} if isinstance(v, dict) else v))
                      for k, v in e.items()} for e in brackets],
    }
    jp = os.path.join(args.outdir, "phase-09g-pilot-summary.json")
    with open(jp, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    # --- Print ---
    print(f"=== Phase 9G harness pilot: {args.config} (A=w{a_workers} frozen, B=w{b_workers}) ===")
    print(f"{'bracket':>7} | {'A_before':>8} | {'B':>8} | {'A_after':>8} | {'S_i':>6} | {'A spread':>8}")
    for e in brackets:
        print(f"{e['index']:>7} | {e['A_before']['tok_per_s']:8.3f} | {e['B']['tok_per_s']:8.3f} | "
              f"{e['A_after']['tok_per_s']:8.3f} | {(e['paired_speedup'] or 0):6.3f} | "
              f"{(e['a_spread_rel'] or 0)*100:7.2f}%")
    print(f"\npaired speedup: median {med_s:.3f}" + (f"  95% CI [{ci[0]:.3f}, {ci[1]:.3f}]" if ci else ""))
    print(f"A-bracket spread: CV {summary['a_cv_pct']:.1f}%  |  A mean {statistics.mean(a_all):.3f} tok/s")
    print("\n--- checks ---")
    for c in checks:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}")
    print(f"\noverall: {'PILOT PASS' if all_ok else 'PILOT FAIL'}")
    print(f"wrote {jp}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
