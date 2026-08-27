#!/usr/bin/env python3
"""
Phase 9A offline simulation: prefill -> decode cache continuity.

Models the proposed runtime change for the zero-copy persistent-slot cache:
when a (step, layer) group is oversized for the layer's slot capacity (the
current zc bypass: every expert read from SSD, cache state untouched), the
runtime additionally feeds the LAST min(n_slots, cap) unique experts of that
step (first-occurrence order) through the layer's LRU cache.  This warms the
persistent slots with the prefill tail so decode starts with a full cache
instead of only the ~8 prompt tokens from the small batches.

Fidelity: identical to phase09_selection_gate.py's replay_lru except for the
oversized-group handling (feed tail + count warm evictions).  The arm-call
bypass is unchanged (first n_slots>0 layer runs legacy placement, no zc use,
cache left empty) so the validated Phase 8 lookup/miss/hit replay still
matches the observed pre-change stats.

Answers:
  1. decode MB/token with warm start vs the observed cold-start LRU and OPT;
  2. decode-start cache occupancy (slots/layer used at first decode step)
     with and without warming;
  3. first-decode-step (and early-decode) hit lift;
  4. prefill warm evictions (batch entries replaced by the prefill tail).

Usage:
  python3 tools/phase09a_warm_sim.py [--json out.json]
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from phase09_selection_gate import (  # noqa: E402
    load_moe_trace,
    load_caps_and_bytes,
    load_stats,
    replay_lru,
    replay_opt,
    traffic_mb_per_token,
    observed_decode_mb_per_token,
    N_DECODE_TOKENS,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "benchmarks", "results")

WORKLOADS = [
    ("coding-lru", os.path.join(RES, "phase-08", "coding-lru")),
    ("reasoning", os.path.join(RES, "phase-08", "reasoning")),
    ("ref", os.path.join(RES, "phase-07b", "baseline")),
]
BUDGETS = ["cap-1", "cap-2", "cap-4", "cap-6", "cap-8"]
REP = "r1"


def replay_lru_warm(trace, caps, byte_costs):
    """LRU replay with prefill-tail warming on oversized (bypassed) groups.

    Semantics per group:
      - arm call (first n_slots>0 layer): pure bypass, cache untouched.
      - oversized group (len(experts) > caps[il]): all bytes counted as
        bypass; afterwards feed the last caps[il] unique experts (stream
        order) through the LRU (touches + inserts + evictions).
      - normal group: identical to replay_lru.
    Returns (per_step, classification) with the same shapes as replay_lru;
    warm-fed requests are classified (hit, comp, False) with lookups NOT
    incremented (the runtime performs no cache lookups on bypassed steps —
    warming is bookkeeping placement of already-pread bytes).
    """
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
                # ---- Phase 9A warm: feed the prefill tail through LRU ----
                lst = order[il]
                st = present[il]
                tail = experts[-caps[il]:]
                for e in tail:
                    if e in st:
                        lst.remove(e)
                        lst.insert(0, e)
                    else:
                        st.add(e)
                        lst.insert(0, e)
                        if len(lst) > caps[il]:
                            victim = lst.pop()
                            st.discard(victim)
                            per_step[si]["evictions"] += 1
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


def decode_start_occupancy(trace, caps, warm, stats=None):
    """Proper occupancy: per-layer set of resident experts at the first
    decode step (state after ALL prefill steps, none of the decode steps).
    Replays the same stream semantics as replay_lru(_warm).  stats is used
    to detect the first decode step; when None, all steps are processed."""
    present = defaultdict(set)
    order = defaultdict(list)
    armed = False
    for si, layers in enumerate(trace):
        if stats is not None and stats.get(si, {}).get("phase") == "decode":
            break
        for il in sorted(layers):
            experts = layers[il]
            if not armed:
                armed = True
                continue
            if len(experts) > caps[il]:
                if warm:
                    lst = order[il]
                    st = present[il]
                    for e in experts[-caps[il]:]:
                        if e in st:
                            lst.remove(e)
                            lst.insert(0, e)
                        else:
                            st.add(e)
                            lst.insert(0, e)
                            if len(lst) > caps[il]:
                                victim = lst.pop()
                                st.discard(victim)
                continue
            lst = order[il]
            st = present[il]
            for e in experts:
                if e in st:
                    lst.remove(e)
                    lst.insert(0, e)
                else:
                    st.add(e)
                    lst.insert(0, e)
                    if len(lst) > caps[il]:
                        victim = lst.pop()
                        st.discard(victim)
    return {il: len(present[il]) for il in present}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(RES, "phase-09a-warm-sim.json"))
    args = ap.parse_args()
    out = {}
    for wname, wdir in WORKLOADS:
        if not os.path.isdir(wdir):
            print(f"[skip] {wname}: {wdir} missing", file=sys.stderr)
            continue
        tp = os.path.join(wdir, "cap-4", REP, "moe.csv")
        if not os.path.exists(tp):
            tp = os.path.join(wdir, "cap-4-r1", "moe.csv")
        if not os.path.exists(tp):
            print(f"[skip] {wname}: no moe.csv", file=sys.stderr)
            continue
        trace = load_moe_trace(tp)
        n_decode = N_DECODE_TOKENS[wname]
        w = {"trace_steps": len(trace), "n_decode_tokens": n_decode, "budgets": {}}
        for b in BUDGETS:
            base = os.path.join(wdir, b, REP)
            if not os.path.isdir(base):
                base = os.path.join(wdir, f"{b}-{REP}")
            if not os.path.isdir(base):
                print(f"[skip] {wname}/{b}: missing", file=sys.stderr)
                continue
            caps, byte_costs = load_caps_and_bytes(os.path.join(base, "cache_layers.csv"))
            stats = load_stats(os.path.join(base, "stats.csv"))

            per_step_lru, _ = replay_lru(trace, caps, byte_costs)
            per_step_warm, cls_warm = replay_lru_warm(trace, caps, byte_costs)
            per_step_opt, _ = replay_opt(trace, caps, byte_costs)

            obs = observed_decode_mb_per_token(stats, n_decode)
            lru = traffic_mb_per_token(per_step_lru, stats, n_decode)
            warm = traffic_mb_per_token(per_step_warm, stats, n_decode)
            opt = traffic_mb_per_token(per_step_opt, stats, n_decode)

            occ_cold = decode_start_occupancy(trace, caps, warm=False, stats=stats)
            occ_warm = decode_start_occupancy(trace, caps, warm=True, stats=stats)

            # decode hit-rate with/without warm (decode steps only)
            def decode_hits(per_step):
                h = m = 0
                for si, d in per_step.items():
                    if stats.get(si, {}).get("phase") == "decode":
                        h += d["hits"]
                        m += d["misses"]
                return h, m

            h_cold, m_cold = decode_hits(per_step_lru)
            h_warm, m_warm = decode_hits(per_step_warm)

            # first decode step hit count (26 layers)
            first_dec = next(si for si, st in stats.items() if st["phase"] == "decode")
            fh_cold = per_step_lru[first_dec]["hits"]
            fh_warm = per_step_warm[first_dec]["hits"]

            # prefill warm evictions (all prefill steps)
            pre_evict_cold = sum(d["evictions"] for si, d in per_step_lru.items()
                                 if stats.get(si, {}).get("phase") == "prefill")
            pre_evict_warm = sum(d["evictions"] for si, d in per_step_warm.items()
                                 if stats.get(si, {}).get("phase") == "prefill")

            w["budgets"][b] = {
                "cap_slots_min": min(caps.values()),
                "cap_slots_max": max(caps.values()),
                "obs_decode_mb_tok": round(obs, 1),
                "lru_cold_decode_mb_tok": round(lru, 1),
                "lru_warm_decode_mb_tok": round(warm, 1),
                "opt_decode_mb_tok": round(opt, 1),
                "warm_vs_obs_pct": round(100.0 * (obs - warm) / obs, 1),
                "warm_vs_lru_pct": round(100.0 * (lru - warm) / lru, 1),
                "gap_recovered_pct": round(100.0 * (lru - warm) / max(lru - opt, 1e-9), 1),
                "decode_start_occ_cold": round(sum(occ_cold.values()) / len(occ_cold), 1),
                "decode_start_occ_warm": round(sum(occ_warm.values()) / len(occ_warm), 1),
                "decode_start_occ_cold_slots": {il: occ_cold.get(il, 0) for il in range(1, 27)},
                "decode_start_occ_warm_slots": {il: occ_warm.get(il, 0) for il in range(1, 27)},
                "decode_hit_rate_cold": round(h_cold / max(h_cold + m_cold, 1), 4),
                "decode_hit_rate_warm": round(h_warm / max(h_warm + m_warm, 1), 4),
                "first_decode_step_hits_cold": fh_cold,
                "first_decode_step_hits_warm": fh_warm,
                "prefill_evictions_cold": pre_evict_cold,
                "prefill_evictions_warm": pre_evict_warm,
            }
        out[wname] = w
    with open(args.json, "w") as f:
        json.dump(out, f, indent=2)
    # concise console table
    for wname, w in out.items():
        print(f"== {wname} ==")
        for b, d in w["budgets"].items():
            print(f"  {b}: obs={d['obs_decode_mb_tok']:7.1f}  lru={d['lru_cold_decode_mb_tok']:7.1f}  "
                  f"warm={d['lru_warm_decode_mb_tok']:7.1f}  opt={d['opt_decode_mb_tok']:7.1f}  "
                  f"warmΔ={d['warm_vs_obs_pct']:5.1f}%  gapRec={d['gap_recovered_pct']:5.1f}%  "
                  f"occ {d['decode_start_occ_cold']:.0f}->{d['decode_start_occ_warm']:.0f}  "
                  f"hit {d['decode_hit_rate_cold']:.3f}->{d['decode_hit_rate_warm']:.3f}  "
                  f"firstDecHits {d['first_decode_step_hits_cold']}->{d['first_decode_step_hits_warm']}")


if __name__ == "__main__":
    main()
