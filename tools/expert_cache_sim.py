#!/usr/bin/env python3
"""Phase 3 routing-locality analysis and expert-cache simulation.

Reads a Kimi Linear MoE routing trace produced by the env-gated tracer
(KIMI_TRACE_MOE=1) in llama.cpp and computes:

  - per-token and sliding-window distinct experts;
  - cumulative working-set growth;
  - routing frequency / skew;
  - per-layer routing distributions;
  - reuse distance (tokens and accesses);
  - prefill vs decode, cold-start vs steady-state splits;
  - global LRU, per-layer LRU, and Belady/OPT hit rates at the Phase 8
    ladder capacities (and beyond, up to the Phase 2 Metal budget).

Usage:
    python3 tools/expert_cache_sim.py \
        benchmarks/results/traces/phase-03-coding-lru.csv \
        benchmarks/results/phase-03-locality.json

Trace format (one row per (graph-execution, layer, token-within-batch)):
    phase,n_tokens,layer,token_idx,e0..e7
Rows are emitted layer-major per graph execution; every execution emits
exactly the MoE layers 1..26 in order, so group boundaries are detected
by layer wrapping back to 1.
"""

import csv
import json
import sys
from collections import defaultdict, OrderedDict

PER_EXPERT_BYTES = 4285440.0  # measured avg (Phase 3 addressability): gate/up Q4_K 1,327,104 B; down Q6_K 1,935,360 B (13 layers) or Q4_K 1,327,104 B (13 layers)
N_LAYERS = 26
N_EXPERT_USED = 8

# Phase 8 ladder capacities in experts (approx, at 4.59 MB/expert)
LADDER_GB = [1, 2, 4, 6, 8, 10, 12]
EXTRA_GB = [14.6]  # ~ max practical GPU-side cache headroom at 8K ctx (Phase 2)


def load_trace(path):
    """Return (groups, accesses).

    groups: list of dicts {phase, n_tokens, start_token, rows}
    accesses: list of (token, layer, expert_id) in access order.
    """
    groups = []
    cur = None
    prev_layer = None
    with open(path) as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or row[0].startswith("#"):
                continue
            phase = row[0]
            n_tokens = int(row[1])
            layer = int(row[2])
            tok_idx = int(row[3])
            experts = [int(x) for x in row[4:4 + N_EXPERT_USED]]
            # rows are emitted layer-major per graph execution, layers
            # non-decreasing within a group; a drop to a lower layer marks a new
            # group. Note: the last MoE layer (26) is only routed for output
            # tokens during prefill, so it may carry fewer rows than n_tokens.
            if cur is None or (prev_layer is not None and layer < prev_layer):
                cur = {
                    "phase": phase,
                    "n_tokens": n_tokens,
                    "rows": [],
                    "start_token": groups[-1]["start_token"] + groups[-1]["n_tokens"] if groups else 0,
                }
                groups.append(cur)
            assert len(experts) == N_EXPERT_USED, f"bad row: {row}"
            cur["rows"].append((layer, tok_idx, experts))
            prev_layer = layer
    # sanity: layers 1..25 present in every group; layer 26 optional (output-token
    # selection on the last layer); no layer exceeds n_tokens rows.
    for g in groups:
        layers = [r[0] for r in g["rows"]]
        assert set(range(1, 26)).issubset(set(layers)), f"group {g['phase']} start={g['start_token']} missing layers"
        assert max(layers) <= 26
    accesses = []
    for g in groups:
        base = g["start_token"]
        for layer, tok_idx, experts in g["rows"]:
            for e in experts:
                accesses.append((base + tok_idx, layer, e))
    return groups, accesses


