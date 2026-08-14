#!/usr/bin/env python3
"""Phase 4 checkpoint: compare conventional-CPU vs streamed-CPU inference.

Reads:
  - activation/logits binary traces (TCAT v2 records, token-0 column) from
    both runs (KIMI_TRACE_ACT=1);
  - MoE routing traces (KIMI_TRACE_MOE=1) from both runs;

and reports, per execution, three increasingly strong claims:

  A. Retrieval equivalence (streamed side only): the expert byte ranges the
     executor requested match the GGUF slices (checked against the mmap view
     at runtime by the loader; reported in the streamed retrieval log as
     occurrence-aware `(expert_id -> slot)` rows.
  B. Layer equivalence: router IDs bit-identical at every MoE layer, and
     per-layer l_out activation max|d| <= 1e-5 at token 0, with the first
     divergent layer reported automatically.
  C. Model equivalence: final logits max|d| <= 1e-5, mean|d| <= 1e-6,
     top-1 and top-5 agreement 100%.

Executions are aligned SEMANTICALLY by (phase, start_pos, n_tokens) in
FILE ORDER (occurrence index within the key sequence), not by exec id:
the conventional path numbers execs with a static counter (even ids
0,2,4,... after the warmup-dump quirk) while the streamed path uses its
own sequential counter (0,1,2,...), so exec ids are not comparable
between runs. Both traces contain the same executions with the same
semantic keys; the key sequence is the only trustworthy alignment.
If the ordered semantic-key sequences differ, the comparator fails
loudly instead of comparing mismatched executions.

TCAT v2 record: u32 magic "TCAT" | u32 exec_id | i32 il | u32 n_embd |
u32 n_tokens | u32 start_pos | u32 phase | u32 name_len | name |
f32[n_embd]   (il = -1 for logits; phase: 0=prefill, 1=decode)

Usage:
    python3 tools/phase04_compare.py CONV_ACT CONV_MOE STREAM_ACT STREAM_MOE \
        [STREAM_RETRIEVAL_LOG]
"""

import struct
import sys
from collections import defaultdict

MAGIC = 0x54434154  # "TCAT"
CRIT_ACT_MAX = 1e-5
CRIT_LOGITS_MAX = 1e-5
CRIT_LOGITS_MEAN = 1e-6
CRIT_TOP1 = 1.0
CRIT_TOP5 = 1.0

PHASE_NAME = {0: "prefill", 1: "decode"}


def read_act_trace(path):
    """Return (execs, order). execs: {exec_id: (semkey, {il: ...})};
    order: exec ids in first-seen file order (chronological dump order)."""
    execs = {}
    order = []
    cur = None
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
            execs[exec_id][1][il] = (name, n_tokens, start_pos, phase, data)
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


