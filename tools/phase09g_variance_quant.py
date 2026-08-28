#!/usr/bin/env python3
"""Phase 9G seed analysis: decompose tok/s variance from the archived 9D/9F
ladders into within-session (bracket-to-bracket) and between-session
components, using only the frozen workers=1 control runs plus the candidate
runs. This quantifies the session confound that falsified the 9E end-to-end
prediction, and provides the first variance estimates the 9G protocol design
needs (bracket count for 5/10/20% effect detection).

Convention (must match phase09f_analyze.py): steady-state decode = decode
rows after the 8-step warmup; tok/s = sum(n_tokens)*1e6/sum(total_us).

Usage:
    python3 tools/phase09g_variance_quant.py [OUT_JSON]

Output: benchmarks/results/phase-09g/variance-9d-9f.json (default).
"""
import csv
import json
import os
import sys

WARMUP = 8
ROOT = "benchmarks/results"
SESSIONS = {
    "9d": os.path.join(ROOT, "phase-09d", "ladder"),
    "9f": os.path.join(ROOT, "phase-09f", "ladder"),
}
CONFIGS = ["coding-cap4", "reasoning-cap8"]

# Run order in the bracketed ladders (frozen w1 control brackets around each
# candidate worker count). Workers=1 runs are all the identical frozen path.
RUN_ORDER = [
    "w1-before-W2", "w2", "w1-after-W2",
    "w1-before-W4", "w4", "w1-after-W4",
    "w1-before-W8", "w8", "w1-after-W8",
]


def read_stats(path):
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        return [dict(r) for r in reader]


def steady(rows):
    return [r for r in rows if r["phase"] == "decode"][WARMUP:]


def metrics(dirpath):
    sp = os.path.join(dirpath, "stats.csv")
    if not os.path.exists(sp):
        return None
    rows = steady(read_stats(sp))
    if not rows:
        return None
    tok = sum(int(r["n_tokens"]) for r in rows)
    total_us = sum(int(r["total_us"]) for r in rows)
    n = len(rows)
    return {
        "tok_per_s": tok * 1e6 / total_us,
        "ms_per_step": total_us / n / 1000.0,
        "pread_wall_ms": sum(int(r.get("pread_wall_us", r["pread_us"])) for r in rows) / n / 1000.0,
        "repack_ms": sum(int(r["repack_us"]) for r in rows) / n / 1000.0,
        "n_steps": n,
    }


def cv(xs):
    """coefficient of variation of non-empty list, or None"""
    if not xs:
        return None
    mean = sum(xs) / len(xs)
    if mean == 0:
        return None
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    return var ** 0.5 / mean


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "phase-09g", "variance-9d-9f.json")
    result = {"sessions": {}, "summary": {}}

    for sess, root in SESSIONS.items():
        result["sessions"][sess] = {"configs": {}}
        for cfg in CONFIGS:
            base = os.path.join(root, cfg)
            runs = {}
            for name in RUN_ORDER:
                m = metrics(os.path.join(base, name))
                if m:
                    runs[name] = m
            w1 = {k: v for k, v in runs.items() if k.startswith("w1-")}
            cand = {k: v for k, v in runs.items() if not k.startswith("w1-")}
            result["sessions"][sess]["configs"][cfg] = {
                "runs": runs,
                "w1_tok_per_s": [v["tok_per_s"] for v in w1.values()],
                "w1_pread_wall_ms": [v["pread_wall_ms"] for v in w1.values()],
            }

    # Summary: per session+config frozen-control spread, and session delta.
    for cfg in CONFIGS:
        for sess in SESSIONS:
            w1t = result["sessions"][sess]["configs"][cfg]["w1_tok_per_s"]
            w1r = result["sessions"][sess]["configs"][cfg]["w1_pread_wall_ms"]
            result["summary"][f"{sess}.{cfg}"] = {
                "w1_n": len(w1t),
                "w1_tok_per_s_mean": sum(w1t) / len(w1t) if w1t else None,
                "w1_tok_per_s_min": min(w1t) if w1t else None,
                "w1_tok_per_s_max": max(w1t) if w1t else None,
                "w1_tok_per_s_cv": cv(w1t),
                "w1_pread_wall_ms_mean": sum(w1r) / len(w1r) if w1r else None,
                "w1_pread_wall_ms_cv": cv(w1r),
            }
        a = result["summary"][f"9d.{cfg}"]
        b = result["summary"][f"9f.{cfg}"]
        result["summary"][f"session-delta.{cfg}"] = {
            "tok_per_s_9d_w1": a["w1_tok_per_s_mean"],
            "tok_per_s_9f_w1": b["w1_tok_per_s_mean"],
            "ratio_9f_over_9d": (b["w1_tok_per_s_mean"] / a["w1_tok_per_s_mean"]) if a["w1_tok_per_s_mean"] else None,
            "pread_wall_ms_9d_w1": a["w1_pread_wall_ms_mean"],
            "pread_wall_ms_9f_w1": b["w1_pread_wall_ms_mean"],
            "pread_wall_ratio": (b["w1_pread_wall_ms_mean"] / a["w1_pread_wall_ms_mean"]) if a["w1_pread_wall_ms_mean"] else None,
        }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True)
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
