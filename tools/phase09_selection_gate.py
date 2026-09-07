#!/usr/bin/env python3
"""
Phase 9 selection gate: replay Phase 8 routing traces through per-layer slot
caches (LRU as implemented, and Belady-OPT as the offline oracle) at the
observed 1/2/4/6/8 GB budgets.  No inference is run; all data comes from the
existing phase-08 artifacts (moe.csv = routing trace, stats.csv = observed
hit/miss counters for validation, cache_layers.csv = per-layer slot caps and
per-layer expert slice bytes).

Fidelity model (validated against observed stats to 100% on lookups):
  - Steps are detected in moe.csv file order by layer-wrap (layer resets to
    1), matching the runtime's step structure (moe.csv start_pos is not a
    unique step key: the two 2-token prefill batches share start_pos=0).
  - The runtime arms the cache on the FIRST n_slots>0 call via legacy
    placement (cache_enabled() = budget>0 && cache_armed_); that call makes
    no cache lookups and leaves the zc cache empty.  Modeled as a bypass.
  - When a (step, layer) has more unique experts than the layer's persistent
    slot capacity, the zc cache is bypassed entirely (observed lookups=0 on
    the 233-token prefill step); every expert is read from SSD and cache
    state is untouched.
  - Expert SSD cost is per-layer (up+gate+down slice bytes) from
    cache_layers.csv, not a uniform constant.

Answers (ROADMAP Phase 9 selection questions):
  1. actual SSD bytes per generated token (verified from stats.csv);
  2. compulsory/unique loads vs repeated/reloaded loads;
  3. reload amplification (actual / minimum required by the routing trace);
  4. expert reuse-distance distribution;
  5. eviction regret (evicted expert re-requested soon after);
  6. cache-size counterfactuals at ~1/2/4/6/8 GB (same trace, replay only);
  7. LRU vs OPT at those sizes (upper bound from better replacement);
  8. avoidable (caching-fixable) vs compulsory (I/O-efficiency-only) split.

Usage:
  python3 tools/phase09_selection_gate.py
"""

import csv
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "benchmarks", "results")

WORKLOADS = [
    ("coding-lru",  os.path.join(RES, "phase-08", "coding-lru")),
    ("reasoning",   os.path.join(RES, "phase-08", "reasoning")),
    ("ref",         os.path.join(RES, "phase-07b", "baseline")),
]
BUDGETS = ["cap-1", "cap-2", "cap-4", "cap-6", "cap-8"]
REP = "r1"
N_DECODE_TOKENS = {"coding-lru": 128, "reasoning": 128, "ref": 64}

N_LAYERS = 26


def load_moe_trace(moe_path):
    """Return ordered steps: list of dicts {layer: [unique experts in
    first-occurrence order]}.  Step boundary = layer wraps (layer < prev)."""
    steps = []
    cur = None
    prev_layer = 0
    with open(moe_path) as f:
        for row in csv.reader(f):
            if not row or row[0].startswith("#"):
                continue
            layer = int(row[2])
            if cur is None or layer < prev_layer:
                cur = {}
                steps.append(cur)
            experts = [int(x) for x in row[4:12]]
            uniq = cur.setdefault(layer, [])
            seen = set(uniq)
            for e in experts:
                if e not in seen:
                    seen.add(e)
                    uniq.append(e)
            prev_layer = layer
    return steps


def load_caps_and_bytes(cache_layers_path):
    """Per-layer slot capacity (mode of cap_slots) and per-layer expert bytes
    (mode of nonzero up+gate+down)."""
    caps = defaultdict(lambda: defaultdict(int))
    bytes_seen = defaultdict(set)
    with open(cache_layers_path) as f:
        for row in csv.reader(f):
            if not row or row[0].startswith("#") or row[0] == "step":
                continue
            il = int(row[1])
            cap = int(row[2])
            caps[il][cap] += 1
            b = int(row[5]) + int(row[6]) + int(row[7])
            if b > 0:
                bytes_seen[il].add(b)
    cap_out = {}
    byte_out = {}
    for il in range(1, N_LAYERS + 1):
        cap_out[il] = max(caps[il], key=caps[il].get)
        # all nonzero values are identical per layer (verified); take max
        byte_out[il] = max(bytes_seen[il]) if bytes_seen[il] else 0
    return cap_out, byte_out


def load_stats(stats_path):
    out = {}
    with open(stats_path) as f:
        for row in csv.reader(f):
            if not row or row[0].startswith("#") or row[0] == "step":
                continue
            step = int(row[0])
            out[step] = {
                "phase": row[1],
                "n_tokens": int(row[2]),
                "pread_bytes": int(row[4]),
                "lookups": int(row[13]),
                "hits": int(row[14]),
                "misses": int(row[15]),
                "evictions": int(row[16]),
            }
    return out


