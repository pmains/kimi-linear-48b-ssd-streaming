# Phase 9B Report — Replacement-Policy Selection Gate

## Status

**PASS (selection gate) — NO-GO for runtime implementation of any tested
replacement policy.** Offline replay of the Phase 8 routing traces through a
deliberately small family of practical online policies (LRU, CLOCK, 2Q, SLRU
×3 splits, LFU-DA ×2 aging variants, LRU-2, LIRS ×2 HIR sizes, LRU-F1 ×2 scan
bounds, and two admission-gate variants) shows that **no online policy
recovers ≥ 25% of the LRU→Belady-OPT gap at any budget on any workload**,
against the pre-registered thresholds (25% = "potentially worthwhile",
50% = "strong candidate"). At the primary operating point (coding, 4 GB,
LRU 286.4 → OPT 171.1 MB/token, gap 115.3), the best policy recovers
**3.1%** of the gap (SLRU, protected 25%: 282.8 MB/token). The most robust
policy (SLRU, protected 50%) is positive on all three workloads at 4 GB
(+2.7% / +15.6% / +11.6% of the gap) but remains far below threshold and is
not "essentially free". **Keep the frozen Phase 8 LRU baseline.** No
llama.cpp code was modified; this gate is offline trace replay only. The
Phase 9A warm-start state is confirmed neutral-to-negative for every policy
(no policy × warm-start interaction exists).

## Objective

Answer the gate's central question: **how much of the measured LRU→Belady-OPT
gap can a practical online cache policy recover without excessive
implementation complexity, metadata overhead, or runtime decision cost?**

Pre-registered decision thresholds (from the task):

| recovery of LRU→OPT gap | verdict |
|---|---:|
| < 25% | Probably NO-GO unless essentially free/simple |
| 25–50% | Potentially worthwhile if overhead small |
| > 50% | Strong implementation candidate |
| > 70% | Excellent; prefer the simplest policy reaching this range |

Reference at 4 GB (coding): LRU 286.4, OPT 171.1, gap 115.3 MB/token;
50% ≈ 228.8, 70% ≈ 205.7 MB/token.

## Methodology

- **Data**: identical to the Phase 9 selection gate —
  `benchmarks/results/phase-08/{coding-lru,reasoning}/cap-{1,2,4,6,8}-r1` and
  `phase-07b/baseline` (ref), all r1. `moe.csv` = routing trace (md5-identical
  across budgets), `stats.csv` = observed counters for validation,
  `cache_layers.csv` = per-layer slot caps + expert slice bytes.
- **Fidelity model**: exactly the validated Phase 9 gate model (arm-call
  bypass, oversized-group bypass, per-layer LRU slot semantics, per-layer
  bytes). The LRU replay is re-validated against observed stats at every
  budget/workload: **lookups/hits/misses match 100.0% in all 15 cells**
  (see Validation).
- **Policy implementations**: all strictly online; each policy's per-layer
  state machine handles the same request stream with identical arm/bypass
  semantics; only hit-refresh / insert / victim-choice differ.
- **Bounds**: Belady-OPT per layer (farthest-next-use eviction), and
  OPT-with-2nd-touch-admission (`opt_admit2`) as the admission-side bound.
  OPT is computed on the full trace (offline) and is never a candidate.
- **Metrics per policy per (workload, budget)**: decode MB/token (primary),
  decode hit rate, decode expert reload count (non-compulsory decode misses),
  eviction count, short-term eviction regret (le8/le26/le52 effective
  requests, policy-independent positions), fraction of LRU→OPT gap recovered,
  plus per-policy metadata / decision-cost / complexity assessment.
- **Secondary condition**: Phase 9A warm start (prefill-tail feeding on
  oversized groups) replayed for every policy at 4 GB.
- **No oracle leakage**: candidate policies use only past accesses. The
  frequency counters, seen sets, ghosts, and recency ranks all derive from
  requests strictly before the current one. OPT/opt_admit2 are labeled bounds.

### Policy family

