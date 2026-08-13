#!/usr/bin/env python3
"""Phase 4 checkpoint: compare conventional-CPU vs streamed-CPU inference.

Reads:
  - activation/logits binary traces (TCAT records, token-0 column) from
    both runs (KIMI_TRACE_ACT=1);
  - MoE routing traces (KIMI_TRACE_MOE=1) from both runs;

and reports, per execution (prefill batch = exec 0, decode tokens = exec 1+):

  - router ID equality at every MoE layer, every token (bit-exact);
  - per-layer l_out activation error (max abs, mean abs) at token 0;
  - logits error: max abs, mean abs, top-1 agreement, top-5 agreement.

Criterion is PREDETERMINED and strict (both paths run identical quantized
kernels on identical expert bytes; weights pass an exact f32 readback on
CPU):

  - router IDs: bit-identical everywhere;
  - per-layer activation max|d| <= 1e-5;
  - logits max|d| <= 1e-5, mean|d| <= 1e-6, top-1 == 100%, top-5 == 100%.

If the criterion fails, investigate the numerical mechanism BEFORE loosening
anything. The per-layer errors localize the first divergent layer.

Usage:
    python3 tools/phase04_compare.py CONV_ACT CONV_MOE STREAM_ACT STREAM_MOE
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


def read_act_trace(path):
    """Return {exec_id: {il: (name, n_tokens, f32[n_embd])}}."""
    recs = defaultdict(dict)
    with open(path, "rb") as f:
        while True:
            head = f.read(4 + 4 + 4 + 4 + 4 + 4)
            if len(head) == 0:
                break
            if len(head) != 24:
                raise SystemExit(f"{path}: truncated header ({len(head)} bytes)")
            magic, exec_id, il, n_embd, n_tokens, name_len = struct.unpack("<IIiIII", head)
            if magic != MAGIC:
                raise SystemExit(f"{path}: bad magic 0x{magic:08x}")
            name = f.read(name_len).decode("utf-8", "replace")
            data = struct.unpack(f"<{n_embd}f", f.read(4 * n_embd))
            recs[exec_id][il] = (name, n_tokens, data)
    return recs


def read_moe_trace(path):
    """Return {exec_id: {layer: {token_idx: [8 ids]}}}."""
    execs = defaultdict(lambda: defaultdict(dict))
    cur_exec = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(",")
            phase, n_tokens, layer, tok = parts[0], int(parts[1]), int(parts[2]), int(parts[3])
            ids = [int(x) for x in parts[4:]]
            execs[cur_exec][layer][tok] = ids
    # exec boundaries are not marked in the CSV; the caller treats each
    # contiguous run of identical (phase, n_tokens) as one execution.
    # For the checkpoint both runs use identical prompts, so grouping by
    # phase transitions is done in the caller if needed.
    return execs


def topk_indices(v, k):
    return sorted(range(len(v)), key=lambda i: v[i], reverse=True)[:k]


def compare_exec(conv, stream, exec_id, label):
    """Compare one execution. Returns (ok, report_lines)."""
    lines = []
    ok = True

    conv_layers = {il for il in conv if il >= 0}
    strm_layers = {il for il in stream if il >= 0}
    if conv_layers != strm_layers:
        ok = False
        lines.append(f"  layer set mismatch: conv={sorted(conv_layers)} stream={sorted(strm_layers)}")

    # per-layer activations (token-0 column)
    first_bad = None
    for il in sorted(conv_layers):
        if il not in stream:
            continue
        _, nt_c, a = conv[il]
        _, nt_s, b = stream[il]
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

    # logits
    if -1 in conv and -1 in stream:
        _, nt_c, lg_c = conv[-1]
        _, nt_s, lg_s = stream[-1]
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
    elif (-1 in conv) != (-1 in stream):
        ok = False
        lines.append("  logits record missing on one side only (conv=%d stream=%d)" % (-1 in conv, -1 in stream))
    # else: logits absent on both sides is consistent (e.g. an eval-only ubatch)

    if first_bad is not None:
        lines.append(f"  first divergent layer: {first_bad}")

    verdict = "PASS" if ok else "FAIL"
    lines.insert(0, f"[{label}] exec {exec_id}: {verdict}")
    return ok, lines


def main():
    if len(sys.argv) != 5:
        raise SystemExit(__doc__)
    conv_act, conv_moe, strm_act, strm_moe = sys.argv[1:5]

    conv = read_act_trace(conv_act)
    strm = read_act_trace(strm_act)
    # (moe traces parsed for completeness; router equality checked via csv diff below)

    execs = sorted(set(conv) & set(strm))
    if not execs:
        raise SystemExit("no common executions between the two traces")
    if len(execs) != max(execs) + 1:
        print(f"note: non-contiguous exec ids: {execs}")

    all_ok = True
    for e in execs:
        ok, lines = compare_exec(conv[e], strm[e], e, "conv-vs-stream")
        all_ok = all_ok and ok
        print("\n".join(lines))

    # router equality: compare moe CSVs row-for-row (both runs deterministic,
    # same prompt, same order of executions)
    conv_rows = [l for l in open(conv_moe) if l.strip() and not l.startswith("#")]
    strm_rows = [l for l in open(strm_moe) if l.strip() and not l.startswith("#")]
    if conv_rows != strm_rows:
        n = min(len(conv_rows), len(strm_rows))
        first = next((i for i in range(n) if conv_rows[i] != strm_rows[i]), None)
        print(f"\nrouter IDs: FAIL ({len(conv_rows)} vs {len(strm_rows)} rows; "
              f"first diff at row {first})")
        if first is not None:
            print(f"  conv:   {conv_rows[first].strip()}")
            print(f"  stream: {strm_rows[first].strip()}")
        all_ok = False
    else:
        print(f"\nrouter IDs: PASS ({len(conv_rows)} rows bit-identical)")

    print(f"\nOVERALL: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
