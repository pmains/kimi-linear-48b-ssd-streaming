# Phase 9C Report — I/O–Compute Overlap Feasibility Gate

## Status

**PASS (analysis gate) — GO for async expert loading.** The dependency
structure of the frozen Phase 8 streamer was mapped, the decode critical
path was decomposed with isolated instrumentation, the pread workload was
characterized, and the realizable overlap bound was established with
measurements (runtime traces + a standalone read-schedule replay probe).
**The dependency-constrained bound is 1.30–1.53× decode tok/s (central
estimate ≈ 1.38×, ≥ 30%)** — the async lever is in the ">30%:
high-priority optimization" band of the pre-registered decision
framework, and the implementation can be small and low-risk (a bounded
worker pool issuing the layer's miss preads in parallel; no graph
changes, no numerics changes). The 1.4–1.5× upper-bound motivation from
the selection gate is **confirmed as substantially realizable**, not
because compute can hide the I/O (it mostly cannot — see §4), but
because the pread phase is **syscall-latency-bound, not
bandwidth-bound**: the decode read set is largely page-cache-resident,
and each read costs ~0.3–0.4 ms of issue latency. Parallel submission
removes ~90% of that latency directly. **No async subsystem was
implemented.** The instrumentation is isolated in the dev tree
(env-gated, behavior-inert: moe.csv md5-identical to the frozen Phase 8
baselines in all three runs) and was **not** promoted to the live
runtime.

## Objective

Answer the gate's central question: of the decode time attributed to
expert pread, how much can actually be removed from the critical path by
overlapping expert loading with useful computation (or by otherwise
restructuring the I/O)?

The selection gate's ~37% pread share and 1.4–1.5× potential speedup are
upper-bound motivation. This phase establishes the realizable bound given
the actual dependency structure, and pre-registers the implementation
decision.

## 1. Current Decode Pipeline and Dependency Map

### 1.1 Execution sequence (from code: `llama-expert-stream-exec.cpp` +
`llama-expert-stream.cpp`, dev tree `5472cc2e5` + 9C instrumentation)

One decode step (`n_tokens = 1`) executes:

```
preamble (embeddings → persistent act)          ~0.2 ms
for il in 0..47:
    route subgraph: dense trunk (attn/norm/KDA) + MoE router
        → ids tensor (ffn_moe_topk)             ~0.5 ms/layer (23 ms/step total)
    readback ids → host (get_async + moe dump)  ~35 µs/layer
    if MoE layer (26 of 48):
        load_layer(il, ids):
          Phase A  dedupe occurrences → unique experts (first-occurrence order)
          Phase A  zc cache lookup + slot assignment (per-layer LRU)   ~1-2 ms
          Phase C  slot_ids → backend tensor
          Phase D  per kind (up, gate, down):
                     for each miss slot: pread(slice)  ← ALL SEQUENTIAL
                     for each miss slot: repack (single-slice tensor_set)
                     for each miss slot: placement (memcpy → persistent slot)
                   (hits place nothing — zero-copy)
          sync                                             ~0 ms
    compute subgraph: stateless expert FNN over loaded tensors
        (mul_mat_id over persistent slots)       ~0.85 ms/layer (22 ms/step)
epilogue (output norm + lm_head)                 ~5.5 ms
```

The MoE layers are 26 of 48; dense layers complete entirely in the route
subgraph.

### 1.2 Where the expert IDs become known and where weights are first required

- **Expert IDs become known** at the end of the layer's route-subgraph
  compute (`route_il`), after the dense trunk produces the router input.
- **Weights are first required** by the layer's compute subgraph
  (`compute_il`), which cannot build/run until `load_layer` has placed
  every missing expert.
- `load_layer(il)` is called **immediately after** `route_il` completes
  and **strictly before** `compute_il` builds. There is no overlap and no
  idle-independent work between the two boundaries except the ~1-2 ms
  bookkeeping (dedupe + lookup + slot assignment).