| key | policy | metadata | decision cost | complexity |
|---|---|---|---|---|
| `lru` | LRU (Phase 8 baseline, control) | 1 list position/resident | O(1) | existing runtime code |
| `clock` | CLOCK second chance | 1 ref bit + hand | O(1) amort., worst-case scan cap | small (~40–60 lines) |
| `twoq` | 2Q (A1in 25% FIFO + A1out ghost 50% + Am LRU) | queue tags/links + ghost ids | O(1) | moderate (~80–120 lines) |
| `slru` | SLRU probation/protected 50/50 | 1 segment bit + position | O(1) | moderate (~60–80 lines) |
| `slru_t25` | SLRU protected 25% | same | O(1) | same |
| `slru_t75` | SLRU protected 75% | same | O(1) | same |
| `lfu_da` | LFU, dynamic aging at counter saturation | 1B counter + recency tiebreak | O(1) buckets; aging O(cap) amort. | moderate-high (~80–120 lines) |
| `lfu_da2` | LFU, avg-threshold aging (subtract-min/halve) | same | same | same |
| `lru2` | LRU-2 (evict by 2nd-most-recent ref age) | 2 timestamps + priority struct | O(log cap) | moderate-high (~100–150 lines) |
| `lirs` | LIRS, HIR 5% | stack entry + small FIFO | O(1) amort. | moderate-high (~100–150 lines) |
| `lirs_h20` | LIRS, HIR 20% | same | O(1) amort. | same |
| `lru_f1` | LRU + evict oldest single-ref entry (scan ≤ cap/2) | 1 freq≥2 bit | O(scan) worst-case | small (~30–50 lines) |
| `lru_f1_all` | same, unbounded scan | same | O(cap) worst-case | small |
| `lru_admit` | LRU + admit-on-2nd-read (all reads count) | +32 B bitmap/layer | O(1) | tiny (~15–25 lines) |
| `lru_admit2` | LRU + admit-on-2nd-cache-visible-read | +32 B bitmap/layer | O(1) | tiny (~15–25 lines) |
| `opt` | Belady-OPT (bound) | — | offline | — |
| `opt_admit2` | Belady-OPT + 2nd-touch admission (bound) | — | offline | — |

`lru_admit` was included as the "essentially free" option; `slru*`, `lfu_da*`,
`lru2`, `lirs`, `lru_f1` as the frequency/scan-resistant options; `twoq`,
`clock` per the required minimum family.

## Validation of Replay Against Observed LRU

LRU replay vs observed Phase 8 stats (`stats.csv`), all 15 cells:

| workload | cap-1 | cap-2 | cap-4 | cap-6 | cap-8 |
|---|---:|---:|---:|---:|---:|
| coding-lru | 100.0 / 100.0 / 100.0 | 100.0 / 100.0 / 100.0 | 100.0 / 100.0 / 100.0 | 100.0 / 100.0 / 100.0 | 100.0 / 100.0 / 100.0 |
| reasoning | 100.0 / 100.0 / 100.0 | 100.0 / 100.0 / 100.0 | 100.0 / 100.0 / 100.0 | 100.0 / 100.0 / 100.0 | 100.0 / 100.0 / 100.0 |
| ref | 100.0 / 100.0 / 100.0 | 100.0 / 100.0 / 100.0 | 100.0 / 100.0 / 100.0 | 100.0 / 100.0 / 100.0 | 100.0 / 100.0 / 100.0 |

(columns show lookups / misses / hits match %, each 100.0). Replayed LRU
decode MB/token equals observed exactly at every cell (e.g. 286.4 = 286.4 at
coding cap-4), and OPT reproduces the Phase 9 gate's oracle numbers
(coding 374.0/262.7/171.1/136.5/118.1; reasoning 428.8/317.0/203.5/151.0/
122.5; ref 405.8/302.6/202.9/164.0/152.0).

## Results

### Decode MB/token (primary metric) and fraction of LRU→OPT gap recovered

**coding-lru** (the target workload; 128 decode tokens):

| budget | LRU | OPT | best policy | best MB/tok | best recovery |
|---|---:|---:|---|---:|---:|
| 1 GB | 567.5 | 374.0 | lfu_da | 537.7 | +15.4% |
| 2 GB | 420.2 | 262.7 | slru | 405.7 | +9.2% |
| 4 GB | **286.4** | **171.1** | slru_t25 | **282.8** | **+3.1%** |
| 6 GB | 219.0 | 136.5 | lru_f1 | 215.5 | +4.2% |
| 8 GB | 180.5 | 118.1 | lru_f1 | 177.7 | +4.4% |