def metrics(accesses, n_tokens):
    """Basic locality metrics over the access list."""
    # per-token distinct experts (union over layers)
    per_token = defaultdict(set)
    for tok, layer, e in accesses:
        per_token[tok].add((layer, e))
    distinct_per_token = [len(per_token[t]) for t in sorted(per_token)]

    # cumulative working set
    ws = set()
    working_set = []
    for tok, layer, e in accesses:
        ws.add((layer, e))
        working_set.append(len(ws))

    # frequency / skew
    freq = defaultdict(int)
    for tok, layer, e in accesses:
        freq[(layer, e)] += 1
    total_accesses = len(accesses)
    top = sorted(freq.values(), reverse=True)
    n_experts = len(freq)
    frac_10 = sum(top[: max(1, n_experts // 10)]) / total_accesses
    frac_1 = sum(top[: max(1, n_experts // 100)]) / total_accesses

    # per-layer distributions
    per_layer_counts = defaultdict(int)
    per_layer_experts = defaultdict(set)
    for tok, layer, e in accesses:
        per_layer_counts[layer] += 1
        per_layer_experts[layer].add(e)
    per_layer = {
        str(l): {"accesses": per_layer_counts[l], "distinct_experts": len(per_layer_experts[l])}
        for l in sorted(per_layer_counts)
    }

    # reuse distance in tokens (token-to-token since last use of same (layer, expert))
    last_seen = {}
    reuse_tokens = []
    for tok, layer, e in accesses:
        key = (layer, e)
        if key in last_seen:
            reuse_tokens.append(tok - last_seen[key])
        last_seen[key] = tok
    # reuse distance in accesses
    last_seen_a = {}
    reuse_access = []
    for i, (tok, layer, e) in enumerate(accesses):
        key = (layer, e)
        if key in last_seen_a:
            reuse_access.append(i - last_seen_a[key])
        last_seen_a[key] = i

    def pct(v, p):
        if not v:
            return None
        v = sorted(v)
        return v[min(len(v) - 1, int(p / 100 * len(v)))]

    return {
        "n_accesses": total_accesses,
        "n_tokens": n_tokens,
        "n_distinct_experts": n_experts,
        "distinct_per_token": {
            "mean": round(sum(distinct_per_token) / len(distinct_per_token), 2),
            "max": max(distinct_per_token),
        },
        "working_set_final": len(ws),
        "accesses_top_1pct_experts_share": round(frac_1, 4),
        "accesses_top_10pct_experts_share": round(frac_10, 4),
        "reuse_distance_tokens": {
            "mean": round(sum(reuse_tokens) / len(reuse_tokens), 1) if reuse_tokens else None,
            "median": pct(reuse_tokens, 50),
            "p90": pct(reuse_tokens, 90),
        },
        "reuse_distance_accesses": {
            "mean": round(sum(reuse_access) / len(reuse_access), 1) if reuse_access else None,
            "median": pct(reuse_access, 50),
            "p90": pct(reuse_access, 90),
        },
        "per_layer": per_layer,
    }


def simulate_lru(accesses, capacity, per_layer=False):
    """LRU hit rate. capacity in experts. Returns (hits, misses)."""
    if not per_layer:
        cache = OrderedDict()
        hits = misses = 0
        for tok, layer, e in accesses:
            key = (layer, e)
            if key in cache:
                cache.move_to_end(key)
                hits += 1
            else:
                cache[key] = None
                if len(cache) > capacity:
                    cache.popitem(last=False)
                misses += 1
        return hits, misses
    per_layer_cap = max(1, capacity // N_LAYERS)
    caches = [OrderedDict() for _ in range(N_LAYERS + 1)]
    hits = misses = 0
    for tok, layer, e in accesses:
        c = caches[layer]
        key = e
        if key in c:
            c.move_to_end(key)
            hits += 1
        else:
            c[key] = None
            if len(c) > per_layer_cap:
                c.popitem(last=False)
            misses += 1
    return hits, misses


def simulate_opt(accesses, capacity):
    """Belady/OPT hit rate. capacity in experts."""
    # next-access index per (layer, expert)
    next_idx = defaultdict(list)
    for i, (tok, layer, e) in enumerate(accesses):
        next_idx[(layer, e)].append(i)
    ptr = {k: 0 for k in next_idx}
    cache = {}  # key -> next access index
    hits = misses = 0
    for i, (tok, layer, e) in enumerate(accesses):
        key = (layer, e)
        ptr[key] += 1
        nxt = next_idx[key][ptr[key]] if ptr[key] < len(next_idx[key]) else float("inf")
        if key in cache:
            cache[key] = nxt
            hits += 1
        else:
            if len(cache) >= capacity:
                victim = max(cache, key=cache.get)
                del cache[victim]
            cache[key] = nxt
            misses += 1
    return hits, misses


def hr(hits, misses):
    return hits / (hits + misses) if (hits + misses) else None


def main():
    trace_path, out_path = sys.argv[1], sys.argv[2]
    groups, accesses = load_trace(trace_path)
    n_tokens = groups[-1]["start_token"] + groups[-1]["n_tokens"]

    cold_cut = min(64, n_tokens)
    steady_cut = min(256, n_tokens)
    prefill_acc = [(t, l, e) for t, l, e in accesses
                   if any(g["phase"] == "prefill" and g["start_token"] <= t < g["start_token"] + g["n_tokens"] for g in groups)]
    # simpler: recompute by phase from groups
    prefill_acc, decode_acc = [], []
    for g in groups:
        acc = prefill_acc if g["phase"] == "prefill" else decode_acc
        base = g["start_token"]
        for layer, tok_idx, experts in g["rows"]:
            for e in experts:
                acc.append((base + tok_idx, layer, e))

    result = {
        "trace": trace_path,
        "summary": {
            "n_tokens": n_tokens,
            "n_prefill_tokens": sum(1 for _ in prefill_acc) // (N_LAYERS * N_EXPERT_USED),
            "n_decode_tokens": sum(1 for _ in decode_acc) // (N_LAYERS * N_EXPERT_USED),
            "n_accesses": len(accesses),
        },
        "overall": metrics(accesses, n_tokens),
        "prefill": metrics(prefill_acc, n_tokens),
        "decode": metrics(decode_acc, n_tokens),
    }

    # cold vs steady by token index
    cold_acc = [a for a in accesses if a[0] < cold_cut]
    steady_acc = [a for a in accesses if a[0] >= steady_cut]
    result["cold_first_%d_tokens" % cold_cut] = metrics(cold_acc, n_tokens)
    result["steady_from_token_%d" % steady_cut] = metrics(steady_acc, n_tokens)

    capacities_gb = LADDER_GB + EXTRA_GB
    sims = []
    for gb in capacities_gb:
        cap = max(1, int(gb * 1024 ** 3 / PER_EXPERT_BYTES))
        row = {"capacity_gb": gb, "capacity_experts": cap}
        for name, acc, label in [
            ("global_lru", accesses, "overall"),
            ("global_lru", cold_acc, "cold"),
            ("global_lru", steady_acc, "steady"),
        ]:
            h, m = simulate_lru(acc, cap, per_layer=False)
            row["%s_%s" % (name, label)] = round(hr(h, m), 4)
        h, m = simulate_lru(accesses, cap, per_layer=True)
        row["per_layer_lru_overall"] = round(hr(h, m), 4)
        h, m = simulate_lru(steady_acc, cap, per_layer=True)
        row["per_layer_lru_steady"] = round(hr(h, m), 4)
        h, m = simulate_opt(accesses, cap)
        row["opt_overall"] = round(hr(h, m), 4)
        h, m = simulate_opt(steady_acc, cap)
        row["opt_steady"] = round(hr(h, m), 4)
        sims.append(row)
    result["simulations"] = sims

    # capacities required for 90/95/99 (global LRU overall and steady)
    for policy in ["global_lru", "per_layer_lru", "opt"]:
        for subset in ["overall", "steady"]:
            key = "%s_%s" % (policy, subset)
            thresholds = {}
            for th in [0.90, 0.95, 0.99]:
                found = None
                for row in sims:
                    if row.get(key) is not None and row[key] >= th:
                        found = row["capacity_gb"]
                        break
                thresholds[str(int(th * 100))] = found
            result.setdefault("threshold_capacities_gb", {})[key] = thresholds

    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    # human-readable output
    print("=== Phase 3 locality summary ===")
    s = result["summary"]
    print(f"tokens: {s['n_tokens']} (prefill {s['n_prefill_tokens']}, decode {s['n_decode_tokens']})")
    o = result["overall"]
    print(f"distinct experts: {o['n_distinct_experts']} / 6656, working set final: {o['working_set_final']}")
    print(f"distinct/token: mean {o['distinct_per_token']['mean']}, max {o['distinct_per_token']['max']}")
    print(f"top-1% experts carry {o['accesses_top_1pct_experts_share']*100:.1f}% of accesses")
    print(f"reuse distance tokens: mean {o['reuse_distance_tokens']['mean']}, median {o['reuse_distance_tokens']['median']}")
    print("\n=== Hit-rate table (overall / steady) ===")
    print(f"{'GB':>5} {'exp':>5} | {'gLRU':>6} {'plRU':>6} {'OPT':>6} | {'gLRU-s':>7} {'plRU-s':>7} {'OPT-s':>7}")
    for row in sims:
        print(f"{row['capacity_gb']:>5} {row['capacity_experts']:>5} | "
              f"{row['global_lru_overall']:>6.3f} {row['per_layer_lru_overall']:>6.3f} {row['opt_overall']:>6.3f} | "
              f"{row['global_lru_steady']:>7.3f} {row['per_layer_lru_steady']:>7.3f} {row['opt_steady']:>7.3f}")
    print("\n=== Capacities (GB) for hit-rate thresholds ===")
    for policy, thr in result["threshold_capacities_gb"].items():
        print(f"{policy:>22}: 90%={thr['90']} 95%={thr['95']} 99%={thr['99']}")


if __name__ == "__main__":
    main()