### 1.3 Serialization facts (verified in code, confirmed by the per-layer
trace)

```
compute_{il-1} → route_il → [dedupe/lookup] → pread_il → repack_il →
placement_il → compute_il → route_{il+1} → ...
```

1. `route_il` consumes the hidden state produced by `compute_{il-1}`
   (layer il's attention input is layer il-1's output). Strict dependency.
2. `ids_il` are produced by `route_il`. Strict dependency.
3. `compute_il` needs the loaded weights. Strict dependency.
4. Therefore **all preads of layer il sit strictly between route_il and
   compute_il**, and route_{il+1} cannot start until compute_il finishes.
   The whole decode step is one serial chain; there is no other compute in
   flight anywhere in the step (single sequence, CPU backend, -ngl 0).
5. Within `load_layer`, the reads are issued **one syscall at a time**
   (`for kind { for slot { pread } }`, zc_place Phase D — confirmed in
   code). 3 kinds × N_miss reads, fully sequential. The repack of kind k
   runs only after all of kind k's reads complete; the reads of kind k+1
   do not start until kind k's repack+placement finish.
6. All 26 MoE layers' loads are on the critical path: **load_wall is
   75% of the decode step** (184 ms of 245 ms at 4 GB).

### 1.4 Does useful work exist between "identity known" and "weights
required"?

- Same-layer: only the ~1-2 ms bookkeeping (Phase A/C), which is already
  in the critical path and already minimal.
- Cross-layer: **no** — `route_{il+1}` requires `compute_il`, which
  requires the load. The load of layer il cannot overlap the compute of
  layer il-1 (ids not yet known) nor the route of layer il (it precedes
  the load).
- Cross-step: **no** — the next step's routing needs this step's logits.
- Consequence: with exact routing, the only same-step compute that can
  overlap a layer's pread is a *split* of that layer's own expert compute
  (hit experts first — option A/B below), and that is small.

## 2. Timing Decomposition (frozen Phase 8 behavior, instrumented)

Measured with the isolated per-layer/per-pread instrumentation
(`KIMI_PHASE9C_TRACE`), one run each, deterministic (moe.csv md5
identical to Phase 8 baselines: coding `da45ab…e5c`, reasoning
`e26205…b3`).

### 2.1 Coding @ 4 GB (operating point), 127 decode tokens

| component | ms/step (median) | % of decode wall |
|---:|---:|---:|
| **pread (I/O wait)** | **91.2** | **38.0** |
| **repack (layout transform)** | **61.4** | **26.0** |
| placement (slot memcpy) | 21.4 | 9.9 |
| route (dense trunk + router) | 23.2 | 12.6 |
| expert compute (FNN) | 22.1 | 9.5 |
| epilogue | 5.5 | 2.4 |
| load bookkeeping (dedupe/lookup/ids/sync) | 2.2 | 1.0 |
| readback | 1.1 | 0.5 |
| preamble | 0.2 | 0.1 |
| **step total** | **245.5** | 100 |
| throughput | 4.07 tok/s (in-session; env slower than Phase 8 dates) | |

Per-layer structure (step 20, representative MoE layers): route 0.5–0.9 ms,
load 3–10.5 ms (pread 1.2–5.2 ms, repack 0.9–3.6 ms, placement 0.6–2.2 ms),
compute 0.6–1.8 ms. The load dominates every layer.

### 2.2 Reasoning @ 8 GB (second operating point), 127 decode tokens

pread 68.3 ms (34.4%), repack 40.9 ms (20.7%), placement 22.5 ms (14.0%),
route 22.0 ms (14.4%), compute 23.1 ms (12.1%), epilogue 5.7 ms (2.9%);
step 203.4 ms, 4.92 tok/s.

### 2.3 Uncached (pure-I/O worst case), coding prompt

