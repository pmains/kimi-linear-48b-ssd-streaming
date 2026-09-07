# Phase 9 Selection Gate Report

## Status

**PASS (selection gate) — GO for a scoped Phase 9.** An offline Belady-OPT
oracle cuts decode SSD traffic by **40% at the recommended 4 GB budget**
(286.4 → 171.1 MB/token, coding) and **35% at 8 GB** (180.5 → 118.1),
against the same per-layer slot capacities and the same routing traces.
The oracle reaching ~118 MB/token at 8 GB meets the pre-registered
"substantial unexploited locality" threshold (~120 MB/token). No llama.cpp
code was modified; this is analysis of existing Phase 8 artifacts only.

## Objective

Determine whether Phase 9 streaming/cache optimization is justified and
which levers have measurable headroom, using existing Phase 8 telemetry
(routing traces + observed hit/miss counters). Central question: how much
of the observed SSD traffic is fundamentally required by the routing trace
vs caused by cache eviction/reloading or inefficient I/O?

## Evidence and Methodology

- **Data**: `benchmarks/results/phase-08/{coding-lru,reasoning}/cap-{1,2,4,6,8}-r1`
  and `phase-07b/baseline` (ref workload). `moe.csv` = per-token expert
  routing (identical across budgets, md5-verified), `stats.csv` = observed
  per-step lookups/hits/misses/pread bytes, `cache_layers.csv` = per-layer
  slot capacities and per-layer expert slice bytes.
- **Trace**: moe.csv reconstructed into ordered steps (layer-wrap in file
  order; moe `start_pos` is not a unique step key — two 2-token prefill
  batches share start_pos=0). Per (step, layer): unique experts in
  first-occurrence order, matching the runtime's dedupe/slot order.
- **Cache model**: per-layer persistent-slot cache with the observed
  per-layer capacities (mode of `cap_slots`); LRU within layer. Models the
  runtime's zc bypass (unique experts > capacity ⇒ no cache consultation,
  all preads, cache state untouched — matches observed lookups=0 on the
  233-token prefill step) and the cache-arm call (first n_slots>0 layer
  runs legacy placement, no zc lookups).
- **Costs**: per-layer expert bytes from `cache_layers.csv` (up+gate+down;
  13 layers 4,589,568 B, 13 layers 3,981,312 B — not uniform).
- **Validation**: replay reproduces observed lookups/misses/hits **exactly
  (100.0%)** at every budget in every workload; replayed decode MB/token
  equals observed decode MB/token to 0.1 (e.g. 286.4 = 286.4 at cap-4
  coding); r1=r2=r3 are byte-identical.
- **Oracle**: Belady-OPT per layer (evict resident expert with farthest
  next use in the effective stream; bypass and arm semantics identical to
  the LRU replay).
- **Headline verification**: observed decode SSD at 4 GB coding =
  286.4 MB/token (raw: 38,439,512,064 pread bytes / 128 decode tokens),
  matching the Phase 8 reported 286.3; uncached 850.1 MB/token.

## Current Measured Traffic (decode phase, MB/token)

| budget | caps/layer | coding | reasoning | ref |
|---:|---:|---:|---:|---:|
| uncached | — | 850.1 | 850.1 | 850.1* |
| 1 GB | 9–10 | 567.5 | 623.1 | 585.5 |
| 2 GB | 19–20 | 420.2 | 486.4 | 454.1 |
| 4 GB | 38–39 | **286.4** | 370.1 | 345.1 |
| 6 GB | 57–58 | 219.0 | 270.3 | 267.4 |
| 8 GB | 77–78 | 180.5 | 201.2 | 209.8 |

*ref uncached from phase-07b. Replay LRU equals observed exactly at every
rung (validated), so the LRU column is omitted.

## Compulsory vs Avoidable Traffic

- Whole-run compulsory (unique (layer, expert) pairs × per-layer bytes):
  **22,071 MB (coding)**, 22,318 MB (reasoning), 16,851 MB (ref).
- Decode-phase compulsory (experts whose **first** request occurs in a
  decode step): only **16.6 MB/token (coding)** — because the 241-token
  prompt pre-loads most experts. Nearly all decode traffic is **reload**
  traffic (experts evicted between uses).