def effective_requests(trace, caps):
    """The cache-visible request stream: (step_idx, layer, expert) in order,
    excluding (a) the arm call (first n_slots>0 layer) and (b) bypassed
    (step, layer) groups whose unique count exceeds the layer's capacity."""
    reqs = []
    armed = False
    for si, layers in enumerate(trace):
        for il in sorted(layers):
            experts = layers[il]
            if not armed:
                armed = True          # arm call: legacy placement, no zc use
                continue
            if len(experts) > caps[il]:
                continue              # bypass: cache untouched
            for e in experts:
                reqs.append((si, il, e))
    return reqs


def replay_lru(trace, caps, byte_costs):
    """Per-layer LRU over the effective stream.  Returns (per_step,
    classification) with classification = (step, layer, expert, hit,
    compulsory, bypass)."""
    per_step = defaultdict(lambda: {"lookups": 0, "hits": 0, "misses": 0,
                                    "evictions": 0, "bypass": 0, "bytes": 0})
    classification = []
    seen = set()
    order = defaultdict(list)
    present = defaultdict(set)
    armed = False

    for si, layers in enumerate(trace):
        for il in sorted(layers):
            experts = layers[il]
            if not armed:
                armed = True
                for e in experts:
                    key = (il, e)
                    comp = key not in seen
                    seen.add(key)
                    per_step[si]["bypass"] += 1
                    per_step[si]["bytes"] += byte_costs[il]
                    classification.append((si, il, e, False, comp, True))
                continue
            if len(experts) > caps[il]:
                for e in experts:
                    key = (il, e)
                    comp = key not in seen
                    seen.add(key)
                    per_step[si]["bypass"] += 1
                    per_step[si]["bytes"] += byte_costs[il]
                    classification.append((si, il, e, False, comp, True))
                continue
            lst = order[il]
            st = present[il]
            for e in experts:
                key = (il, e)
                comp = key not in seen
                seen.add(key)
                per_step[si]["lookups"] += 1
                if e in st:
                    lst.remove(e)
                    lst.insert(0, e)
                    per_step[si]["hits"] += 1
                    classification.append((si, il, e, True, comp, False))
                else:
                    st.add(e)
                    lst.insert(0, e)
                    per_step[si]["misses"] += 1
                    per_step[si]["bytes"] += byte_costs[il]
                    classification.append((si, il, e, False, comp, False))
                    if len(lst) > caps[il]:
                        victim = lst.pop()
                        st.discard(victim)
                        per_step[si]["evictions"] += 1
    return per_step, classification


def replay_opt(trace, caps, byte_costs):
    """Belady-OPT over the effective stream.  Evict the resident expert whose
    next use in the effective stream is farthest (or never)."""
    reqs = effective_requests(trace, caps)
    # per-layer effective streams and per-layer next-use index lists
    streams = defaultdict(list)
    for si, il, e in reqs:
        streams[il].append((si, e))
    next_table = defaultdict(lambda: defaultdict(list))
    for il, stream in streams.items():
        for idx, (si, e) in enumerate(stream):
            next_table[il][e].append(idx)

    per_step = defaultdict(lambda: {"lookups": 0, "hits": 0, "misses": 0,
                                    "evictions": 0, "bypass": 0, "bytes": 0})
    classification = []
    seen = set()
    present = defaultdict(set)
    ptr = defaultdict(int)
    ri = 0
    armed = False

    for si, layers in enumerate(trace):
        for il in sorted(layers):
            experts = layers[il]
            if not armed:
                armed = True
                for e in experts:
                    key = (il, e)
                    comp = key not in seen
                    seen.add(key)
                    per_step[si]["bypass"] += 1
                    per_step[si]["bytes"] += byte_costs[il]
                    classification.append((si, il, e, False, comp, True))
                continue
            if len(experts) > caps[il]:
                for e in experts:
                    key = (il, e)
                    comp = key not in seen
                    seen.add(key)
                    per_step[si]["bypass"] += 1
                    per_step[si]["bytes"] += byte_costs[il]
                    classification.append((si, il, e, False, comp, True))
                continue
            st = present[il]
            p = ptr[il]
            for e in experts:
                key = (il, e)
                comp = key not in seen
                seen.add(key)
                per_step[si]["lookups"] += 1
                if e in st:
                    per_step[si]["hits"] += 1
                    classification.append((si, il, e, True, comp, False))
                else:
                    st.add(e)
                    per_step[si]["misses"] += 1
                    per_step[si]["bytes"] += byte_costs[il]
                    classification.append((si, il, e, False, comp, False))
                    if len(st) > caps[il]:
                        def nu(x):
                            uses = [u for u in next_table[il].get(x, []) if u > p]
                            return uses[0] if uses else 10**18
                        victim = max(st, key=nu)
                        st.discard(victim)
                        per_step[si]["evictions"] += 1
                p += 1
            ptr[il] = p
    return per_step, classification


