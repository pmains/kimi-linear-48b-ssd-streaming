#!/usr/bin/env python3
"""Phase 6B hit-rate prediction: per-layer LRU with the exact zero-copy
partition.

The zero-copy cache partitions the budget into per-layer persistent slot
capacities (floor 9 slots/layer, remainder distributed round-robin in whole
experts). Unlike the Phase 6 global LRU, eviction is per-layer, so hit
rates shift slightly. This script predicts steady (decode-only) hit rates
for each ladder rung using the EXACT constructor partition, so the measured
6B hit rates can be compared against the model rather than the global-LRU
table.

Usage:
    python3 tools/phase06b_zc_sim.py TRACE_MOE_CSV RETRIEVAL_CSV [GB...]
        (default capacities: the Phase 6/6B ladder 1 2 4 6 8 10 12 14.6)

Trace format: the phase04 moe.csv (identical to the Phase 3 trace):
    # phase,n_tokens,layer,token_idx,e0..e7,start_pos
Retrieval CSV supplies the measured per-layer down slice bytes.
"""

import csv
import sys

UP_GATE_BYTES = 2 * 1327104  # up + gate, Q4_K, all layers (measured)
MIN_SLOTS = 9
N_LAYERS = 26

DEFAULT_GB = [1, 2, 4, 6, 8, 10, 12, 14.6]


def load_trace(path):
    """Return decode accesses as (layer, expert) in access order."""
    acc = []
    with open(path) as f:
        for row in csv.reader(f):
            if not row or row[0].startswith("#"):
                continue
            phase = row[0]
            layer = int(row[2])
            experts = [int(x) for x in row[4:12]]
            if phase == "decode":
                for e in experts:
                    acc.append((layer, e))
    return acc


def down_bytes_by_layer(retr_path):
    db = {}
    with open(retr_path) as f:
        for row in csv.reader(f):
            if not row or row[0].startswith("#"):
                continue
            il, kind, _occ, _e, _s, _o, by = row[0], row[1], row[2], row[3], row[4], row[5], row[6]
            if kind == "down":
                db[int(il)] = int(by)
    return db


def partition(budget_bytes, down_bytes):
    """Exact replica of the streamer constructor's per-layer capacity."""
    per_layer = {il: UP_GATE_BYTES + down_bytes[il] for il in range(1, N_LAYERS + 1)}
    min_cost = sum(MIN_SLOTS * b for b in per_layer.values())
    if min_cost > budget_bytes:
        return None
    cap = {il: MIN_SLOTS for il in range(1, N_LAYERS + 1)}
    rem = budget_bytes - min_cost
    progress = True
    while progress:
        progress = False
        for il in range(1, N_LAYERS + 1):
            if rem <= 0:
                break
            if cap[il] >= 256:
                continue
            if rem >= per_layer[il]:
                cap[il] += 1
                rem -= per_layer[il]
                progress = True
    return cap


def per_layer_lru_hit_rate(accesses, cap):
    """Per-layer LRU with per-layer capacity; returns hit/(hit+miss)."""
    lru = {il: [] for il in range(1, N_LAYERS + 1)}
    hits = misses = 0
    for (il, e) in accesses:
        lst = lru[il]
        if e in lst:
            hits += 1
            lst.remove(e)
            lst.append(e)
        else:
            misses += 1
            if len(lst) >= cap[il]:
                lst.pop(0)
            lst.append(e)
    return hits / (hits + misses) if hits + misses else 0.0


def main():
    trace_path = sys.argv[1]
    retr_path = sys.argv[2]
    gbs = [float(x) for x in sys.argv[3:]] if len(sys.argv) > 3 else DEFAULT_GB

    acc = load_trace(trace_path)
    db = down_bytes_by_layer(retr_path)
    if len(db) != N_LAYERS:
        print(f"warning: expected down-slice bytes for {N_LAYERS} layers, got {len(db)}", file=sys.stderr)

    print(f"decode accesses: {len(acc)}")
    print(f"{'GB':>5} {'slots/layer':>12} {'pred hit':>9}  caps")
    for gb in gbs:
        budget = int(round(gb * 1024 * 1024 * 1024))
        cap = partition(budget, db)
        if cap is None:
            print(f"{gb:>5}  below floor ({MIN_SLOTS} slots/layer) — zerocopy unavailable")
            continue
        hr = per_layer_lru_hit_rate(acc, cap)
        caps = f"{min(cap.values())}..{max(cap.values())}"
        total = sum(cap.values())
        print(f"{gb:>5} {total:>12} {hr:>9.4f}  {caps}")


if __name__ == "__main__":
    main()