- Reload amplification (actual bytes / compulsory bytes, whole run):
  LRU **2.75×** at 4 GB coding (obs 2.746), OPT **2.08×**.

At 4 GB coding, decode traffic decomposes as:

| component | MB/token | share |
|---:|---:|---:|
| compulsory (first-load in decode) | 16.6 | 5.8% |
| policy-fixable (LRU reload − OPT reload) | 115.3 | 40.3% |
| capacity-bound residual (OPT − compulsory) | 154.5 | 53.9% |
| **total (observed)** | **286.4** | 100% |

## LRU vs OPT Headroom

| budget | LRU | OPT | reduction |
|---:|---:|---:|---:|
| 1 GB | 567.5 | 374.0 | 34% |
| 2 GB | 420.2 | 262.7 | 37% |
| 4 GB | 286.4 | 171.1 | **40%** |
| 6 GB | 219.0 | 136.5 | 38% |
| 8 GB | 180.5 | 118.1 | 35% |

(coding; reasoning and ref show the same pattern: 45%/39% at 4 GB, 39%/41%
at 8 GB.) The headroom is large and consistent across workloads — LRU is
leaving ~35–45% of decode reload traffic on the table even at the
recommended budget.

## Expert Reuse-Distance Distribution (step units)

| workload | p50 | p90 | p99 | max | ≤1 | ≤8 | ≤26 | ≤52 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| coding | 3 | 40 | 105 | 128 | 39% | 69% | 86% | 92% |
| reasoning | 4 | 33 | 101 | 128 | 33% | 62% | 86% | 95% |
| ref | 2 | 24 | 49 | 64 | 40% | 70% | 91% | 99% |

Strong short-term locality (≈70% of reuses within 8 decode steps) but a
long tail: ~8–14% of reuses are 26+ steps apart, which is what the
capacity-bound residual represents at small budgets.

## Eviction Regret (LRU, coding)

Share of evictions followed by a re-request of the victim within 8 / 26 /
52 effective requests (≈1 / 3.25 / 6.5 decode steps):

| budget | n evictions | ≤8 | ≤26 | ≤52 |
|---:|---:|---:|---:|---:|
| 1 GB | 13,757 | 21% | 41% | 57% |
| 2 GB | 9,699 | 11% | 29% | 45% |
| 4 GB | 5,802 | 7% | 20% | 33% |
| 8 GB | 2,479 | 4% | 12% | 21% |

At 1–2 GB a third to half of evictions are regretted within ~6 decode
steps; at 4 GB+ the regret rate drops but is not zero — consistent with the
LRU-vs-OPT gap.

## Cache-Size Counterfactuals (same traces, replay only)

| budget | coding LRU | coding OPT | reasoning LRU | reasoning OPT |
|---:|---:|---:|---:|---:|
| 1 GB | 567.5 | 374.0 | 623.1 | 428.8 |
| 2 GB | 420.2 | 262.7 | 486.4 | 317.0 |
| 4 GB | 286.4 | 171.1 | 370.1 | 203.5 |
| 6 GB | 219.0 | 136.5 | 270.3 | 151.0 |
| 8 GB | 180.5 | 118.1 | 201.2 | 122.5 |

The OPT curve is monotone and steep: 374 → 118 MB/token from 1 → 8 GB.
Capacity buys roughly linear traffic reduction under an optimal policy.

## I/O Efficiency Facts (coding)

- Pread granularity: **3 preads per expert** (up/gate/down at widely
  separated file offsets), average read **1.36 MB**; not coalescible
  without storage-layout change.
- Decode pread time: 103 ms/step uncached → 68.9 ms/step at 4 GB → 65.8 at
  8 GB; **pread is ~37% of the ~185 ms decode step at 4 GB** (the largest
  single component; Phase 8 component table agrees: pread 66 ms of 185 ms).
- Achieved pread bandwidth drops with cache size (8.6 → 4.4 → 2.9 GB/s)
  — smaller miss batches queue less I/O; latency per read dominates, not
  raw bandwidth.
- Prefill: the 233-token prefill step **bypasses the cache entirely**
  (lookups=0, 19.98 GB read in one step), so decode starts with the cache
  warmed only by the 2+2+4-token batches (~8 prompt tokens). Prefill cache
  warm-up is a structural, currently-unexploited opportunity.

