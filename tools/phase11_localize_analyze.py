#!/usr/bin/env python3
"""Phase 11 Metal numerical localization — boundary comparison CPU vs Metal.

Parses the TCAT act.bin traces from the two localize runs, aligns records
by (phase, start_pos, il, name), and reports per-boundary divergence stats
so the earliest causally meaningful CPU-vs-Metal difference can be found by
walking backward from layer-1 attn_out through the KDA attention chain.

Usage:
    python3 tools/phase11_localize_analyze.py \
        benchmarks/results/phase-k1/localize/cpu/act.bin \
        benchmarks/results/phase-k1/localize/metal/act.bin \
        [--il 1] [--ph 0] [--sp 0]
"""
import argparse
import math
import struct
import sys


def read_tcat(path):
    """Yield (exec_id, il, n_embd, n_tokens, start_pos, phase, name, data)."""
    recs = []
    with open(path, "rb") as f:
        while True:
            hdr = f.read(4)
            if len(hdr) < 4:
                break
            magic, = struct.unpack("<I", hdr)
            if magic != 0x54434154:
                print(f"bad magic {magic:#x} at offset {f.tell()-4}", file=sys.stderr)
                break
            exec_id, = struct.unpack("<I", f.read(4))
            il, = struct.unpack("<i", f.read(4))
            n_embd, = struct.unpack("<I", f.read(4))
            n_tokens, = struct.unpack("<I", f.read(4))
            start_pos, = struct.unpack("<I", f.read(4))
            phase, = struct.unpack("<I", f.read(4))
            name_len, = struct.unpack("<I", f.read(4))
            name = f.read(name_len).decode("utf-8", "replace")
            data = struct.unpack(f"<{n_embd}f", f.read(4 * n_embd))
            recs.append((exec_id, il, n_embd, n_tokens, start_pos, phase, name, data))
    return recs


def stats(a, b):
    """Divergence stats between two equal-length float tuples."""
    n = min(len(a), len(b))
    max_abs = 0.0
    sum_abs = 0.0
    sum_sq = 0.0
    nan_a = nan_b = 0
    inf_a = inf_b = 0
    worst_i = -1
    for i in range(n):
        x, y = a[i], b[i]
        if math.isnan(x):
            nan_a += 1
        if math.isnan(y):
            nan_b += 1
        if math.isinf(x):
            inf_a += 1
        if math.isinf(y):
            inf_b += 1
        d = abs(x - y)
        sum_abs += d
        sum_sq += d * d
        if d > max_abs:
            max_abs = d
            worst_i = i
    mean_abs = sum_abs / n if n else 0.0
    rms = math.sqrt(sum_sq / n) if n else 0.0
    # relative error where meaningful (denominator = max(|a|,|b|) at worst point)
    rel = 0.0
    if worst_i >= 0:
        denom = max(abs(a[worst_i]), abs(b[worst_i]), 1e-30)
        rel = max_abs / denom
    return {
        "n": n, "max_abs": max_abs, "mean_abs": mean_abs, "rms": rms,
        "rel": rel, "nan_a": nan_a, "nan_b": nan_b, "inf_a": inf_a, "inf_b": inf_b,
        "worst": (a[worst_i], b[worst_i]) if worst_i >= 0 else (None, None),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cpu_act")
    ap.add_argument("metal_act")
    ap.add_argument("--il", type=int, default=1)
    ap.add_argument("--ph", type=int, default=0)
    ap.add_argument("--sp", type=int, default=0)
    args = ap.parse_args()

    cpu = read_tcat(args.cpu_act)
    metal = read_tcat(args.metal_act)
    print(f"cpu records: {len(cpu)}   metal records: {len(metal)}")

    def by_key(recs):
        d = {}
        for r in recs:
            _, il, n_embd, n_tokens, sp, ph, name, data = r
            d.setdefault((ph, sp, il, name), []).append((n_tokens, data))
        return d

    ck = by_key(cpu)
    mk = by_key(metal)

    # the layer-1 attention chain, in execution order (earliest first);
    # the analysis walks BACKWARD from attn_out
    chain = [
        "l_in", "attn_norm",
        "kda_Q_proj", "kda_K_proj", "kda_V_proj",
        "kda_Qcur", "kda_Kcur", "kda_Vcur",
        "kda_g1", "kda_beta",
        "kda_Q_norm", "kda_K_norm",
        "kda_delta_out", "kda_new_state",
        "kda_normed", "kda_gate", "kda_gated",
        "kda_out", "attn_out",
    ]
    print(f"\n=== layer {args.il}, phase={args.ph}, start_pos={args.sp} ===")
    print(f"{'name':16s} {'n':>5s} {'max|d|':>12s} {'mean|d|':>12s} {'rms':>12s} {'rel@max':>10s} {'nanC/M':>8s} {'worst (cpu, metal)':>28s}")
    rows = []
    for name in chain:
        key = (args.ph, args.sp, args.il, name)
        c = ck.get(key)
        m = mk.get(key)
        if not c or not m:
            print(f"{name:16s}  (missing: cpu={bool(c)} metal={bool(m)})")
            continue
        # take the record with the largest n_tokens in this step (first prefill sub-step)
        c = max(c, key=lambda t: t[0])
        m = max(m, key=lambda t: t[0])
        s = stats(c[1], m[1])
        rows.append((name, s))
        worst = s["worst"]
        wstr = "(identical)" if worst[0] is None else f"({worst[0]:.4e}, {worst[1]:.4e})"
        print(f"{name:16s} {s['n']:5d} {s['max_abs']:12.3e} {s['mean_abs']:12.3e} "
              f"{s['rms']:12.3e} {s['rel']:10.2e} {s['nan_a']+s['inf_a']}/{s['nan_b']+s['inf_b']:>2d} "
              f"{wstr:>28s}")

    # backward walk: find the first (deepest) boundary where divergence is
    # still above a material threshold; report the earliest boundary that
    # already differs, going from attn_out back to l_in.
    print("\n=== backward walk (attn_out -> l_in) ===")
    names_by_depth = list(reversed(chain))
    prev = None
    for name in names_by_depth:
        row = next((r for r in rows if r[0] == name), None)
        if row is None:
            continue
        s = row[1]
        marker = ""
        if prev is not None and s["max_abs"] > 0:
            marker = "  <-- divergence persists upstream of " + prev
        print(f"{name:16s} max|d|={s['max_abs']:.3e} rel={s['rel']:.2e}{marker}")
        if s["max_abs"] > 0:
            prev = name if prev is None else prev
    print("\nInterpretation: the EARLIEST boundary (top of this list) with")
    print("max|d| materially above f32-rounding noise is the first causally")
    print("meaningful divergence; its upstream input boundaries tell whether")
    print("inputs were already different (propagation) or identical (op-level).")


if __name__ == "__main__":
    main()
