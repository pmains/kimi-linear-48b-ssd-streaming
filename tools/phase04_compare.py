#!/usr/bin/env python3
"""Phase 4 first-divergence diagnostic: conventional-CPU vs streamed-CPU.

Reads:
  - activation/logits binary traces (TCAT v2 records) from both runs
    (KIMI_TRACE_ACT=1). Records carry a tensor-role name: l_in, attn_out,
    ffn_inp, router_logits, router_weights, moe_out, l_out, logits.
  - MoE routing traces (KIMI_TRACE_MOE=1) from both runs;
  - the streamed retrieval log (KIMI_STREAM_RETR_FILE).

Reports, per semantically aligned execution, the boundaries in true
pipeline order (layer 0..N, role order within layer) and STOPS at the
first boundary whose max|d| exceeds the 1e-5 criterion. The goal is
localization: name the first tensor that differs, not exhaustively diff
everything.

Also retains the A/B/C claims:
  A. Retrieval equivalence (streamed side only): byte-exact expert ranges.
  B. Routing: router IDs bit-identical per (layer, token).
  C. Model equivalence: final logits within 1e-5, top-1/top-5 agreement.

Executions are aligned SEMANTICALLY by (phase, start_pos, n_tokens) in
FILE ORDER (occurrence index within the key sequence), not by exec id:
the conventional path numbers execs with a static counter (even ids
0,2,4,...) while the streamed path uses its own sequential counter
(0,1,2,...), so exec ids are not comparable between runs.

TCAT v2 record: u32 magic "TCAT" | u32 exec_id | i32 il | u32 n_embd |
u32 n_tokens | u32 start_pos | u32 phase | u32 name_len | name |
f32 data[n_embd]   (il = -1 for logits; phase: 0=prefill, 1=decode;
data = token-0 column)

Usage:
    python3 tools/phase04_compare.py CONV_ACT CONV_MOE STREAM_ACT STREAM_MOE \
        [STREAM_RETRIEVAL_LOG]
"""

import struct
import sys

MAGIC = 0x54434154  # "TCAT"
CRIT_ACT_MAX = 1e-5
CRIT_LOGITS_MAX = 1e-5
CRIT_LOGITS_MEAN = 1e-6
CRIT_TOP1 = 1.0
CRIT_TOP5 = 1.0

# True pipeline order within a layer. Records are compared in this order so
# the first DIFF reported is the causal boundary (layer-major, role-minor).
# Some entries are virtual comparison roles (4A.2 mul_mat_id isolation):
#   ffn_normed_v  - streamed persisted norm view  vs conventional route norm
#   moe_out_full  - streamed full-parent-tensor reference vs conventional moe_out
ROLE_TARGETS = {
    "l_in":           ("l_in",           "l_in"),
    "attn_out":       ("attn_out",       "attn_out"),
    "ffn_inp":        ("ffn_inp",        "ffn_inp"),
    "ffn_normed":     ("ffn_normed",     "ffn_normed"),
    "ffn_normed_v":   ("ffn_normed",     "ffn_normed_v"),
    "router_logits":  ("router_logits",  "router_logits"),
    "router_weights": ("router_weights", "router_weights"),
    "router_weights_v":("router_weights", "router_weights_v"),
    "moe_up":         ("moe_up",         "moe_up"),
    "moe_gate":       ("moe_gate",       "moe_gate"),
    "moe_down":       ("moe_down",       "moe_down"),
    "moe_up_full":    ("moe_up",         "moe_up_full"),
    "moe_gate_full":  ("moe_gate",       "moe_gate_full"),
    "moe_down_full":  ("moe_down",       "moe_down_full"),
    "moe_out":        ("moe_out",        "moe_out"),
    "moe_out_full":   ("moe_out",        "moe_out_full"),
    "l_out":          ("l_out",          "l_out"),
    "logits":         ("logits",         "logits"),
}
ROLE_ORDER = list(ROLE_TARGETS.keys())

PHASE_NAME = {0: "prefill", 1: "decode"}