**reasoning** (128 decode tokens):

| budget | LRU | OPT | best policy | best MB/tok | best recovery |
|---|---:|---:|---|---:|---:|
| 1 GB | 623.1 | 428.8 | lfu_da | 603.9 | +9.9% |
| 2 GB | 486.4 | 317.0 | slru_t25 | 477.3 | +5.4% |
| 4 GB | 370.1 | 203.5 | slru | 344.1 | +15.6% |
| 6 GB | 270.3 | 151.0 | slru | 258.0 | +10.3% |
| 8 GB | 201.2 | 122.5 | slru | 195.4 | +7.4% |

**ref** (64 decode tokens):

| budget | LRU | OPT | best policy | best MB/tok | best recovery |
|---|---:|---:|---|---:|---:|
| 1 GB | 585.5 | 405.8 | lfu_da | 556.1 | +16.3% |
| 2 GB | 454.1 | 302.6 | slru_t25 | 452.5 | +1.0% |
| 4 GB | 345.1 | 202.9 | lfu_da2 | 326.7 | +13.0% |
| 6 GB | 267.4 | 164.0 | lfu_da | 251.4 | +15.5% |
| 8 GB | 209.8 | 152.0 | lru_f1_all | 200.6 | +16.0% |

**Maximum recovery anywhere: +16.3% (ref cap-1, lfu_da).** No cell reaches
+25%. The primary operating point (coding, 4 GB) shows the *smallest*
recoverable fraction (+3.1%), because coding is the most recency-dominated
workload.

### Robustness across workloads (4 GB, the primary operating point)

| policy | coding | reasoning | ref | verdict |
|---|---:|---:|---:|---|
| slru (t50) | +2.7% | +15.6% | +11.6% | positive everywhere; the only such policy |
| slru_t25 | +3.1% | +6.5% | +6.7% | positive everywhere; smaller |
| lfu_da2 | −6.0% | +12.5% | +13.0% | negative on coding (primary workload) |
| lfu_da | −19.7% | +9.7% | +12.5% | negative on coding |
| lru2 | −90.9% | −76.2% | −11.0% | collapses |
| lirs | −96.4% | −34.1% | −97.4% | collapses |
| clock | −6.0% | −6.4% | −8.6% | consistently negative |
| lru_admit | +0.1% | −0.6% | +0.4% | ≈ zero (free, but nothing) |

SLRU (t50) is the most robust candidate but is workload-sensitive
(+2.7%…+15.6%) and below threshold everywhere. Frequency-heavy policies
(LFU, LRU-2, LIRS) win on ref/reasoning but lose on coding — the workload the
runtime actually serves.

### Why the gap is not practically capturable (mechanism, coding @ 4 GB)

Classify every decode request by (LRU hit, OPT hit):

| class | n | meaning |
|---|---:|---|
| lru & opt hit | 17,624 | short-distance reuses; **LRU is already perfect here (0 lru-only cases)** |
| opt-only hit | 3,611 | the entire recoverable gap (~115 MB/token) |
| both miss | 6,507 | capacity-bound; unrecoverable |

- The recoverable population is **5:1 outweighed** by the recency population
  LRU already serves. Any policy that deviates from LRU order risks the
  large class to gain the small one. The class split explains the
  cross-workload differences (4 GB):

  | workload | lru&opt | opt-only | ratio | opt-only share of lru&opt |
  |---|---|---:|---:|---:|
  | coding | 17,478 | 3,598 | 4.9:1 | 20.6% |
  | reasoning | 14,879 | 5,194 | 2.9:1 | 34.9% |
  | ref | 7,733 | 2,216 | 3.5:1 | 28.7% |

  coding is the most recency-dominated (frequency policies have the least
  to gain), which is exactly why the primary workload shows the smallest
  recoverable fraction.
- opt-only experts are **repeat users**: total freq mean 11.0, p50 7, 95.3%
  with freq ≥ 3 (both-miss: mean 5.7, p50 4). Their reuse interleaving
  exceeds capacity (raw reuse distance 40–300+ requests), so LRU evicts them
  during a quiet gap; OPT retains them by evicting "dead" entries instead.