pread 121.8 ms (30.4%), repack 217.2 ms (45.4%), route 20.4 ms, compute
17.5 ms; step 484.3 ms, 2.06 tok/s. Repack scales with miss count (208
unique experts × 3 kinds every step) and becomes the largest component
uncached.

### 2.4 Attribution notes

pread_us / repack_us / placement_us / sync_us are per-syscall / per-op
timers summed in the streamer; route/compute are per-subgraph wall timers;
load_wall is the load_layer call duration (so load_other = wall −
pread−repack−placement−sync). No double counting: the sub-timers are
disjoint and their sum plus route/compute/readback/preamble/epilogue
reproduces the step wall within ~1%. Timing is cross-session
environment-sensitive (thermal/ambient load), but the *structure* (shares,
ordering) is stable and matches the Phase 8/9A decompositions (pread ~37%,
repack ~25%+).

## 3. I/O Workload Characterization

From the per-pread trace (every pread: offset, size, latency), decode
phase only:

| metric | coding @ 4 GB | reasoning @ 8 GB | uncached |
|---|---:|---:|---:|
| reads/token | 211.7 | 147.4 | 624 |
| bytes/token | 289.4 MB | 201.6 MB | 850.1 MB |
| read sizes | 1.33 MB (p50), 1.94 MB (p90) | same | same |
| per-read latency p50 / p99 / max | 388 / 2524 / 26036 µs | 410 / 2346 / — µs | 103 / 1757 / 25038 µs |
| achieved bandwidth (in-session) | 3.25 GB/s | 3.02 GB/s | 6.05 GB/s |

- **Read size distribution**: two sizes only — 1,327,104 B (Q4_K up/gate
  slices) and 1,935,360 B (Q6_K down slices). 3 reads per expert (up, gate,
  down), one per kind.
- **Sequential vs scattered**: the runtime issues reads in *routing
  (first-occurrence) order* → the issued stream is scattered. But the
  underlying offsets **cluster by kind**: within a (layer, kind) group,
  78% of offset-sorted consecutive reads are within one slice of each
  other and ~85% are contiguous (gap ≤ 0) in the uncached trace — expert
  slices are packed contiguously per tensor in the GGUF. The three kinds
  live in three separate tensors (far apart). So **per-kind coalescing is
  possible with zero layout change** (the `coalesced` streamer mode
  already implements sort+merge), contrary to the selection gate's
  "offsets far apart, not coalescible" note — that note was true across
  kinds, not within a kind.
- **Queueable independent reads**: all of a layer's miss reads are
  independent (disjoint staging regions, no ordering requirement).
  70–350 in flight per step at 4 GB.
- **Latency-bound vs bandwidth-bound**: in-session, the pread phase runs
  at 3.0–3.25 GB/s — essentially 1.33 MB / 0.39 ms per read, i.e.
  **per-read issue latency dominates**. The decode read set is largely
  **page-cache-resident** (24 GB machine; prefill loads ~20 GB; decode
  revisits it; only the p99/max tail is true SSD traffic). The standalone
  replay probe (same file, same offsets, warm cache) confirms this:
  single-step sequential replay costs 27–113 ms/step while **parallel
  issue (8–16 threads) collapses it to 3–10 ms/step** (44–51 GB/s — page-
  cache/memory bandwidth, impossible for the SSD). Even the *full 127-step
  replay* (38.5 GB, exceeding cache) shows seq 7.3 s → par16 3.3 s
  (5.27 → 11.64 GB/s, 2.2×), and a cold sequential ceiling (rawseq, first
  38.5 GB of the file, mostly never-read trunk bytes) of 8.27 GB/s.
- **Both latency- and bandwidth-bound, in different regimes**: at steady
  state the pread phase is latency-bound (parallel issue wins ~10×);
  against a truly cold cache it is bandwidth-bound (parallel issue still
  wins ~2–3× by saturating the SSD queue, but the floor is
  bytes/8–12 GB/s).