def read_act_trace(path):
    """Return (execs, order).

    execs: {exec_id: (semkey, {(il, name): (n_tokens, start_pos, phase, data)})}
    order: exec ids in first-seen file order (chronological dump order).
    """
    execs = {}
    order = []
    with open(path, "rb") as f:
        while True:
            head = f.read(4 + 4 + 4 + 4 + 4 + 4 + 4 + 4)
            if len(head) == 0:
                break
            if len(head) != 32:
                raise SystemExit(f"{path}: truncated header ({len(head)} bytes)")
            magic, exec_id, il, n_embd, n_tokens, start_pos, phase, name_len = struct.unpack("<IIiIIIII", head)
            if magic != MAGIC:
                raise SystemExit(f"{path}: bad magic 0x{magic:08x}")
            name = f.read(name_len).decode("utf-8", "replace")
            data = struct.unpack(f"<{n_embd}f", f.read(4 * n_embd))
            if exec_id not in execs:
                execs[exec_id] = ((phase, start_pos, n_tokens), {})
                order.append(exec_id)
            execs[exec_id][1][(il, name)] = (n_tokens, start_pos, phase, data)
    return execs, order


def read_moe_trace(path):
    """Return (semkey_seq, rows). semkey_seq: per-execution (phase, n_tokens,
    start_pos) run-lengths; rows: raw CSV rows (ids parsed)."""
    runs = []
    rows = []
    cur_key = None
    cur_count = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(",")
            phase = parts[0]
            n_tokens = int(parts[1])
            start_pos = int(parts[12]) if len(parts) > 12 else None
            ids = [int(x) for x in parts[4:12]]
            rows.append((phase, n_tokens, start_pos, ids))
            key = (phase, n_tokens, start_pos)
            if key != cur_key:
                if cur_key is not None:
                    runs.append((cur_key, cur_count))
                cur_key = key
                cur_count = 0
            cur_count += 1
    if cur_key is not None:
        runs.append((cur_key, cur_count))
    return runs, rows


def topk_indices(v, k):
    return sorted(range(len(v)), key=lambda i: v[i], reverse=True)[:k]


def fmt_boundary(il, role, res):
    """res = (ok, maxd, meand, bit, first_idx, va, vb)"""
    ok, maxd, meand, bit, first_idx, va, vb = res
    tag = "PASS" if ok else "DIFF"
    s = f"  {role}[{il:>2}] {tag:4s} max|d|={maxd:.3e} mean|d|={meand:.3e} bit={'yes' if bit else 'no '}"
    if not ok:
        s += f" first@[{first_idx}] {va:.6e} vs {vb:.6e}"
    return s