## Likely Benefit of Each Candidate Phase 9 Lever

| lever | evidence | expected value |
|---|---|---|
| **Replacement policy / pinning / frequency-aware eviction** | LRU→OPT = 40% at 4 GB, 35% at 8 GB; reuse tail 26+ steps; regret 20–33% at 4 GB | High: realistic policies (LFU-biased, pin hot experts, scan-resistant) can plausibly capture a third to half of the 115 MB/token policy gap at 4 GB (~10–20% total traffic cut, ~30–50% of reloads) |
| **Prefetch (hot-expert / recent-expert warm-up)** | 69% of reuses ≤8 steps; prefill bypass leaves decode cold | Medium-high: prefetching the last-k-steps working set + warming the cache during prefill attacks the same reload population as policy |
| **Async expert loading (I/O-compute overlap)** | pread = 37% of decode step; expert+route compute ≈ 36 ms vs 69 ms pread | High for latency: overlapping pread with the next layer's compute could hide most of 69 ms/step → step time 185→~120 ms, tok/s 5.4→~8 (1.4–1.5×) without changing bytes |
| **Batched/coalesced reads** | 3 preads/expert, 1.36 MB avg | Low-medium alone: syscall savings only; offsets are scattered (needs layout change to matter) |
| **Storage layout (group up/gate/down per expert; per-layer expert blocks)** | offsets far apart; pread BW falls with queue depth | Medium: enables 1 pread/expert and longer sequential runs; compounds with async and coalescing |
| **mmap/filesystem-cache tuning** | no direct measurement in Phase 8 (pread path) | Unknown — needs a probe; likely small unless combined with layout |

## Recommended Phase 9 Scope

Ordered by expected ROI and measurement support:

1. **Replacement policy work** (frequency/pinning-aware eviction) +
   **prefetch/warm-up** (including fixing the prefill bypass so the cache
   is warm at decode start). Target: capture a measurable share of the
   115 MB/token policy gap at 4 GB.
2. **Async expert loading** (I/O-compute overlap) — the clearest latency
   lever (up to ~1.4–1.5× tok/s at constant traffic).
3. Coalesced reads + storage-layout repack only if (1)+(2) measurements
   show I/O call/latency still binding.

Explicitly **not** recommended from this evidence alone: mmap/filesystem
tuning (unmeasured), new kernels, DIO, MXFP4 (Phase 10 concern).

## GO / NO-GO Decision

**GO** — with the scope above. Rationale against the pre-registered
criterion: an oracle replacement policy reduces decode traffic to
**171 MB/token at 4 GB** (40% below LRU) and **118 MB/token at 8 GB**
(≈ the "substantial unexploited locality" threshold). This is far from the
"oracle only gets 286 → 250" poor-ROI case. Caveat: OPT is an offline
oracle; a realistic policy will capture only part of the 40%, so Phase 9
should (a) implement policy + warm-up first and (b) measure the realized
gap before investing in layout/async work.

## Implications for Later MXFP4 Work (Phase 10)

- At 4 GB, **54% of decode traffic is capacity-bound even under OPT**
  (154.5 MB/token): experts whose reuse distance exceeds what any policy
  can retain at that capacity. This residual is the natural MXFP4 target —
  halving expert bytes doubles effective capacity, shifting the whole OPT
  curve down.
- The policy gap (40%) is **orthogonal** to MXFP4: better replacement pays
  off regardless of quant. Phase 9 policy work also de-risks Phase 10 by
  separating "policy inefficiency" from "capacity shortfall" in the final
  measurement.
- MXFP4 should be evaluated against the Phase 9-improved baseline, not the
  current LRU baseline, or its benefit will be overstated.

## Reproduction

```bash
# Replay + all metrics (writes benchmarks/results/phase-09-selection-gate.json)
python3 tools/phase09_selection_gate.py

# Key validation cross-check (any budget):
awk -F, 'NR>1 && $1!="step" && $2=="decode"{pb+=$5}END{print pb/128/1048576}' \
  benchmarks/results/phase-08/coding-lru/cap-4-r1/stats.csv   # 286.4
```

Environment: same artifacts as Phase 8 (`llama.cpp` e8baeb16e,
`benchmarks/results/phase-08`). No new inference runs were performed.