## 4. Overlap Windows (options A–D evaluated)

**A. Same-layer overlap (cached experts compute while missing experts
load).** Structurally possible only as a *graph split*: the ids tensor is
fully known after Phase C (before Phase D preads), and hit slots are
already resident. Compute over hit slots could run during the pread phase.
**Measured ceiling: ~11–15 ms/step** (expert compute ≈ 22 ms × hit
fraction ≈ 0.66). Cost: two mul_mat_id graphs + partial-sum reduction →
changes FP32 accumulation order → **breaks the bit-identical oracle
invariant**. Not recommended now.

**B. Expert-level pipelining (each expert computes as its read
completes).** Same accumulation-order problem (partial sums), finer-
grained. Ceiling identical to A. Not recommended now.

**C. Cross-layer prefetch/overlap (layer N+1 reads during layer N
compute).** **Infeasible with exact routing**: ids_{N+1} require
route_{N+1}, which requires compute_N (hidden-state dependency). No
amount of restructuring changes this short of *speculative* prefetch
(using layer N's ids or past-step history to guess layer N+1's demand) —
which changes traffic (extra reads), is a separate lever from overlap,
and is out of scope for this gate.

**D. Batched/asynchronous submission (many reads in flight; compute still
waits).** **Feasible and is the dominant lever.** The reads are
independent; the current code serializes them. Parallel issue (8–16
threads) measured 2.2× (full replay) to 9–11× (per step, warm cache) on
the pread wall. This requires **no compute overlap at all** — it removes
the issue-latency serialization directly. This is the mechanism that
makes the realizable bound approach the perfect-overlap bound.

**Verdict: A and B are small and correctness-risky; C is infeasible; D is
large and cheap. The async decision does not depend on compute overlap at
all.**

## 5. Bounds (coding @ 4 GB; reasoning @ 8 GB in parentheses)

All bounds computed from the measured per-step decomposition; tok/s uses
the in-session current rate (4.07 / 4.92 tok/s). Speedups are
multipliers, so they carry to any environment:

| bound | step ms | tok/s | speedup |
|---|---:|---:|---:|
| current (measured) | 245.5 (203.4) | 4.07 (4.92) | 1.00 |
| **THEORETICAL perfect overlap** (all 91 ms pread hidden behind compute) | 152.3 | 6.57 | **1.61×** (1.53×) |
| **dependency-constrained, parallel issue at measured par16 bandwidth (11.64 GB/s full-replay)** | 178.4 | 5.61 | **1.38×** (1.34×) |
| **dependency-constrained, cold-SSD floor (8.27 GB/s rawseq)** | 189.0 | 5.29 | **1.30×** (1.28×) |
| **dependency-constrained, steady-state warm (per-step par/seq ratios 6–11×, applies to all steps)** | ~160–165 | ~6.1–6.2 | **~1.49–1.53×** |

- The **perfect-overlap bound (1.61×)** is not the expected result; it
  assumes all I/O hides behind compute, which the dependency map shows is
  only ~11–15 ms/step achievable (option A).
- The **dependency-constrained bound (1.30–1.53×)** is the honest
  prediction. The gap to perfect is small because the perfect-overlap
  assumption was never the binding constraint: **the pread phase is
  serialized syscall latency, and parallel issue removes it without any
  overlap at all**.
- The spread across scenarios is the cache-warmth question: warm
  steady-state (the realistic long-session regime) → ~1.5×; cold cache →
  ~1.3×. **Central estimate: 1.35–1.45×.**

### Predicted tok/s range

- Coding @ 4 GB: **5.3–6.2 tok/s** in today's environment (from 4.07);
  on the Phase 8-dated environment (5.39 baseline) the same multipliers
  give **7.0–8.1 tok/s**.
- Reasoning @ 8 GB: **6.3–7.5 tok/s** in today's environment.