- OPT's victims are 60% never-used-again (3,314 of 5,505) vs LRU's 36%;
  LRU's re-used victims return after a median 104 requests (OPT's 445).
  Identifying "dead" residents is what OPT exploits — that requires future
  knowledge. No past-only signal discriminates dead entries without also
  sacrificing the recency population.
- Measured coverage/loss (coding cap-4): lfu_da covers 42.5% of opt-only
  but loses 14.3% of lru&opt → net −985 requests. The break-even requires
  loss ≤ ~7% at that coverage; every frequency mechanism tested lost more.
  slru covers 23.9% / loses 4.7% → net +37; slru_t25 covers 9.7% / loses
  1.4% → net +100 (the observed +3.1%).
- Split-pool policies (2Q, SLRU t75, LIRS, LRU-2) all fail the same way:
  any first-touch pool smaller than the full cache converts 2nd references
  at distance > pool-size (p50 reuse ≈ 19 requests, p90 ≈ 232) into misses.
  LRU-2's textbook form death-spirals (stale double-reference entries
  starve the working set: hit rate decays 0.63 → 0.49 over the trace).
  LIRS's tiny HIR pool has the same 2nd-reference conversion problem.

### Admission vs eviction

- **Admission-side levers are negative, not neutral.** `lru_admit`
  (2nd-read gate, the "free" option) is ≈ 0 everywhere; `lru_admit2`
  (2nd-cache-visible-read gate) is −11%…−139% of the gap.
- The bound is decisive: **even the oracle loses most of its advantage under
  2nd-touch admission** (`opt_admit2` vs `opt`, MB/token):

| budget | coding opt → opt_admit2 | reasoning | ref |
|---|---:|---:|---:|
| 1 GB | 374.0 → 399.0 | 428.8 → 459.4 | 405.8 → 451.6 |
| 2 GB | 262.7 → 303.2 | 317.0 → 359.7 | 302.6 → 369.8 |
| 4 GB | 171.1 → 230.8 | 203.5 → 261.3 | 202.9 → 297.5 |
| 6 GB | 136.5 → 211.5 | 151.0 → 222.8 | 164.0 → 285.9 |
| 8 GB | 118.1 → 200.7 | 122.5 → 209.0 | 152.0 → 285.9 |

  Deferring admission to a 2nd cache-visible reference converts the entire
  near-term 2nd-reference population (the p50 reuse class) into misses.
  The Phase 9A hint that "admission may matter" is **disconfirmed by
  measurement**: at these capacities the 2nd reference is the dominant
  hit class, and refusing early admission throws it away. The recoverable
  gap is **eviction-side only** (choosing better victims), and that
  requires the future-knowledge "dead entry" signal.
- Phase 9A warm start: neutral-to-negative for every online policy at 4 GB
  (e.g. coding slru_t25 +3.1% → +2.4%; lru +0.0% → −0.6%). It only helps
  the *oracle* (opt 171.1 → 167.4 warm, ref 202.9 → 186.9) — free
  occupancy that no online policy can exploit. No policy × warm-start
  interaction exists; 9A's "do not promote" decision stands.

### Secondary metrics (coding, cap-4; representative)

| policy | MB/tok | hit rate | reloads | evictions | regret le8 / le26 / le52 |
|---|---:|---:|---:|---:|---:|
| lru | 286.4 | 0.6616 | 8,429 | 9,116 | 0.071 / 0.20 / 0.33 |
| slru_t25 | 282.8 | 0.6658 | 8,318 | 9,016 | 0.070 / 0.20 / 0.32 |
| slru | 283.3 | 0.6652 | 8,335 | 9,075 | 0.066 / 0.20 / 0.32 |
| lfu_da2 | 293.3 | 0.6536 | 8,642 | 9,320 | 0.077 / 0.22 / 0.35 |
| opt | 171.1 | 0.7978 | 4,831 | 5,505 | — |

Reload counts track MB/token as expected; regret is a weak differentiator
across policies (the evicted populations are similar; only OPT's are
qualitatively different).

## Problems