def compare_exec(conv_recs, strm_recs, label):
    """Compare one semantically-aligned execution in pipeline order.

    Returns (ok, lines, first_div). first_div is None or
    (il, role, maxd, meand, bit, first_idx, va, vb). Scanning stops at the
    first boundary exceeding the criterion (localization, not exhaustive
    diff). Boundaries within tolerance are still reported (they are the
    'layers 0..N identical' evidence)."""
    lines = []
    ok = True
    first_div = None

    # structural check: record key sets must match, canonicalized to display
    # roles (streamed-only isolation roles fold onto their conv counterparts)
    def norm_keys(recs):
        out = set()
        for (il, r) in recs:
            disp = r
            for d, (cr, sr) in ROLE_TARGETS.items():
                if r == sr:
                    disp = cr  # stream-only virtual role folds onto its conv counterpart
                    break
            out.add((il, disp))
        return out
    kc = norm_keys(conv_recs)
    ks = norm_keys(strm_recs)
    if kc != ks:
        only_c = sorted(kc - ks)
        only_s = sorted(ks - kc)
        lines.append(f"  STRUCTURAL: record-set mismatch")
        if only_c:
            lines.append(f"    conv-only: {only_c[:8]}{'...' if len(only_c) > 8 else ''}")
        if only_s:
            lines.append(f"    stream-only: {only_s[:8]}{'...' if len(only_s) > 8 else ''}")
        ok = False

    layers = sorted({il for (il, _) in kc & ks if il >= 0})
    for il in layers:
        for role in ROLE_ORDER:
            c_role, s_role = ROLE_TARGETS[role]
            ckey = (il, c_role)
            skey = (il, s_role)
            if ckey not in conv_recs or skey not in strm_recs:
                continue
            nt_c, _, _, a = conv_recs[ckey]
            nt_s, _, _, b = strm_recs[skey]
            if nt_c != nt_s:
                lines.append(f"  {role}[{il:>2}] n_tokens mismatch {nt_c} vs {nt_s}")
                ok = False
                if first_div is None:
                    first_div = (il, role, -1.0, -1.0, False, -1, -1.0, -1.0)
                continue
            d = [abs(x - y) for x, y in zip(a, b)]
            maxd = max(d)
            meand = sum(d) / len(d)
            bit = maxd == 0.0
            okb = maxd <= CRIT_ACT_MAX
            if not okb and first_div is None:
                idx = d.index(maxd)
                first_div = (il, role, maxd, meand, bit, idx, a[idx], b[idx])
            lines.append(fmt_boundary(il, role, (okb, maxd, meand, bit,
                                                 -1 if okb else d.index(maxd),
                                                 -1.0 if okb else a[d.index(maxd)],
                                                 -1.0 if okb else b[d.index(maxd)])))
            if first_div is not None and (il, role) == (first_div[0], first_div[1]):
                # localization: stop the scan at the causal boundary
                lines.append(f"[{label}] FIRST DIVERGENCE: layer {first_div[0]}, "
                             f"role {first_div[1]}, max|d|={first_div[2]:.3e} "
                             f"(mean|d|={first_div[3]:.3e}, bit={first_div[4]})")
                return ok, lines, first_div

    # logits (il = -1) after the layer scan
    if (-1, "logits") in kc and (-1, "logits") in ks:
        _, nt_c, _, lg_c = conv_recs[(-1, "logits")]
        _, nt_s, _, lg_s = strm_recs[(-1, "logits")]
        d = [abs(x - y) for x, y in zip(lg_c, lg_s)]
        mx, mean = max(d), sum(d) / len(d)
        t1c, t1s = topk_indices(lg_c, 1)[0], topk_indices(lg_s, 1)[0]
        t5c, t5s = set(topk_indices(lg_c, 5)), set(topk_indices(lg_s, 5))
        agree1 = t1c == t1s
        agree5 = t5c == t5s
        flag = "PASS" if (mx <= CRIT_LOGITS_MAX and mean <= CRIT_LOGITS_MEAN and agree1 and agree5) else "DIFF"
        lines.append(f"  logits[-1] {flag} max|d|={mx:.3e} mean|d|={mean:.3e} "
                     f"top1={agree1} top5={agree5}")
        if not agree1:
            lines.append(f"    top1 conv={t1c} stream={t1s}")
        if flag == "DIFF" and first_div is None:
            first_div = (-1, "logits", mx, mean, mx == 0.0, -1, -1.0, -1.0)

    return ok, lines, first_div