def traffic_mb_per_token(per_step, stats, n_decode_tokens):
    decode_bytes = 0
    for si, d in per_step.items():
        st = stats.get(si)
        if st and st["phase"] == "decode":
            decode_bytes += d["bytes"]
    return decode_bytes / 1048576.0 / n_decode_tokens


def observed_decode_mb_per_token(stats, n_decode_tokens):
    b = sum(st["pread_bytes"] for st in stats.values() if st["phase"] == "decode")
    return b / 1048576.0 / n_decode_tokens


def decode_compulsory_mb(trace, byte_costs, stats):
    """Decode-phase compulsory bytes: experts whose FIRST request anywhere in
    the trace occurs in a decode step.  This is the decode traffic floor that
    no cache policy can remove (each such expert must be read at least once)."""
    first_phase = {}
    for si, layers in enumerate(trace):
        phase = stats.get(si, {}).get("phase", "decode")
        for il, experts in layers.items():
            for e in experts:
                if (il, e) not in first_phase:
                    first_phase[(il, e)] = phase
    total = 0
    for (il, e), ph in first_phase.items():
        if ph == "decode":
            total += byte_costs[il]
    return total / 1048576.0


def reuse_distances(trace):
    last = defaultdict(dict)
    dists = []
    for si, layers in enumerate(trace):
        for il in sorted(layers):
            for e in layers[il]:
                if e in last[il]:
                    dists.append(si - last[il][e])
                last[il][e] = si
    return dists


def eviction_regret(trace, caps, byte_costs):
    """True eviction regret: for each LRU eviction, the gap in that layer's
    effective-stream positions until the victim is requested again.  Small
    gap = the victim was evicted too early (a better policy would have kept
    it).  Positions are per-layer effective (cache-visible) requests."""
    reqs = effective_requests(trace, caps)
    streams = defaultdict(list)
    for si, il, e in reqs:
        streams[il].append(e)
    # effective stream positions per layer
    pos = defaultdict(int)
    present = defaultdict(set)
    order = defaultdict(list)
    evictions = []   # (layer, evict_pos, expert)
    armed = False
    for si, layers in enumerate(trace):
        for il in sorted(layers):
            experts = layers[il]
            if not armed:
                armed = True
                continue
            if len(experts) > caps[il]:
                continue
            lst = order[il]
            st = present[il]
            for e in experts:
                p = pos[il]
                if e in st:
                    lst.remove(e)
                    lst.insert(0, e)
                else:
                    st.add(e)
                    lst.insert(0, e)
                    if len(lst) > caps[il]:
                        victim = lst.pop()
                        st.discard(victim)
                        evictions.append((il, p, victim))
                pos[il] += 1
    # next request position per (layer, expert) after each eviction
    next_pos = defaultdict(list)
    for il, stream in streams.items():
        for i, e in enumerate(stream):
            next_pos[(il, e)].append(i)
    gaps = []
    for il, evict_pos, victim in evictions:
        uses = [u for u in next_pos.get((il, victim), []) if u > evict_pos]
        if uses:
            gaps.append(uses[0] - evict_pos)
    if not gaps:
        return {"n": 0, "p50": 0, "p90": 0, "le8": 0, "le26": 0, "le52": 0}
    s = sorted(gaps)
    return {
        "n": len(gaps),
        "p50": s[len(s) // 2],
        "p90": s[int(len(s) * 0.9)],
        "le8": round(sum(1 for g in gaps if g <= 8) / len(gaps), 3),
        "le26": round(sum(1 for g in gaps if g <= 26) / len(gaps), 3),
        "le52": round(sum(1 for g in gaps if g <= 52) / len(gaps), 3),
    }


def percentile(vals, p):
    if not vals:
        return 0
    s = sorted(vals)
    k = min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1))))
    return s[k]