## 6. Implementation Mechanisms (compared; nothing implemented)

| mechanism | expected gain | complexity | notes |
|---|---|---|---|
| **Worker-thread pool (4–8 threads) issuing the layer's miss preads in parallel; per-kind barrier; existing repack/placement unchanged** | 1.30–1.53× (the bound above) | LOW: ~100–150 lines in `zc_place`/`load_layer`; staging regions already disjoint; join per layer | The recommended mechanism. Simplest thing that captures the measured window. |
| Per-kind pipelined repack (repack kind k while kinds k+1..2 reads are in flight) | +~5–10 ms/step beyond parallel issue (hides part of the 61 ms repack behind the read tail) | LOW-MEDIUM | Bundle with the pool; do NOT reorder repack before its kind's reads complete. |
| Platform async I/O (aio/io_uring/libkqueue) | no better than threads for page-cache-backed pread; adds completion-callback complexity | MEDIUM-HIGH | Not justified; pread into user buffers completes in kernel and the pool is sufficient. Reject per "do not select a more sophisticated async API merely because it is theoretically available." |
| Bounded read-ahead queues / per-layer miss queues | none beyond the pool | MEDIUM | The pool IS the bounded queue; a separate queue adds nothing. |
| Expert-level completion signaling (start compute per expert) | ≤ ~15 ms but breaks bit-identity (accumulation order) | HIGH (correctness) | Rejected at this gate (see §8). |
| Coalesced reads (sort miss offsets per kind, merge contiguous, reuse existing `coalesced` mode) | measured 2.4× on the full replay standalone; compounds with the pool across kinds | LOW (mode exists) | Secondary recommendation; do it as part of the same change if the sort cost (~1 ms) is acceptable. Not required for the GO. |
| Speculative cross-layer prefetch | unknown; changes traffic | MEDIUM-HIGH | Out of scope (policy-like lever; 9B closed the policy family). |

CPU overhead of the pool: 4–8 threads blocked on I/O; active only during
the ~10–40 ms read window of a ~200 ms step; idle otherwise. Memory
overhead: ~0 (staging buffers already exist per kind × slots; the pool
adds only thread stacks). Additional copies: none (reads land directly in
the existing per-slice staging). Synchronization cost: one barrier/join
per layer (~µs). SSD queue behavior: reads are 1.3–1.9 MB; measured no
degradation up to 32 threads (11.6–11.9 GB/s flat from 8→32).

## 7. Pre-Registered Decision

**GO — implement async expert loading (parallel pread issue + per-kind
pipelined repack) in a subsequent phase.**

Against the pre-registered framework (predicted throughput improvement):
- 1.30× (cold floor) → 30%, at the "20–30%: strong candidate" boundary.
- 1.38× (full-replay bandwidth) → 38%, ">30%: high-priority".
- ~1.5× (steady-state warm) → 50%, high-priority.
- **Central prediction ≈ 1.35–1.45× (35–45%)** → high-priority band.

Complexity/risk accounting does not overturn it: the recommended
mechanism is the simplest available (a bounded thread pool around the
existing sequential pread loop), touches no graph code, no cache policy,
no numerics, and preserves bit-identity by construction (per-slice repack
unchanged; only the arrival timing of bytes changes). The correctness
risk is limited to implementation discipline (staging lifetime, join
ordering), which the existing `KIMI_CACHE_SELFCHECK` byte-comparison and
the Phase 5 oracle already verify. Async submission alone is promising
**and** read coalescing should be considered simultaneously only as the
secondary, low-cost companion (per-kind sort+merge, machinery already
exists); the async pool is the primary lever.

**Scope of the GO** (for the implementation phase): parallel pread issue
only; no compute-split, no speculation, no platform async API, no layout
rewrite. Expected validation: bit-identical oracle + selfcheck + ladder
vs the frozen Phase 8 baseline.

## 8. Correctness Constraint

