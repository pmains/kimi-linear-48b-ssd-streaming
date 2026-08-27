#!/usr/bin/env python3
"""
Phase 9B policy-selection gate: offline trace replay of a deliberately small
family of ONLINE replacement policies against the Phase 8 routing traces, at
the observed 1/2/4/6/8 GB budgets.  No inference is run; no llama.cpp code is
modified.  Primary baseline = frozen Phase 8 LRU behavior (validated replay).

Fidelity model (identical to phase09_selection_gate.py, which is validated
100% against observed Phase 8 stats on lookups/hits/misses):
  - Steps are detected in moe.csv file order by layer-wrap.
  - Arm call (first n_slots>0 layer): legacy placement, pure bypass, cache
    state untouched.
  - Oversized (step, layer) groups (unique experts > per-layer slot cap):
    full bypass, cache state untouched (observed lookups=0 on the 233-token
    prefill step).
  - Per-layer expert SSD cost from cache_layers.csv (up+gate+down).

Policies (all strictly online — past information only):
  lru        control; frozen Phase 8 behavior.
  clock      CLOCK second-chance (1 ref bit, hand pointer).
  twoq       2Q (Johnson & Shasha): A1in FIFO (25% cap), A1out ghost
             (50% cap, ids only), Am LRU (rest).  First touch -> A1in;
             A1in hit -> promote to Am; A1out ghost hit -> miss + Am insert;
             else miss + A1in insert.  Combined admission + eviction.
  slru       segmented LRU: probation + protected LRUs; probation hit
             promotes to protected (demote protected LRU to probation).
             Protected share parameterized (0.25 / 0.50 / 0.75).
  lfu_da     LFU with dynamic aging: saturating frequency counters, halve all
             counts when max hits the cap (255); evict min frequency, LRU
             tiebreak.  Counts every read (incl. bypass/arm) like a natural
             runtime counter would.
  lru_admit  LRU eviction + admission gate: admit only on 2nd READ overall
             (seen set = any read, incl. prefill bypass).  Isolates the
             admission lever on top of the baseline eviction policy.
  lru_admit2 LRU eviction + admission gate: admit only on 2nd CACHE-VISIBLE
             read (prefill-bypass reads do NOT count).  Blocks single-decode-
             touch pollution including prefill-once/decode-once experts.
  opt        Belady-OPT bound (offline, eviction-side upper bound).
  opt_admit2 Belady-OPT eviction + same 2nd-cache-visible-read admission
             gate (bound: what admission alone could add under an oracle
             eviction policy).

Secondary condition: the Phase 9A warm start (prefill-tail feeding on
oversized groups, known-negative for LRU) is replayed for every policy at
4 GB (primary operating point) and for the recommended policy at every
budget, to test policy x warm-start interaction.

No oracle leakage: every candidate policy uses only accesses that occurred
before the current request.  OPT is computed as a bound only and is never a
candidate.

Output: benchmarks/results/phase-09b/policy-gate.json + console tables.

Usage:
  python3 tools/phase09b_policy_gate.py [--json out.json]
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "benchmarks", "results")

WORKLOADS = [
    ("coding-lru", os.path.join(RES, "phase-08", "coding-lru")),
    ("reasoning", os.path.join(RES, "phase-08", "reasoning")),
    ("ref", os.path.join(RES, "phase-07b", "baseline")),
]
BUDGETS = ["cap-1", "cap-2", "cap-4", "cap-6", "cap-8"]
REP = "r1"
N_DECODE_TOKENS = {"coding-lru": 128, "reasoning": 128, "ref": 64}
N_LAYERS = 26

# ---------------------------------------------------------------------------
# trace / artifact loading (shared with the phase 9 selection gate)
# ---------------------------------------------------------------------------


def load_moe_trace(moe_path):
    """Ordered steps: list of {layer: [unique experts, first-occurrence]}."""
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
    """Per-layer slot capacity (mode) and per-layer expert bytes (mode)."""
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
    cap_out, byte_out = {}, {}
    for il in range(1, N_LAYERS + 1):
        cap_out[il] = max(caps[il], key=caps[il].get)
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


# ---------------------------------------------------------------------------
# policy implementations (per-layer state, strictly online)
# ---------------------------------------------------------------------------

class PolicyLRU:
    name = "lru"
    label = "LRU (Phase 8 baseline)"
    metadata = "1 list position per resident entry; O(1)/request"
    complexity = "existing runtime behavior; zero new code"

    def __init__(self, cap):
        self.cap = cap
        self.order = []          # MRU..LRU
        self.present = set()

    def request(self, e):
        """Returns (hit, evicted_or_None)."""
        if e in self.present:
            self.order.remove(e)
            self.order.insert(0, e)
            return True, None
        self.present.add(e)
        self.order.insert(0, e)
        victim = None
        if len(self.order) > self.cap:
            victim = self.order.pop()
            self.present.discard(victim)
        return False, victim

    def feed(self, e):
        """Warm placement (no lookup accounting): touch-or-insert."""
        return self.request(e)[1]


class PolicyCLOCK(PolicyLRU):
    name = "clock"
    label = "CLOCK (second chance)"
    metadata = "1 ref bit + hand index per layer; O(1) amortized, worst-case scan cap"
    complexity = "small: ref bit + hand per layer; ~40-60 lines in runtime"

    def __init__(self, cap):
        self.cap = cap
        self.ring = []            # circular buffer of entries
        self.refs = {}            # e -> ref bit
        self.hand = 0

    def request(self, e):
        if e in self.refs:
            self.refs[e] = 1
            return True, None
        if len(self.ring) < self.cap:
            self.refs[e] = 1
            self.ring.append(e)
            return False, None
        # full: scan from hand for a ref=0 entry
        n = len(self.ring)
        victim = None
        while True:
            cand = self.ring[self.hand]
            if self.refs[cand] == 0:
                victim = cand
                self.ring[self.hand] = e
                self.refs[e] = 1
                del self.refs[victim]
                self.hand = (self.hand + 1) % n
                return False, victim
            self.refs[cand] = 0
            self.hand = (self.hand + 1) % n

    def feed(self, e):
        if e in self.refs:
            self.refs[e] = 1
            return None
        if len(self.ring) < self.cap:
            self.refs[e] = 1
            self.ring.append(e)
            return None
        n = len(self.ring)
        while True:
            cand = self.ring[self.hand]
            if self.refs[cand] == 0:
                victim = cand
                self.ring[self.hand] = e
                self.refs[e] = 1
                del self.refs[victim]
                self.hand = (self.hand + 1) % n
                return victim
            self.refs[cand] = 0
            self.hand = (self.hand + 1) % n


class Policy2Q(PolicyLRU):
    name = "twoq"
    label = "2Q (A1in FIFO + A1out ghost + Am LRU)"
    metadata = "queue tags + links per resident; ghost = ids only (50% cap); O(1)/request"
    complexity = "moderate: 3 structures per layer; ~80-120 lines"

    def __init__(self, cap):
        self.cap = cap
        self.kin_cap = max(1, cap // 4)
        self.km_cap = cap - self.kin_cap
        self.kout_cap = cap // 2          # ghost capacity (ids only)
        self.kin = []                     # FIFO (tail = newest)
        self.km = []                      # LRU (head = MRU)
        self.kout = []                    # ghost recency (head = newest)
        self.in_kin = set()
        self.in_km = set()
        self.in_kout = set()

    def request(self, e):
        if e in self.in_km:
            self.km.remove(e)
            self.km.insert(0, e)
            return True, None
        if e in self.in_kin:
            # promote to Am
            self.kin.remove(e)
            self.in_kin.discard(e)
            self.km.insert(0, e)
            self.in_km.add(e)
            victim = None
            if len(self.km) > self.km_cap:
                victim = self.km.pop()
                self.in_km.discard(victim)
            return True, victim
        if e in self.in_kout:
            # ghost hit: miss, re-admit to Am
            self.kout.remove(e)
            self.in_kout.discard(e)
            self.km.insert(0, e)
            self.in_km.add(e)
            victim = None
            if len(self.km) > self.km_cap:
                victim = self.km.pop()
                self.in_km.discard(victim)
            return False, victim
        # cold miss: insert into A1in FIFO
        self.kin.append(e)
        self.in_kin.add(e)
        victim = None
        if len(self.kin) > self.kin_cap:
            ghost = self.kin.pop(0)
            self.in_kin.discard(ghost)
            self.kout.insert(0, ghost)
            self.in_kout.add(ghost)
            if len(self.kout) > self.kout_cap:
                dropped = self.kout.pop()
                self.in_kout.discard(dropped)
            victim = ghost
        return False, victim

    def feed(self, e):
        # warm placement routes through the normal cold-miss path
        return self.request(e)[1]


class PolicySLRU(PolicyLRU):
    name = "slru"
    label = "SLRU (probation + protected, protected={frac})"
    metadata = "1 segment bit + list position per resident; O(1)/request"
    complexity = "moderate: split LRU list; ~60-80 lines"

    def __init__(self, cap, frac=0.50):
        self.cap = cap
        self.prot_cap = max(1, int(round(cap * frac)))
        self.prob_cap = cap - self.prot_cap
        self.prob = []           # LRU (head = MRU)
        self.prot = []           # LRU (head = MRU)
        self.in_prob = set()
        self.in_prot = set()

    def request(self, e):
        if e in self.in_prot:
            self.prot.remove(e)
            self.prot.insert(0, e)
            return True, None
        if e in self.in_prob:
            # promote to protected
            self.prob.remove(e)
            self.in_prob.discard(e)
            self.prot.insert(0, e)
            self.in_prot.add(e)
            victim = None
            if len(self.prot) > self.prot_cap:
                demoted = self.prot.pop()
                self.in_prot.discard(demoted)
                self.prob.insert(0, demoted)
                self.in_prob.add(demoted)
                if len(self.prob) > self.prob_cap:
                    victim = self.prob.pop()
                    self.in_prob.discard(victim)
            return True, victim
        # miss: insert into probation
        self.prob.insert(0, e)
        self.in_prob.add(e)
        victim = None
        if len(self.prob) > self.prob_cap:
            victim = self.prob.pop()
            self.in_prob.discard(victim)
        return False, victim

    def feed(self, e):
        return self.request(e)[1]


class PolicyLFUDA(PolicyLRU):
    name = "lfu_da"
    label = "LFU with dynamic aging (8-bit counters, halve at saturation)"
    metadata = "1 saturating byte + recency tiebreak per resident; bucket lists O(1); aging pass O(cap) amortized"
    complexity = "moderate-high: counters + aging + buckets; ~80-120 lines"

    MAXF = 255

    def __init__(self, cap):
        self.cap = cap
        self.freq = {}           # e -> count (counts EVERY read incl. bypass)
        self.seq = {}            # e -> last touch sequence
        self.order = []          # resident list (any order; tiebreak uses seq)
        self.present = set()
        self.t = 0

    def _read_count(self, e):
        """Frequency counter for any read (bypass/arm/miss/hit)."""
        if e in self.freq:
            if self.freq[e] < self.MAXF:
                self.freq[e] += 1
        else:
            self.freq[e] = 1

    def _age(self):
        for e in self.freq:
            self.freq[e] = max(1, self.freq[e] // 2)

    def observe_read(self, e):
        """Count non-cache-visible reads (arm/bypass) in the frequency table."""
        self._read_count(e)

    def request(self, e):
        self.t += 1
        self._read_count(e)
        if e in self.present:
            self.seq[e] = self.t
            if self.freq[e] >= self.MAXF:
                self._age()
            return True, None
        # miss: insert
        self.present.add(e)
        self.order.append(e)
        self.seq[e] = self.t
        victim = None
        if len(self.order) > self.cap:
            # evict min (freq, seq): lowest frequency, oldest tiebreak
            victim = min(self.order, key=lambda x: (self.freq.get(x, 1), self.seq.get(x, 0)))
            self.order.remove(victim)
            self.present.discard(victim)
        if self.freq.get(e, 1) >= self.MAXF:
            self._age()
        return False, victim

    def feed(self, e):
        if e in self.present:
            self.seq[e] = self.t
            return None
        self.t += 1
        self.present.add(e)
        self.order.append(e)
        self.seq[e] = self.t
        if e not in self.freq:
            self.freq[e] = 1
        victim = None
        if len(self.order) > self.cap:
            victim = min(self.order, key=lambda x: (self.freq.get(x, 1), self.seq.get(x, 0)))
            self.order.remove(victim)
            self.present.discard(victim)
        return victim


class PolicyLFUDA2(PolicyLFUDA):
    """LFU with dynamic aging proper (Arlitt-style): when the AVERAGE resident
    frequency exceeds a threshold, subtract the minimum resident frequency from
    every counter (or halve).  Stale all-time hotties decay so the CURRENT
    working set wins the slots.  Counts every read (incl. bypass/arm)."""

    name = "lfu_da2"
    label = "LFU with dynamic aging (avg-threshold subtract-min)"
    complexity = "moderate-high: counters + aging + buckets; ~80-120 lines"

    def __init__(self, cap, avg_thresh=8.0):
        super().__init__(cap)
        self.avg_thresh = avg_thresh

    def _maybe_age(self):
        if not self.present:
            return
        avg = sum(self.freq.get(e, 1) for e in self.present) / len(self.present)
        if avg > self.avg_thresh:
            mn = min(self.freq.get(e, 1) for e in self.present)
            if mn > 1:
                for e in self.present:
                    self.freq[e] = max(1, self.freq[e] - mn)
            else:
                for e in self.present:
                    self.freq[e] = max(1, self.freq[e] // 2)

    def request(self, e):
        self.t += 1
        self._read_count(e)
        if e in self.present:
            self.seq[e] = self.t
            self._maybe_age()
            return True, None
        self.present.add(e)
        self.order.append(e)
        self.seq[e] = self.t
        victim = None
        if len(self.order) > self.cap:
            victim = min(self.order, key=lambda x: (self.freq.get(x, 1), self.seq.get(x, 0)))
            self.order.remove(victim)
            self.present.discard(victim)
        self._maybe_age()
        return False, victim

    def feed(self, e):
        if e in self.present:
            self.seq[e] = self.t
            return None
        self.t += 1
        self.present.add(e)
        self.order.append(e)
        self.seq[e] = self.t
        if e not in self.freq:
            self.freq[e] = 1
        victim = None
        if len(self.order) > self.cap:
            victim = min(self.order, key=lambda x: (self.freq.get(x, 1), self.seq.get(x, 0)))
            self.order.remove(victim)
            self.present.discard(victim)
        self._maybe_age()
        return victim


class PolicyLRU2(PolicyLRU):
    """LRU-2 (O'Neil et al.): evict the resident entry whose SECOND-most-recent
    reference is oldest; entries referenced once rank below all twice-referenced
    entries (tiebreak by first-reference age).  Exact online approximation of
    the 'repeat users' discriminator; no future knowledge."""

    name = "lru2"
    label = "LRU-2 (2nd-reference recency)"
    metadata = "2 timestamps per resident + priority structure; O(log cap)/request"
    complexity = "moderate-high: per-layer heap/2-level lists; ~100-150 lines"

    def __init__(self, cap):
        self.cap = cap
        self.refs = {}           # e -> [t_most_recent, t_second_most_recent] (None if only 1 ref)
        self.present = set()
        self.t = 0

    def _key(self, e):
        r = self.refs.get(e)
        if r is None:
            return (-1, 0)
        t2 = r[1]
        if t2 is None:
            return (0, r[0])     # single reference: evict before any double
        return (1, t2)           # double reference: older 2nd-ref evicted first

    def request(self, e):
        self.t += 1
        if e in self.present:
            r = self.refs[e]
            r[1] = r[0]
            r[0] = self.t
            return True, None
        self.present.add(e)
        self.refs[e] = [self.t, None]
        victim = None
        if len(self.present) > self.cap:
            victim = min(self.present, key=self._key)
            self.present.discard(victim)
            del self.refs[victim]
        return False, victim

    def feed(self, e):
        if e in self.present:
            self.refs[e][1] = self.refs[e][0]
            self.refs[e][0] = self.t
            return None
        self.t += 1
        self.present.add(e)
        self.refs[e] = [self.t, None]
        victim = None
        if len(self.present) > self.cap:
            victim = min(self.present, key=self._key)
            self.present.discard(victim)
            del self.refs[victim]
        return victim


class PolicyLIRS(PolicyLRU):
    """LIRS (Jiang & Zhang 2002): Low Inter-reference Recency Set.
    Stack S keeps recency order (LIR pages + HIR history); a small FIFO Q
    holds HIR residents (new/scan pages).  A page becomes LIR on its 2nd
    access; the bottom LIR is demoted to HIR when the LIR set exceeds L.
    Designed for scan resistance while retaining recency: new pages cycle
    through Q, repeat users are retained in the LIR set by recency.
    No future knowledge."""

    name = "lirs"
    label = "LIRS (low inter-reference recency set)"
    metadata = "stack entry (id + 1 bit) + small FIFO; O(1) amortized; stack bounded by pruning"
    complexity = "moderate-high: stack + Q; ~100-150 lines"

    def __init__(self, cap, hir_frac=0.05, stack_cap=None):
        self.cap = cap
        self.H = max(1, int(round(cap * hir_frac)))
        self.L = cap - self.H
        self.stack = []           # top=0 (MRU); entries: e (we track type via sets)
        self.lir = set()          # resident LIR pages
        self.hir_res = []         # FIFO of resident HIR pages (tail = newest)
        self.hir_res_set = set()
        self.stack_set = set()
        self.stack_cap = stack_cap or max(64, 4 * cap)

    def _prune(self):
        while self.stack and self.stack[-1] not in self.lir and \
                self.stack[-1] not in self.hir_res_set:
            e = self.stack.pop()
            self.stack_set.discard(e)
        while len(self.stack) > self.stack_cap:
            e = self.stack.pop()
            self.stack_set.discard(e)
            self.lir.discard(e)
            if e in self.hir_res_set:
                self.hir_res.remove(e)
                self.hir_res_set.discard(e)

    def _evict_hir(self):
        victim = None
        while self.hir_res:
            v = self.hir_res.pop(0)
            self.hir_res_set.discard(v)
            if v in self.stack_set:
                victim = v          # stays in stack as HIR history
                break
            victim = v
            break
        return victim

    def request(self, e):
        if e in self.stack_set:
            # move to top
            self.stack.remove(e)
            self.stack.insert(0, e)
            if e in self.lir:
                self._prune()
                return True, None
            # HIR (resident or history): miss -> promote to LIR
            if e in self.hir_res_set:
                self.hir_res.remove(e)
                self.hir_res_set.discard(e)
            self.lir.add(e)
            victim = None
            if len(self.lir) > self.L:
                # demote bottom-most LIR to HIR resident
                for x in reversed(self.stack):
                    if x in self.lir:
                        self.lir.discard(x)
                        self.hir_res.append(x)
                        self.hir_res_set.add(x)
                        if len(self.hir_res) > self.H:
                            victim = self._evict_hir()
                        break
            self._prune()
            return False, victim
        # not in stack: miss, enter as HIR resident + stack history
        self.stack.insert(0, e)
        self.stack_set.add(e)
        self.hir_res.append(e)
        self.hir_res_set.add(e)
        victim = None
        if len(self.hir_res) > self.H:
            victim = self._evict_hir()
        self._prune()
        return False, victim

    def feed(self, e):
        if e in self.lir or e in self.hir_res_set:
            # treat as a hit: refresh recency
            if e in self.stack_set:
                self.stack.remove(e)
                self.stack.insert(0, e)
            if e in self.lir:
                self._prune()
                return None
            # HIR resident: promote like a hit would
            self.hir_res.remove(e)
            self.hir_res_set.discard(e)
            self.lir.add(e)
            victim = None
            if len(self.lir) > self.L:
                for x in reversed(self.stack):
                    if x in self.lir:
                        self.lir.discard(x)
                        self.hir_res.append(x)
                        self.hir_res_set.add(x)
                        if len(self.hir_res) > self.H:
                            victim = self._evict_hir()
                        break
            self._prune()
            return victim
        # cold: insert as HIR resident
        self.stack.insert(0, e)
        self.stack_set.add(e)
        self.hir_res.append(e)
        self.hir_res_set.add(e)
        victim = None
        if len(self.hir_res) > self.H:
            victim = self._evict_hir()
        self._prune()
        return victim


class PolicyLRUF1(PolicyLRU):
    """LRU with single-reference-first eviction: keep the FULL recency order
    (no first-touch pool split), but when a victim is needed, scan from the
    LRU tail toward MRU and evict the first entry that has been referenced
    only once in the cache-visible stream; fall back to the LRU tail if none
    is found within the scan bound.  Protects quiet repeat users (the
    opt-only population) without shrinking the recency pool.
    Metadata: 1 freq bit per resident + scan bound.  O(scan) worst-case."""

    name = "lru_f1"
    label = "LRU + single-reference-first eviction"
    metadata = "1 freq>=2 bit per resident; O(scan) worst-case per eviction"
    complexity = "small: victim-scan tweak on the existing LRU list; ~30-50 lines"

    def __init__(self, cap, scan_bound=None):
        super().__init__(cap)
        self.two_ref = set()          # resident entries referenced >= 2x (cache-visible)
        self.seen = set()             # cache-visible seen (any read)
        self.scan_bound = scan_bound if scan_bound is not None else max(4, cap // 2)

    def request(self, e):
        if e in self.present:
            self.order.remove(e)
            self.order.insert(0, e)
            if e in self.seen:
                self.two_ref.add(e)
            else:
                self.seen.add(e)
            return True, None
        self.present.add(e)
        self.order.insert(0, e)
        if e in self.seen:
            self.two_ref.add(e)
        else:
            self.seen.add(e)
        victim = None
        if len(self.order) > self.cap:
            victim = self._pick_victim()
            self.order.remove(victim)
            self.present.discard(victim)
            self.two_ref.discard(victim)
        return False, victim

    def _pick_victim(self):
        tail = self.order[-1]
        n = min(self.scan_bound, len(self.order))
        for i in range(1, n + 1):
            cand = self.order[-i]
            if cand not in self.two_ref:
                return cand
        return tail

    def feed(self, e):
        if e in self.present:
            self.order.remove(e)
            self.order.insert(0, e)
            if e in self.seen:
                self.two_ref.add(e)
            return None
        self.present.add(e)
        self.order.insert(0, e)
        if e in self.seen:
            self.two_ref.add(e)
        else:
            self.seen.add(e)
        victim = None
        if len(self.order) > self.cap:
            victim = self._pick_victim()
            self.order.remove(victim)
            self.present.discard(victim)
            self.two_ref.discard(victim)
        return victim


class PolicyLRUAdmit(PolicyLRU):
    """LRU eviction + admit-on-2nd-read admission gate.
    count_all=True: seen set includes bypass/arm reads (prefill counts).
    count_all=False: seen set counts cache-visible reads only.
    Semantics: the FIRST read of an expert is always a miss and is NOT
    inserted; the SECOND read admits it.  Hits refresh recency."""

    def __init__(self, cap, count_all=True):
        super().__init__(cap)
        self.count_all = count_all
        self.seen = set()

    def observe_read(self, e):
        """Called by the driver for non-cache-visible reads (arm/bypass)."""
        if self.count_all:
            self.seen.add(e)

    def request(self, e):
        if e in self.present:
            self.order.remove(e)
            self.order.insert(0, e)
            self.seen.add(e)
            return True, None
        first_read = e not in self.seen
        self.seen.add(e)
        if first_read:
            return False, None          # read but not admitted
        # 2nd (or later) read: admit
        self.present.add(e)
        self.order.insert(0, e)
        victim = None
        if len(self.order) > self.cap:
            victim = self.order.pop()
            self.present.discard(victim)
        return False, victim

    def feed(self, e):
        # warm placement: forced (bytes already read), bypasses the gate
        self.seen.add(e)
        return super().request(e)[1]


class PolicyLRUAdmit2(PolicyLRUAdmit):
    """Admission gate counts cache-visible reads only (prefill bypass reads
    do not count): blocks single-decode-touch pollution including
    prefill-once/decode-once experts."""

    name = "lru_admit2"
    label = "LRU + 2nd cache-visible-read admission gate"

    def __init__(self, cap):
        super().__init__(cap, count_all=False)

    def observe_read(self, e):
        pass                            # bypass/arm reads do not count


# ---------------------------------------------------------------------------
# generic replay driver (arm/bypass semantics identical to validated replay)
# ---------------------------------------------------------------------------

def replay_policy(trace, caps, byte_costs, policy_factory, warm=False,
                  stats=None):
    """Replay a policy over the trace.  Returns (per_step, classification,
    evictions) where evictions = [(il, pos, e_victim)] with pos = the layer's
    effective-stream position of the request that caused the eviction, and
    classification = [(si, il, e, hit, compulsory, bypass)] (warm placements
    are NOT in classification; they are bookkeeping placement only)."""
    per_step = defaultdict(lambda: {"lookups": 0, "hits": 0, "misses": 0,
                                    "evictions": 0, "bypass": 0, "bytes": 0})
    classification = []
    evictions = []
    seen = set()                 # compulsory tracking: any read marks seen
    pos = defaultdict(int)       # per-layer effective-stream position
    pol = {il: policy_factory(caps[il]) for il in range(1, N_LAYERS + 1)}
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
                    if hasattr(pol[il], "observe_read"):
                        pol[il].observe_read(e)
                    per_step[si]["bypass"] += 1
                    per_step[si]["bytes"] += byte_costs[il]
                    classification.append((si, il, e, False, comp, True))
                continue
            if len(experts) > caps[il]:
                for e in experts:
                    key = (il, e)
                    comp = key not in seen
                    seen.add(key)
                    if hasattr(pol[il], "observe_read"):
                        pol[il].observe_read(e)
                    per_step[si]["bypass"] += 1
                    per_step[si]["bytes"] += byte_costs[il]
                    classification.append((si, il, e, False, comp, True))
                if warm:
                    # 9A warm: feed the tail through the policy's placement
                    p = pol[il]
                    for e in experts[-caps[il]:]:
                        victim = p.feed(e)
                        if victim is not None:
                            per_step[si]["evictions"] += 1
                            evictions.append((il, pos[il], victim))
                continue
            p = pol[il]
            for e in experts:
                key = (il, e)
                comp = key not in seen
                seen.add(key)
                per_step[si]["lookups"] += 1
                hit, victim = p.request(e)
                if hit:
                    per_step[si]["hits"] += 1
                    classification.append((si, il, e, True, comp, False))
                else:
                    per_step[si]["misses"] += 1
                    per_step[si]["bytes"] += byte_costs[il]
                    classification.append((si, il, e, False, comp, False))
                    if victim is not None:
                        per_step[si]["evictions"] += 1
                        evictions.append((il, pos[il], victim))
                pos[il] += 1
    return per_step, classification, evictions


# ---------------------------------------------------------------------------
# OPT bound (offline) with optional 2nd-cache-visible-read admission gate
# ---------------------------------------------------------------------------

def replay_opt(trace, caps, byte_costs, admit_gate=False, warm=False):
    """Belady-OPT over the effective stream.  Evict resident expert with
    farthest next use.  admit_gate=True additionally refuses admission on the
    first cache-visible read (admission bound).  warm=True feeds oversized
    prefill tails as free placements (9A condition)."""
    reqs = []
    armed = False
    for si, layers in enumerate(trace):
        for il in sorted(layers):
            experts = layers[il]
            if not armed:
                armed = True
                continue
            if len(experts) > caps[il]:
                continue
            for e in experts:
                reqs.append((si, il, e))
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
    evictions = []
    seen = set()
    present = defaultdict(set)
    ptr = defaultdict(int)
    pos = defaultdict(int)         # effective-stream position per layer
    seen_cv = defaultdict(set)     # cache-visible seen (for admit gate)
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
                if warm:
                    # free placements from the prefill tail
                    p = ptr[il]
                    for e in experts[-caps[il]:]:
                        if e in present[il]:
                            continue
                        present[il].add(e)
                        if len(present[il]) > caps[il]:
                            def nu(x):
                                uses = [u for u in next_table[il].get(x, []) if u > p]
                                return uses[0] if uses else 10**18
                            victim = max(present[il], key=nu)
                            present[il].discard(victim)
                            per_step[si]["evictions"] += 1
                            evictions.append((il, pos[il], victim))
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
                    # admission gate: refuse first cache-visible read
                    admit = True
                    if admit_gate:
                        if e in seen_cv[il]:
                            admit = True
                        else:
                            admit = False
                        seen_cv[il].add(e)
                    per_step[si]["misses"] += 1
                    per_step[si]["bytes"] += byte_costs[il]
                    classification.append((si, il, e, False, comp, False))
                    if admit:
                        st.add(e)
                        if len(st) > caps[il]:
                            def nu(x):
                                uses = [u for u in next_table[il].get(x, []) if u > p]
                                return uses[0] if uses else 10**18
                            victim = max(st, key=nu)
                            st.discard(victim)
                            per_step[si]["evictions"] += 1
                            evictions.append((il, pos[il], victim))
                p += 1
                pos[il] += 1
            ptr[il] = p
    return per_step, classification, evictions


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def decode_mb_per_token(per_step, stats, n_decode_tokens):
    b = sum(d["bytes"] for si, d in per_step.items()
            if stats.get(si, {}).get("phase") == "decode")
    return b / 1048576.0 / n_decode_tokens


def decode_hit_miss(per_step, stats):
    h = m = 0
    for si, d in per_step.items():
        if stats.get(si, {}).get("phase") == "decode":
            h += d["hits"]
            m += d["misses"]
    return h, m


def decode_reloads(cls, stats):
    """Decode misses that are not compulsory (expert read earlier)."""
    n = 0
    for si, il, e, hit, comp, bypass in cls:
        if not hit and not comp and not bypass and stats.get(si, {}).get("phase") == "decode":
            n += 1
    return n


def eviction_regret(trace, caps, evictions):
    """For each eviction (il, pos, victim), gap in that layer's effective-stream
    positions until the victim is requested again.  Positions are
    policy-independent (same effective stream for all policies), so regret is
    directly comparable across policies."""
    reqs = []
    armed = False
    for si, layers in enumerate(trace):
        for il in sorted(layers):
            experts = layers[il]
            if not armed:
                armed = True
                continue
            if len(experts) > caps[il]:
                continue
            for e in experts:
                reqs.append((si, il, e))
    streams = defaultdict(list)
    for si, il, e in reqs:
        streams[il].append(e)
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
        return {"n": 0, "p50": 0, "le8": 0, "le26": 0, "le52": 0}
    s = sorted(gaps)
    return {
        "n": len(gaps),
        "p50": s[len(s) // 2],
        "le8": round(sum(1 for g in gaps if g <= 8) / len(gaps), 3),
        "le26": round(sum(1 for g in gaps if g <= 26) / len(gaps), 3),
        "le52": round(sum(1 for g in gaps if g <= 52) / len(gaps), 3),
    }


def observed_decode_mb_per_token(stats, n_decode_tokens):
    b = sum(st["pread_bytes"] for st in stats.values() if st["phase"] == "decode")
    return b / 1048576.0 / n_decode_tokens


def percentile(vals, p):
    if not vals:
        return 0
    s = sorted(vals)
    k = min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1))))
    return s[k]


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


# ---------------------------------------------------------------------------
# policy registry
# ---------------------------------------------------------------------------

POLICY_FACTORIES = {
    "lru": lambda cap: PolicyLRU(cap),
    "clock": lambda cap: PolicyCLOCK(cap),
    "twoq": lambda cap: Policy2Q(cap),
    "slru": lambda cap: PolicySLRU(cap, frac=0.50),
    "slru_t25": lambda cap: PolicySLRU(cap, frac=0.25),
    "slru_t75": lambda cap: PolicySLRU(cap, frac=0.75),
    "lfu_da": lambda cap: PolicyLFUDA(cap),
    "lfu_da2": lambda cap: PolicyLFUDA2(cap),
    "lru2": lambda cap: PolicyLRU2(cap),
    "lirs": lambda cap: PolicyLIRS(cap, hir_frac=0.05),
    "lirs_h20": lambda cap: PolicyLIRS(cap, hir_frac=0.20),
    "lru_f1": lambda cap: PolicyLRUF1(cap),
    "lru_f1_all": lambda cap: PolicyLRUF1(cap, scan_bound=10**9),
    "lru_admit": lambda cap: PolicyLRUAdmit(cap, count_all=True),
    "lru_admit2": lambda cap: PolicyLRUAdmit2(cap),
}

POLICY_META = {
    "lru": ("LRU (Phase 8 baseline)", "1 list position per resident entry; O(1)/request", "existing runtime behavior; zero new code"),
    "clock": ("CLOCK (second chance)", "1 ref bit + hand index per layer; O(1) amortized, worst-case scan cap", "small: ref bit + hand per layer; ~40-60 lines in runtime"),
    "twoq": ("2Q (A1in FIFO + A1out ghost + Am LRU)", "queue tags + links per resident; ghost = ids only (50% cap); O(1)/request", "moderate: 3 structures per layer; ~80-120 lines"),
    "slru": ("SLRU (probation + protected 50/50)", "1 segment bit + list position per resident; O(1)/request", "moderate: split LRU list; ~60-80 lines"),
    "slru_t25": ("SLRU (protected 25%)", "1 segment bit + list position per resident; O(1)/request", "moderate: split LRU list; ~60-80 lines"),
    "slru_t75": ("SLRU (protected 75%)", "1 segment bit + list position per resident; O(1)/request", "moderate: split LRU list; ~60-80 lines"),
    "lfu_da": ("LFU with dynamic aging (halve at saturation)", "1 saturating byte + recency tiebreak per resident; bucket lists O(1); aging O(cap) amortized", "moderate-high: counters + aging + buckets; ~80-120 lines"),
    "lfu_da2": ("LFU with dynamic aging (avg-threshold subtract-min)", "1 saturating byte + recency tiebreak per resident; bucket lists O(1); aging O(cap) amortized", "moderate-high: counters + aging + buckets; ~80-120 lines"),
    "lru2": ("LRU-2 (2nd-reference recency)", "2 timestamps per resident + priority structure; O(log cap)/request", "moderate-high: per-layer heap or 2-level lists; ~100-150 lines"),
    "lirs": ("LIRS (HIR 5%)", "stack entry (id + 1 bit) + small FIFO; O(1) amortized; stack bounded", "moderate-high: stack + Q; ~100-150 lines"),
    "lirs_h20": ("LIRS (HIR 20%)", "stack entry (id + 1 bit) + small FIFO; O(1) amortized; stack bounded", "moderate-high: stack + Q; ~100-150 lines"),
    "lru_f1": ("LRU + single-ref-first eviction (scan bound cap/2)", "1 freq>=2 bit per resident; O(scan) worst-case", "small: victim-scan tweak; ~30-50 lines"),
    "lru_f1_all": ("LRU + single-ref-first eviction (unbounded scan)", "1 freq>=2 bit per resident; O(scan) worst-case", "small: victim-scan tweak; ~30-50 lines"),
    "lru_admit": ("LRU + 2nd-read admission gate (all reads count)", "+32 B bitmap per layer (256 expert ids); O(1)/request", "tiny: bitmap + placement gate; ~15-25 lines"),
    "lru_admit2": ("LRU + 2nd cache-visible-read admission gate", "+32 B bitmap per layer (256 expert ids); O(1)/request", "tiny: bitmap + placement gate; ~15-25 lines"),
}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(RES, "phase-09b", "policy-gate.json"))
    ap.add_argument("--warm-budgets", default="cap-4",
                    help="comma-separated budgets for the warm-start secondary condition")
    args = ap.parse_args()

    warm_budgets = [b for b in args.warm_budgets.split(",") if b]
    out = {}
    for wname, wdir in WORKLOADS:
        if not os.path.isdir(wdir):
            print(f"[skip] {wname}: {wdir} missing", file=sys.stderr)
            continue
        tp = os.path.join(wdir, "cap-4", REP, "moe.csv")
        if not os.path.exists(tp):
            tp = os.path.join(wdir, "cap-4-r1", "moe.csv")
        trace = load_moe_trace(tp)
        n_decode = N_DECODE_TOKENS[wname]
        w = {
            "trace_steps": len(trace),
            "n_decode_tokens": n_decode,
            "reuse_distance": None,
            "budgets": {},
        }
        dists = reuse_distances(trace)
        w["reuse_distance"] = {
            "n": len(dists),
            "p50": percentile(dists, 50),
            "p90": percentile(dists, 90),
            "p99": percentile(dists, 99),
            "max": max(dists) if dists else 0,
            "frac_le1": round(sum(1 for d in dists if d <= 1) / len(dists), 3) if dists else 0,
            "frac_le8": round(sum(1 for d in dists if d <= 8) / len(dists), 3) if dists else 0,
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

            # --- bounds ---
            per_opt, cls_opt, ev_opt = replay_opt(trace, caps, byte_costs)
            per_opt_a, cls_opt_a, ev_opt_a = replay_opt(trace, caps, byte_costs,
                                                       admit_gate=True)
            obs = observed_decode_mb_per_token(stats, n_decode)

            budgets_entry = {
                "cap_slots_min": min(caps.values()),
                "cap_slots_max": max(caps.values()),
                "obs_decode_mb_tok": round(obs, 1),
                "opt": None,
                "opt_admit2": None,
                "policies": {},
                "warm": {},
            }

            # --- oracle bounds ---
            per_opt, cls_opt, ev_opt = replay_opt(trace, caps, byte_costs)
            per_opt_a, cls_opt_a, ev_opt_a = replay_opt(trace, caps, byte_costs,
                                                       admit_gate=True)
            opt_mb = decode_mb_per_token(per_opt, stats, n_decode)
            opt_a_mb = decode_mb_per_token(per_opt_a, stats, n_decode)
            budgets_entry["opt"] = {
                "decode_mb_tok": round(opt_mb, 1),
                "decode_hit_rate": round(decode_hit_miss(per_opt, stats)[0] /
                                          max(sum(decode_hit_miss(per_opt, stats)), 1), 4),
                "decode_reloads": decode_reloads(cls_opt, stats),
                "n_evictions": len(ev_opt),
                "regret": eviction_regret(trace, caps, ev_opt),
            }
            budgets_entry["opt_admit2"] = {
                "decode_mb_tok": round(opt_a_mb, 1),
                "decode_hit_rate": round(decode_hit_miss(per_opt_a, stats)[0] /
                                          max(sum(decode_hit_miss(per_opt_a, stats)), 1), 4),
                "decode_reloads": decode_reloads(cls_opt_a, stats),
                "n_evictions": len(ev_opt_a),
                "regret": eviction_regret(trace, caps, ev_opt_a),
            }

            # --- candidate policies (cold start) ---
            lru_mb = None
            for pname in POLICY_FACTORIES:
                factory = POLICY_FACTORIES[pname]
                per_step, cls, ev = replay_policy(trace, caps, byte_costs, factory,
                                                  warm=False, stats=stats)
                mb = decode_mb_per_token(per_step, stats, n_decode)
                h, m = decode_hit_miss(per_step, stats)
                entry = {
                    "decode_mb_tok": round(mb, 1),
                    "decode_hit_rate": round(h / max(h + m, 1), 4),
                    "decode_reloads": decode_reloads(cls, stats),
                    "n_evictions": len(ev),
                    "regret": eviction_regret(trace, caps, ev),
                }
                if lru_mb is None:
                    lru_mb = mb
                entry["gap_recovered_pct"] = round(
                    100.0 * (lru_mb - mb) / max(lru_mb - opt_mb, 1e-9), 1)
                budgets_entry["policies"][pname] = entry

            # --- warm-start secondary condition ---
            if b in warm_budgets:
                warm_entry = {}
                for pname in POLICY_FACTORIES:
                    factory = POLICY_FACTORIES[pname]
                    per_step, cls, ev = replay_policy(trace, caps, byte_costs, factory,
                                                      warm=True, stats=stats)
                    mb = decode_mb_per_token(per_step, stats, n_decode)
                    h, m = decode_hit_miss(per_step, stats)
                    warm_entry[pname] = {
                        "decode_mb_tok": round(mb, 1),
                        "decode_hit_rate": round(h / max(h + m, 1), 4),
                        "decode_reloads": decode_reloads(cls, stats),
                        "n_evictions": len(ev),
                        "gap_recovered_pct": round(
                            100.0 * (lru_mb - mb) / max(lru_mb - opt_mb, 1e-9), 1),
                    }
                per_opt_w, _, ev_opt_w = replay_opt(trace, caps, byte_costs, warm=True)
                opt_w_mb = decode_mb_per_token(per_opt_w, stats, n_decode)
                warm_entry["opt"] = {"decode_mb_tok": round(opt_w_mb, 1)}
                budgets_entry["warm"] = warm_entry

            # --- validation: LRU replay vs observed ---
            per_lru, _, _ = replay_policy(trace, caps, byte_costs,
                                          POLICY_FACTORIES["lru"], warm=False, stats=stats)
            obs_look = sum(st["lookups"] for st in stats.values())
            rep_look = sum(d["lookups"] for d in per_lru.values())
            obs_miss = sum(st["misses"] for st in stats.values())
            rep_miss = sum(d["misses"] for d in per_lru.values())
            obs_hit = sum(st["hits"] for st in stats.values())
            rep_hit = sum(d["hits"] for d in per_lru.values())
            budgets_entry["validation"] = {
                "lookup_match_pct": round(100.0 * min(rep_look, obs_look) / max(obs_look, 1), 2),
                "miss_match_pct": round(100.0 * min(rep_miss, obs_miss) / max(obs_miss, 1), 2),
                "hit_match_pct": round(100.0 * min(rep_hit, obs_hit) / max(obs_hit, 1), 2),
                "lru_replay_mb_tok": round(lru_mb, 1),
            }
            budgets_entry["meta"] = {
                pname: {"label": POLICY_META[pname][0],
                        "metadata": POLICY_META[pname][1],
                        "complexity": POLICY_META[pname][2]}
                for pname in POLICY_FACTORIES
            }
            w["budgets"][b] = budgets_entry
        out[wname] = w

    with open(args.json, "w") as f:
        json.dump(out, f, indent=2)

    # --- console tables ---
    for wname, w in out.items():
        print(f"\n==== {wname} ({w['n_decode_tokens']} decode tokens) ====")
        print(f"{"budget":>6} {"lru":>7} {"opt":>7} {"gap":>5} |" +
              "".join(f"{p:>9}" for p in ["clock", "twoq", "slru", "lfu_da",
                                         "admit", "admit2"]))
        for b, d in w["budgets"].items():
            pols = d["policies"]
            row = (f"{b:>6} {pols['lru']['decode_mb_tok']:7.1f} {d['opt']['decode_mb_tok']:7.1f} "
                   f"{100.0*(pols['lru']['decode_mb_tok']-d['opt']['decode_mb_tok'])/max(pols['lru']['decode_mb_tok'],1e-9):5.1f} |")
            for p in ["clock", "twoq", "slru", "lfu_da", "lru_admit", "lru_admit2"]:
                e = pols[p]
                row += f"{e['decode_mb_tok']:8.1f}{"":>1}"
            print(row)
        print("  gap recovered % of LRU->OPT:")
        for b, d in w["budgets"].items():
            pols = d["policies"]
            row = f"  {b:>6}"
            for p in ["clock", "twoq", "slru", "lfu_da", "lru_admit", "lru_admit2"]:
                row += f" {p:>9}={pols[p]['gap_recovered_pct']:5.1f}%"
            print(row)


if __name__ == "__main__":
    main()