def main():
    if len(sys.argv) not in (5, 6):
        raise SystemExit(__doc__)
    conv_act, conv_moe, strm_act, strm_moe = sys.argv[1:5]
    strm_retr = sys.argv[5] if len(sys.argv) == 6 else None

    conv, conv_order = read_act_trace(conv_act)
    strm, strm_order = read_act_trace(strm_act)

    # semantic alignment: per execution, (semkey, n_l_out_records) in file order
    def sem_seq(execs, order):
        out = []
        for e in order:
            n_lout = sum(1 for (il, name) in execs[e][1] if name == "l_out")
            out.append((execs[e][0], n_lout))
        return out

    seq_c, seq_s = sem_seq(conv, conv_order), sem_seq(strm, strm_order)
    if seq_c != seq_s:
        print("SEMANTIC ALIGNMENT: FAIL")
        print(f"  conv:   {seq_c}")
        print(f"  stream: {seq_s}")
        print("The two paths produced different execution sequences; "
              "refusing to compare mismatched executions.")
        return 1
    print(f"SEMANTIC ALIGNMENT: PASS ({len(seq_c)} executions)")

    all_ok = True
    g_first = None
    for i, (ec, es) in enumerate(zip(conv_order, strm_order)):
        key_c = conv[ec][0]
        key_s = strm[es][0]
        if key_c != key_s:
            print(f"[conv-vs-stream] exec {i}: semantic key mismatch "
                  f"{key_c} vs {key_s} — alignment broken")
            all_ok = False
            continue
        ph, sp, nt = key_c
        print(f"[{i}] ({PHASE_NAME.get(ph, ph)}, pos {sp}, n={nt})")
        ok, lines, first_div = compare_exec(conv[ec][1], strm[es][1], i)
        all_ok = all_ok and ok
        if first_div is not None and (g_first is None or first_div[0] < g_first[0]):
            g_first = (i,) + first_div
        for l in lines:
            print(f"  {l}" if not l.startswith(f"[{i}]") else l)

    # router IDs: verify run-sequence then row-for-row equality
    runs_c, rows_c = read_moe_trace(conv_moe)
    runs_s, rows_s = read_moe_trace(strm_moe)
    if runs_c != runs_s:
        print("\nrouter run-sequence: FAIL")
        print(f"  conv:   {runs_c}")
        print(f"  stream: {runs_s}")
        all_ok = False
    elif rows_c != rows_s:
        n = min(len(rows_c), len(rows_s))
        first = next((i for i in range(n) if rows_c[i] != rows_s[i]), None)
        print(f"\nrouter IDs: FAIL ({len(rows_c)} vs {len(rows_s)} rows; "
              f"first diff at row {first})")
        if first is not None:
            print(f"  conv:   {rows_c[first]}")
            print(f"  stream: {rows_s[first]}")
        all_ok = False
    else:
        print(f"\nrouter IDs: PASS ({len(rows_c)} rows bit-identical)")

    # A: retrieval equivalence (streamed side)
    n_req = n_bad = 0
    first_bad = None
    if strm_retr:
        with open(strm_retr) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                p = line.split(",")
                n_req += 1
                # Retrieval logs may carry extra columns (e.g. occurrence
                # indices). The status is always the penultimate field.
                status = p[-2] if len(p) >= 2 else ""
                if status != "ok":
                    n_bad += 1
                    first_bad = first_bad or p
        if n_bad == 0:
            print(f"\nretrieval equivalence: PASS ({n_req} expert ranges verified byte-exact vs mmap)")
        else:
            print(f"\nretrieval equivalence: FAIL ({n_bad}/{n_req} mismatches; first: {first_bad})")
            all_ok = False

    print(f"\nOVERALL: {'PASS' if all_ok else 'FAIL'}")

    # ---- compact verdict ----
    print("\n=== VERDICT ===")
    if strm_retr:
        print(f"A Retrieval:      {'PASS' if n_bad == 0 else 'FAIL'}"
              f"  ({n_req - n_bad}/{n_req} byte ranges exact)")
    r_state = "PASS" if rows_c == rows_s else "FAIL"
    print(f"B Routing:        {r_state}")
    if rows_c != rows_s:
        n = min(len(rows_c), len(rows_s))
        first = next((i for i in range(n) if rows_c[i] != rows_s[i]), None)
        same_set = first is not None and sorted(rows_c[first][3]) == sorted(rows_s[first][3])
        print(f"   first mismatch: row {first}")
        print(f"   same expert set: {'yes' if same_set else 'no'}"
              + (f"   ordering differs: {rows_c[first][3]} vs {rows_s[first][3]}" if same_set else ""))
    if g_first is not None:
        i, il, role, maxd, meand, bit, idx, va, vb = g_first
        print(f"B FIRST DIVERGENCE: exec {i}, layer {il}, role {role}")
        print(f"   max|d|={maxd:.3e} mean|d|={meand:.3e} bit-identical={'yes' if bit else 'no'}")
        if idx >= 0:
            print(f"   first differing element [{idx}]: {va:.6e} vs {vb:.6e}")
    else:
        print("B FIRST DIVERGENCE: none within criterion")
    print(f"OVERALL:          {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