Any implementation must preserve exact model semantics and the existing
invariants:
- Async execution may change **when** bytes arrive, never **which** expert
  tensors are used or the computation performed. The read set and the
  per-slice repack transform must be byte-identical to the sequential
  path (same offsets, same slices, same repack kernel, same placement
  order into slots). Then bit-identity follows by construction, exactly
  as the sequential path already achieves (moe.csv md5, retrieval
  byte-exactness, max|Δ| = 0 oracle).
- The one design that **violates** this is any split of the MoE
  accumulation (options A/B): computing hit experts in a separate graph
  changes FP32 sum order and would break the bit-identical oracle. That
  is the explicit reason A/B are rejected at this gate despite being the
  only true "compute overlap" windows.
- Cache bookkeeping (LRU order, slot assignment, eviction) must remain
  untouched: parallel issue is only a reordering of the Phase D read loop.
- Validate with: Phase 5 oracle (A/B/C comparator, max|Δ| = 0), moe.csv
  md5 vs baseline, `KIMI_CACHE_SELFCHECK=1` slot byte-verification, and
  the `phase07_summarize` invariants.

## 9. Changes (analysis-only, isolated, not promoted)

- `llama.cpp/src/llama-expert-stream.h` / `.cpp`: env-gated per-pread
  trace (`trace_pread`, `KIMI_PHASE9C_TRACE=<path>` → `<path>.preads`),
  wired into all four pread sites (coalesced / legacy naive / cached miss
  / zerocopy miss). No behavior change when unset.
- `llama.cpp/src/llama-expert-stream-exec.cpp`: env-gated per-layer phase
  trace (preamble / per-layer route, readback, load wall + internal
  pread/repack/placement/sync/other / compute / epilogue).
- `tools/phase09c_io_probe.c`: standalone replay probe (seq / parN /
  coalesce / rawseq strategies) — never linked into the runtime.
- `tools/phase09c_analyze.py`: decomposition + bounds analysis; writes
  `p9c-analysis.json` per run.
- Dev tree commit `5472cc2e5` + working-tree instrumentation; live
  runtime (`runtime/live`, `cad716035`) untouched.

**Behavioral invariance evidence:** all three instrumented runs have
moe.csv md5 identical to the frozen Phase 8 baselines (coding
`da45ab777b0fdd99be62f6c46a642e5c`, reasoning `e26205ed69e8466120c9f199347ca2b3`);
identical router decisions → identical hidden states → identical tokens.

## 10. Problems / Limitations

- **Cross-session timing**: absolute tok/s is environment-dependent
  (today's ambient load is higher than the Phase 8 dates; in-session
  controls and speedup multipliers are the comparable quantities). The
  pread share (~34–38%) and per-read latencies are consistent across
  sessions.
- **Probe conditions**: the replay probe ran with a warm page cache
  immediately after inference; the per-step 9–11× gains assume the
  steady-state warm regime. The cold bound (1.30×) brackets the other
  end. The true steady-state value depends on how much of the ~20 GB
  prefill footprint stays page-cache-resident during a long session —
  likely most of it on this 24 GB machine, but that is an operating-
  regime assumption, not a measured invariant.
- **Repack remains on the critical path** (61 ms at 4 GB, 26% of the
  step). Async I/O does not remove it; pipelining hides only its overlap
  with the read tail. Cutting it further requires fewer misses (policy —
  closed by 9B) or fewer bytes (MXFP4/layout — later phases). After async
  lands, repack becomes the dominant load cost and the natural next
  measurement target.
- The `coalesced` mode's existing sort+merge was not benchmarked in-
  session for this report (probe only); its measured standalone effect
  (2.4×) is the secondary recommendation's evidence.

## 11. Decisions

- **GO for async expert loading** (parallel pread issue + per-kind
  pipelined repack) — central predicted 1.35–1.45×, above the >30%
  high-priority threshold, low complexity/risk.