def compare_exec(conv, stream, label):
    """Compare one semantically-aligned execution.

    Returns (ok, lines, metrics) where metrics carries the summary
    numbers used by the final VERDICT block.
    """
    lines = []
    ok = True
    metrics = {"first_bad_layer": None, "logits": None}

    conv_layers = {il for il in conv if il >= 0}
    strm_layers = {il for il in stream if il >= 0}
    if conv_layers != strm_layers:
        ok = False
        lines.append(f"  layer set mismatch: conv={sorted(conv_layers)} stream={sorted(strm_layers)}")

    first_bad = None
    for il in sorted(conv_layers):
        if il not in stream:
            continue
        _, nt_c, _, _, a = conv[il]
        _, nt_s, _, _, b = stream[il]
        if nt_c != nt_s:
            lines.append(f"  layer {il}: n_tokens mismatch {nt_c} vs {nt_s}")
            ok = False
        d = [abs(x - y) for x, y in zip(a, b)]
        mx, mean = max(d), sum(d) / len(d)
        flag = "OK " if mx <= CRIT_ACT_MAX else "BAD"
        if mx > CRIT_ACT_MAX:
            ok = False
            first_bad = first_bad or il
        lines.append(f"  l_out[{il:>2}] {flag} max|d|={mx:.3e} mean|d|={mean:.3e}")

    if -1 in conv and -1 in stream:
        _, nt_c, _, _, lg_c = conv[-1]
        _, nt_s, _, _, lg_s = stream[-1]
        d = [abs(x - y) for x, y in zip(lg_c, lg_s)]
        mx, mean = max(d), sum(d) / len(d)
        t1c, t1s = topk_indices(lg_c, 1)[0], topk_indices(lg_s, 1)[0]
        t5c, t5s = set(topk_indices(lg_c, 5)), set(topk_indices(lg_s, 5))
        agree1 = t1c == t1s
        agree5 = t5c == t5s
        flag = "OK " if (mx <= CRIT_LOGITS_MAX and mean <= CRIT_LOGITS_MEAN and agree1 and agree5) else "BAD"
        if flag == "BAD":
            ok = False
        lines.append(f"  logits   {flag} max|d|={mx:.3e} mean|d|={mean:.3e} "
                     f"top1={agree1} top5={agree5}")
        if not agree1:
            lines.append(f"    top1 conv={t1c} stream={t1s}")
        metrics["logits"] = (mx, mean, agree1, agree5)
    elif (-1 in conv) != (-1 in stream):
        ok = False
        lines.append("  logits record missing on one side only (conv=%d stream=%d)" % (-1 in conv, -1 in stream))
    # else: logits absent on both sides is consistent (e.g. an eval-only ubatch)

    if first_bad is not None:
        lines.append(f"  first divergent layer: {first_bad}")
        metrics["first_bad_layer"] = first_bad

    verdict = "PASS" if ok else "FAIL"
    lines.insert(0, f"[{label}] {verdict}")
    return ok, lines, metrics


def main():
    if len(sys.argv) not in (5, 6):
        raise SystemExit(__doc__)
    conv_act, conv_moe, strm_act, strm_moe = sys.argv[1:5]
    strm_retr = sys.argv[5] if len(sys.argv) == 6 else None

    conv, conv_order = read_act_trace(conv_act)
    strm, strm_order = read_act_trace(strm_act)

    # semantic alignment: ordered (semkey, n_l_out_records) per execution,
    # in file (chronological) order
    def sem_seq(execs, order):
        return [(execs[e][0], len([il for il in execs[e][1] if il >= 0])) for e in order]

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
    g_first_layer = None
    g_logits = None
    # align by key-sequence occurrence index, NOT exec id (see module docstring)
    for i, (ec, es) in enumerate(zip(conv_order, strm_order)):
        key_c = conv[ec][0]
        key_s = strm[es][0]
        if key_c != key_s:
            print(f"[conv-vs-stream] exec {i}: semantic key mismatch "
                  f"{key_c} vs {key_s} — alignment broken")
            all_ok = False
            continue
        ph, sp, nt = key_c
        ok, lines, metrics = compare_exec(conv[ec][1], strm[es][1], i)
        all_ok = all_ok and ok
        if g_first_layer is None or (metrics["first_bad_layer"] is not None
                                     and metrics["first_bad_layer"] < g_first_layer):
            g_first_layer = metrics["first_bad_layer"]
        if metrics["logits"] is not None:
            g_logits = metrics["logits"]
        print("\n".join(f"  {l}" if not l.startswith("[") else l for l in lines))

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

    # ---- compact verdict (project-health format) ----
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
    a_state = "PASS" if g_first_layer is None else "FAIL"
    print(f"B Activations:    {a_state}")
    if g_first_layer is not None:
        print(f"   first divergence: layer {g_first_layer}")
    if g_logits is not None:
        mx, mean, agree1, agree5 = g_logits
        l_state = "PASS" if (mx <= CRIT_LOGITS_MAX and mean <= CRIT_LOGITS_MEAN and agree1 and agree5) else "FAIL"
        print(f"C Logits:         {l_state}  (max|d|={mx:.3e} mean|d|={mean:.3e} top1={agree1} top5={agree5})")
    print(f"OVERALL:          {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