- **The 40% oracle gap is mostly future-knowledge-bound.** The selection
  gate's hypothesis that "realistic policies (LFU-biased, pin hot experts,
  scan-resistant) can plausibly capture a third to half of the gap" is
  **disconfirmed**: the best realistic policy captures ≤ 16% and the
  primary workload ≤ 3.1%. The gate's own "policy-fixable" label was
  optimistic — OPT's advantage is dominated by dead-entry eviction, which
  has no online proxy on this trace.
- Textbook LRU-2 (no correlated-reference-period tuning) and LIRS
  catastrophically underperform (hit-rate collapse / 2nd-reference
  conversion); both would need parameter engineering whose payoff is
  unproven here. ARC was not run: its ghost-driven recency/frequency
  adaptation targets workload-phase shifts (scan vs loop), not this
  long-gap repeat-user structure; 2Q (its closest structural cousin) was
  negative, and the 5:1 recency asymmetry makes ARC's adaptive headroom
  unpromising. This is a documented judgment call, not a claim that ARC
  cannot ever win on some other trace.
- Cross-workload inconsistency: policies that help ref/reasoning hurt
  coding, so there is no robust winner to carry into the runtime even if
  the threshold had been lower.
- Warm-start evaluation is replay-only for non-LRU policies (the runtime
  9A hook feeds the LRU only); given the uniform ≈0 result across all
  policies this is a negligible limitation.

## Decisions

- **NO-GO: do not implement a replacement policy in the runtime.** Keep
  the frozen Phase 8 LRU as the shipped behavior. Rationale: no policy
  reaches the 25% pre-registered threshold at any budget/workload; the
  best robust option (SLRU t50, ~+10% of gap mean at 4 GB, i.e. ~1–3%
  total traffic) is not "essentially free" (60–80 lines, new per-layer
  structures, eviction-order change) and is workload-sensitive.
- **Do not adopt admission control in any form** (even the free bitmap
  gate): measured ≈ 0 to strongly negative, and provably negative under
  the oracle.
- **Keep the 9A warm-tail change un-promoted** (dev tree only, committed):
  confirmed neutral for every policy; no interaction to exploit.
- **Close the replacement-policy workstream** for this roadmap phase.
  The remaining Phase 9 levers with measured, policy-independent value are
  the I/O-side ones from the selection gate: async expert loading
  (I/O-compute overlap), and storage layout / read coalescing (fewer,
  larger, sequential preads). Those attack the 53.9% capacity-bound +
  I/O-efficiency components, not the policy component.
- Do not revisit replacement policy unless a future change (e.g. MXFP4
  halving expert bytes, or a different workload regime) materially alters
  the reuse-distance/capacity ratio; re-run this gate's replay if so.

## Next Phase

- The offline gate is complete; the recommendation is reported back before
  any runtime implementation (per the gate's stop rule). Await direction
  on which (if any) I/O-side lever to pursue next; the natural candidates
  in selection-gate order are async expert loading, then storage layout.
- If proceeding to any runtime change, remember: benchmark against the
  frozen Phase 8 baseline (9A stays un-promoted), keep moe.csv md5
  determinism + `phase07_summarize` invariants, and report deterministic
  columns (MB/token, hit rate) separately from timing (environment-
  confounded today).
- The `opt_admit2` result is a useful permanent bound: any future
  admission-control proposal must beat the oracle-under-admission curve,
  which is far below plain LRU — admission control is closed unless the
  evidence changes.

## Reproduction

```bash
# Full policy gate (writes benchmarks/results/phase-09b/policy-gate.json)
python3 tools/phase09b_policy_gate.py

# Key validation cross-check (any budget/workload):
awk -F, 'NR>1 && $2=="decode"{pb+=$5}END{print pb/128/1048576}' \
  benchmarks/results/phase-08/coding-lru/cap-4-r1/stats.csv   # 286.4

# Mechanism analysis (opt-only coverage/loss, victim gaps):
#   tools/phase09b_policy_gate.py policy classes + ad-hoc analysis shown in
#   this report; reuse the POLICY_FACTORIES registry.
```

Environment: same frozen Phase 8 artifacts (llama.cpp `e8baeb16e` dev tree +
committed 9A change `2be845e`; live runtime `cad716035` untouched). No new
inference runs were performed; all results are offline replay.

Artifacts: `tools/phase09b_policy_gate.py`,
`benchmarks/results/phase-09b/policy-gate.json`, this report.