- **Implement with a bounded worker-thread pool**; reject platform async
  I/O APIs (no advantage for page-cache-backed pread), read-ahead queues
  (the pool is the queue), and expert-level completion signaling
  (correctness).
- **Do not implement compute-split overlap (A/B)**: ≤ ~15 ms/step ceiling
  vs bit-identity risk (FP accumulation order).
- **Cross-layer overlap (C) is closed** by the dependency structure;
  speculative prefetch remains a separate, traffic-changing lever outside
  this gate's scope.
- **Consider per-kind read coalescing as a low-cost companion** (machinery
  exists; measured standalone 2.4×), not as a prerequisite.
- Keep the 9C instrumentation in the dev tree (env-gated, inert) for the
  implementation phase's validation; it is not promoted to the live
  runtime.
- Do not revisit this gate unless a later change (MXFP4 halving expert
  bytes, or a workload regime that thrashes the page cache) materially
  changes the latency-bound conclusion.

## 12. Next Phase

Implement the async expert-loading subsystem (Phase 9C-implementation or
Phase 9D), scoped to:

1. Bounded worker pool (4–8 threads) in `zc_place` Phase D (and the
   legacy cached-miss path): issue all miss preads of the layer in
   parallel; per-kind completion; then the existing repack/placement
   pipeline unchanged. Keep the sequential path available (env-gated A/B
   comparison vs frozen Phase 8).
2. Optional per-kind sort+merge reuse of the `coalesced` machinery before
   parallel issue.
3. Validate: Phase 5 oracle bit-identity (max|Δ| = 0), moe.csv md5,
   selfcheck on, `phase07_summarize` invariants, then a controlled ladder
   (coding 4 GB, reasoning 8 GB, uncached control) vs the frozen Phase 8
   baseline, reporting MB/token + hit rate (deterministic) and tok/s
   (in-session ratios).
4. Measure the realized pread-wall reduction and the new decomposition;
   if repack then dominates, that is the next gate's question.

## Reproduction

```bash
# Instrumented runs (dev tree; KIMI_PHASE9C_TRACE adds p9c-layer.csv +
# p9c-layer.csv.preads; moe.csv must stay md5-identical to Phase 8):
CTX=4096 KIMI_PHASE7_INSTR=1 KIMI_EXPERT_CACHE_MB=4096 \
  KIMI_EXPERT_CACHE_MODE=zerocopy KIMI_PHASE9C_TRACE=out/p9c-layer.csv \
  tools/phase04_run_streamed.sh out benchmarks/prompts/phase-03-coding-lru.md 128 1 naive

# Analysis (per-run JSON + bounds):
python3 tools/phase09c_analyze.py out --probe /tmp/p9c-probe-results.json

# I/O probe (compile once; schedule = "off len" lines, e.g. cols 4-5 of
# the .preads file):
cc -O2 -pthread -o /tmp/io_probe tools/phase09c_io_probe.c
awk -F, '!/^#/ && NF==6 {print $4,$5}' out/p9c-layer.csv.preads > sched.txt
/tmp/io_probe models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf sched.txt seq par8 par16 coalesce rawseq
```

Artifacts: `benchmarks/results/phase-09c/{coding-cap4-r1,reasoning-cap8-r1,coding-uncached-r1}/`
(stats.csv, p9c-layer.csv, p9c-layer.csv.preads, moe.csv, mem.csv,
cache_layers.csv), `p9c-analysis.json` per run, `/tmp/p9c-probe-results.json`
(probe results table), `tools/phase09c_io_probe.c`, `tools/phase09c_analyze.py`,
this report. Environment: llama.cpp dev tree `5472cc2e5` + 9C
instrumentation (worktree), Release build, CPU repack + Metal, `-ngl 0`,
ctx 4096, seed 1, temp 0, zerocopy cache mode. Model
`models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`
(unchanged). Live runtime untouched.