def main():
    out = {}
    for wname, wdir in WORKLOADS:
        if not os.path.isdir(wdir):
            print(f"[skip] {wname}: {wdir} missing", file=sys.stderr)
            continue
        trace_path = os.path.join(wdir, "cap-4", REP, "moe.csv")
        if not os.path.exists(trace_path):
            trace_path = os.path.join(wdir, "cap-4-r1", "moe.csv")
        if not os.path.exists(trace_path):
            print(f"[skip] {wname}: no moe.csv", file=sys.stderr)
            continue
        trace = load_moe_trace(trace_path)
        n_decode = N_DECODE_TOKENS[wname]

        uniq = set()
        for layers in trace:
            for il, experts in layers.items():
                for e in experts:
                    uniq.add((il, e))

        dists = reuse_distances(trace)
        w = {
            "trace_steps": len(trace),
            "n_decode_tokens": n_decode,
            "unique_experts": len(uniq),
            "expert_bytes_by_layer": None,  # filled below per budget run
            "reuse_distance": {
                "n": len(dists),
                "p50": percentile(dists, 50),
                "p90": percentile(dists, 90),
                "p99": percentile(dists, 99),
                "max": max(dists) if dists else 0,
                "frac_le1": round(sum(1 for d in dists if d <= 1) / len(dists), 3) if dists else 0,
                "frac_le8": round(sum(1 for d in dists if d <= 8) / len(dists), 3) if dists else 0,
                "frac_le26": round(sum(1 for d in dists if d <= 26) / len(dists), 3) if dists else 0,
                "frac_le52": round(sum(1 for d in dists if d <= 52) / len(dists), 3) if dists else 0,
            },
            "budgets": {},
        }

        for b in BUDGETS:
            base = os.path.join(wdir, b, REP)
            if not os.path.isdir(base):
                base = os.path.join(wdir, f"{b}-{REP}")
            if not os.path.isdir(base):
                print(f"[skip] {wname}/{b}: missing", file=sys.stderr)
                continue
            caps, byte_costs = load_caps_and_bytes(os.path.join(base, "cache_layers.csv"))
            stats = load_stats(os.path.join(base, "stats.csv"))
            if w["expert_bytes_by_layer"] is None:
                w["expert_bytes_by_layer"] = byte_costs

            per_step_lru, cls_lru = replay_lru(trace, caps, byte_costs)
            per_step_opt, cls_opt = replay_opt(trace, caps, byte_costs)

            obs_miss = sum(st["misses"] for st in stats.values())
            rep_miss = sum(d["misses"] for d in per_step_lru.values())
            obs_hit = sum(st["hits"] for st in stats.values())
            rep_hit = sum(d["hits"] for d in per_step_lru.values())
            obs_look = sum(st["lookups"] for st in stats.values())
            rep_look = sum(d["lookups"] for d in per_step_lru.values())

            compulsory_bytes = sum(byte_costs[il] for (il, e) in uniq)
            dec_comp_mb = decode_compulsory_mb(trace, byte_costs, stats)
            lru_bytes_total = sum(d["bytes"] for d in per_step_lru.values())
            opt_bytes_total = sum(d["bytes"] for d in per_step_opt.values())
            obs_bytes_total = sum(st["pread_bytes"] for st in stats.values())

            regret = eviction_regret(trace, caps, byte_costs)

            w["budgets"][b] = {
                "cap_slots_min": min(caps.values()),
                "cap_slots_max": max(caps.values()),
                "obs_decode_mb_tok": round(observed_decode_mb_per_token(stats, n_decode), 1),
                "lru_decode_mb_tok": round(traffic_mb_per_token(per_step_lru, stats, n_decode), 1),
                "opt_decode_mb_tok": round(traffic_mb_per_token(per_step_opt, stats, n_decode), 1),
                "validation": {
                    "obs_lookups": obs_look,
                    "replay_lookups": rep_look,
                    "obs_misses": obs_miss,
                    "replay_misses": rep_miss,
                    "obs_hits": obs_hit,
                    "replay_hits": rep_hit,
                    "lookup_match_pct": round(100.0 * min(rep_look, obs_look) / max(obs_look, 1), 2),
                    "miss_match_pct": round(100.0 * min(rep_miss, obs_miss) / max(obs_miss, 1), 2),
                    "hit_match_pct": round(100.0 * min(rep_hit, obs_hit) / max(obs_hit, 1), 2),
                },
                "compulsory_mb": round(compulsory_bytes / 1048576, 1),
                "decode_compulsory_mb": round(dec_comp_mb, 1),
                "decode_compulsory_mb_tok": round(dec_comp_mb / n_decode, 1),
                "lru_mb_total": round(lru_bytes_total / 1048576, 1),
                "opt_mb_total": round(opt_bytes_total / 1048576, 1),
                "obs_mb_total": round(obs_bytes_total / 1048576, 1),
                "lru_amplification": round(lru_bytes_total / compulsory_bytes, 3),
                "opt_amplification": round(opt_bytes_total / compulsory_bytes, 3),
                "obs_amplification": round(obs_bytes_total / compulsory_bytes, 3),
                "evict_regret": regret,
                "n_evictions_lru": sum(d["evictions"] for d in per_step_lru.values()),
                "n_evictions_opt": sum(d["evictions"] for d in per_step_opt.values()),
            }
        out[wname] = w

    dst = os.path.join(RES, "phase-09-selection-gate.json")
    with open(dst, "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
