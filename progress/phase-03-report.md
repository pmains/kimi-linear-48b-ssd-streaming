# Phase 3 Report — Expert Addressability & Routing Characterization

## Status

PASS

All eight acceptance criteria are met. Routing locality is a
characterization result (no pass/fail threshold); addressability and
Metal-residency criteria are demonstrated by measurements.

## Objective

Phase 3 was restructured (see ROADMAP diff) into two separable
workstreams:

- **3A — Routing characterization:** instrument unmodified Kimi Linear
  inference at the existing `ffn_moe_topk` routing point, collect real
  routing traces, and quantify locality across realistic cache
  capacities (global LRU, per-layer LRU, offline OPT; cold-start vs
  steady-state; prefill vs decode).
- **3B — Expert addressability:** make individual routed experts
  independently addressable from backing storage through the CPU → Metal
  boundary without materializing the complete routed-expert collection,
  and prove per-expert Metal residency by measurement.

## Changes

### ROADMAP.md

Rewrote Phase 3 into the 3A/3B structure with explicit acceptance
criteria (8 items), trace-collection requirements, cache-capacity
ladder, and the note that routing locality is a characterization result,
not a pass/fail threshold. Added Phase 4 "Next Phase" handoff notes.

### llama.cpp (working tree at `2606220d9`)

Environment-gated MoE routing tracer, inert unless `KIMI_TRACE_MOE=1`:

- `src/llama-graph.cpp`: `llm_moe_trace_rec` capture at
  `build_moe_ffn` (the `ffn_moe_topk` tensor is copied into a dedicated
  graph output so it survives scheduler buffer reuse); post-execution
  readback in `llm_trace_moe_dump()` writing
  `phase,n_tokens,layer,token_idx,e0..e7` CSV rows; recs dropped in
  `llm_graph_result::reset()` to avoid dangling readbacks.
- `src/llama-context.cpp`: `llm_trace_moe_dump()` invoked after graph
  execution in `process_ubatch`, tagging `prefill` vs `decode` by batch
  size.
- `src/llama-impl.h`: forward declaration.

Routing mathematics are untouched; the patch is fully inert unless the
env var is set (verified by smoke runs with tracing off).

### tools/

- `tools/expert_slice_verify.py` — GGUF header parsing, per-expert slice
  offset/stride computation, `pread` vs mmap byte-for-byte verification.
  Output: `benchmarks/results/phase-03-addressability.json`.
- `tools/expert_cache_sim.py` — locality metrics + cache simulation
  (global LRU over composite `(layer, expert_id)` keys, per-layer LRU
  with budget split across 26 layers, offline Belady/OPT), with
  prefill/decode and cold/steady splits. Output:
  `benchmarks/results/phase-03-locality.json`.
- `tools/metal_expert_probe.m` — standalone Metal residency probe
  (compile: `clang -O2 -fobjc-arc -framework Foundation -framework
  Metal`). Outputs:
  `benchmarks/results/phase-03-metal-residency.txt` and
  `benchmarks/results/phase-03-metal-residency.json`.

### benchmarks/

- `benchmarks/prompts/phase-03-coding-lru.md` — representative coding
  workload (concurrent LRU cache in Rust).
- `benchmarks/prompts/phase-03-reasoning-pumps.md` — reasoning
  workload (water-pump rates).
- `benchmarks/results/traces/phase-03-coding-lru.csv` — processed trace
  (12,607 access rows; the analysis input; authoritative).
- `benchmarks/results/traces/phase-03-coding-long.csv` — longer
  trace (11,255 rows; collected, not yet analyzed — deferred).
- `benchmarks/results/phase-03-locality.json`,
  `benchmarks/results/phase-03-addressability.json`,
  `benchmarks/results/phase-03-metal-residency.{txt,json}`.
- Root-level `trace_*.csv` / `smoke*.log` / `perf.{out,err}` are raw
  tracer dumps and smoke artifacts; superseded by the processed traces
  in `benchmarks/results/traces/`.

## Results

### 3A — Routing characterization (coding workload, CPU `-ngl 0`)

Trace: 494 tokens (239 prefill + 255 decode), 100,856 expert accesses
(26 MoE layers × 8 selected experts per token).

| Metric | Overall |
|---|---|
| Distinct `(layer, expert)` touched | 5,554 of 6,656 possible (83.5%) |
| Distinct experts per token | mean 204.2, max 208 (= 26×8) |
| Final working set | 5,554 |
| Accesses by top 1% experts | 15.4% |
| Accesses by top 10% experts | 47.4% |
| Reuse distance (tokens) | median 4, p90 46, mean 17.7 |
| Reuse distance (accesses) | median 209, p90 7,904, mean 3,080 |

Per-layer: 26 layers × 256 experts; each layer touched 192–246 distinct
experts over the run (of 256) — broad but not complete per-layer
coverage. Prefill (47,816 accesses, 4,775 distinct) vs decode (53,040
accesses): working set saturates during prefill; decode reuses it.

Cache simulations (capacities 1 → 14.6 GB, using measured 4.09 MiB avg
per expert; 1 GB ≈ 250 experts … 14.6 GB ≈ 3,658 experts):

| Capacity | Global LRU (overall / steady) | Per-layer LRU (overall / steady) | OPT (overall / steady) |
|---|---|---|---|
| 1 GB | 59.0% / 32.7% | 27.0% / 26.7% | 70.4% / 53.4% |
| 4 GB | 73.0% / 58.2% | 58.9% / 57.9% | 83.8% / 76.4% |
| 8 GB | 81.9% / 74.2% | 76.0% / 74.0% | 90.0% / 85.9% |
| 12 GB | 88.3% / 84.8% | 86.0% / 84.1% | 93.0% / 89.8% |
| 14.6 GB | 90.9% / 88.4% | 90.1% / 88.0% | 94.0% / 90.4% |

Threshold capacities for 90% hit rate: global LRU overall and per-layer
LRU overall at ~14.6 GB; OPT overall at ~10 GB; OPT steady-state at
~14.6 GB. Global LRU steady-state peaks at 88.4% (90% not reached in
range). **95% and 99% hit rates are not attainable at any tested
capacity with any policy** on this workload. The LRU-vs-OPT gap is
~3 pp at 14.6 GB but ~11 pp at 1 GB, suggesting policy gains only at
small budgets; Phase 6 cache-policy decisions are informed, not made,
here.

### 3B — Expert addressability

- Reused the Phase 2 GGUF offset table; `offsets_match_phase2 = true`.
- 144 byte-range `pread` checks across layers 1, 4, 26; gate/up/down;
  Q4_K and Q6_K; boundary experts (0, 63, 127, 128, 200, 254, 255);
  **all match the mmap view byte-for-byte**.
- Per-expert sizes: gate/up Q4_K 1,327,104 B; down Q6_K 1,935,360 B
  (13 layers: 1,2,3,6,9,12,15,18,21,23,24,25,26) or Q4_K 1,327,104 B
  (13 layers); measured average per expert (gate+up+down) 4,285,440 B.
- No quantization or model-mathematics changes.

### 3B — Metal residency (Apple M5, recommendedMaxWorkingSetSize 17.8 GiB)

Mechanism evaluated and chosen: **independent per-expert
`MTLResourceStorageModeShared` buffers** (one per retrieved expert
slice; `pread` + `memcpy`), rather than a shared arena — smallest
mechanism adequate for Phase 4.

- 8 slices (Q4_K and Q6_K, both ends of the 30 GB file): allocation
  deltas exactly at expert scale — 1.27 MiB per Q4_K expert
  (1,327,104 B) and 1.86 MiB per Q6_K expert (1,935,360 B); all
  byte-exact round trips OK.
- Parent-tensor contrast: one buffer covering L1 gate's full 3D extent
  allocates 324.0 MiB (256 × 1,327,104 B).
- Total requested per-expert bytes 11.87 MiB → RSS delta 15.72 MiB
  (includes `malloc`/page overhead); `currentAllocatedSize` tracks
  expert-scale increments.
- **Finding:** per-expert buffers allocate at expert scale; no Metal
  buffer covers or materializes the complete parent 3D expert tensor;
  loading additional experts grows managed residency at expert-scale
  granularity.

Smoke runs of the traced build: CPU decode works (Prompt 2.9 t/s,
Generation 2.3 t/s on the 30 GB Q4_K_M file, consistent with Phase 2
CPU findings).

## Problems

- **Trace summary token miscount (minor):** the locality JSON summary
  reports 494 tokens / 229 prefill; the trace itself yields 239 prefill
  tokens (the chat template adds ~10 prompt tokens routed through the
  first graph). All access-stream statistics use the raw rows, so this
  does not affect any hit-rate or locality result. Flagged for the
  analysis script's `summary` field if rerun.
- **Metal residency measurement:** `currentAllocatedSize` + RSS deltas
  are allocation-level evidence, not page-granular residency (unified
  memory). Adequate per "measure rather than infer"; a
  `MTLHeap`/arena comparison was deliberately deferred.
- **Coverage:** locality analysis is based on one coding workload in
  depth. The reasoning-workload prompt and a longer trace were
  collected but not analyzed into the JSON (deferred; tracer is
  reusable). Trace expert IDs are per-layer (0–255); global identity is
  the composite `(layer, expert_id)` used throughout the analysis.
- **CPU-only tracing:** conventional Metal execution OOMs (Phase 2);
  routing semantics are backend-independent, so this is acceptable.

## Decisions

1. **Env-gated tracer at `ffn_moe_topk`** with post-execution readback
   and a dedicated copy output tensor; inert unless `KIMI_TRACE_MOE=1`.
   Kept as a reusable diagnostic: recorded router decisions can serve as
   an oracle for Phase 5 correctness comparisons and inform Phase 6
   cache design.
2. **Composite `(layer, expert_id)` keys** for all locality and cache
   analysis (global expert space 26 × 256 = 6,656).
3. **Measured per-expert bytes (4.09 MiB avg)** used for capacity
   mapping instead of the roadmap's 4.59 MB estimate; documented in
   `tools/expert_cache_sim.py`.
4. **Independent per-expert shared MTLBuffers** chosen as the Phase 4
   residency mechanism; shared-arena approach recorded as a future
   alternative (Phase 6+).
5. **Phase 3 split into 3A/3B** in ROADMAP to keep characterization and
   addressability results separable, per roadmap revision.

## Next Phase

Phase 4 (uncached expert streaming) consumes:

- the verified `(layer, expert_id, tensor) → GGUF offset + length` map
  (`benchmarks/results/phase-03-addressability.json`) for `pread`-based
  retrieval;
- the per-expert shared-MTLBuffer mechanism proven in
  `benchmarks/results/phase-03-metal-residency.json`;
- the Phase 2 offset table (already cross-checked, `offsets_match_phase2
  = true`).

Key inputs to Phase 5/6: routing traces under
`benchmarks/results/traces/` (Phase 5 oracle) and the locality JSON
(Phase 6 cache design — expect poor steady-state hit rates below ~8 GB
and no 95%+ regime on this workload class with LRU-class policies).

## Reproduction

Environment: Apple M5 MacBook Air (24 GB unified), macOS 25.5, Metal
device recommendedMaxWorkingSetSize 17.8 GiB. llama.cpp at
`2606220d9` + Phase 3 tracer patch. Model:
`models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`
(30,061,058,720 B, Q4_K_M).

Collect a routing trace (CPU; tracing must be on):

```sh
KIMI_TRACE_MOE=1 KIMI_TRACE_MOE_FILE=/tmp/kimi_moe_trace.csv \
  llama.cpp/build-metal/bin/llama-cli \
  -m models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf \
  -ngl 0 -f benchmarks/prompts/phase-03-coding-lru.md -n 255 \
  --no-display-prompt > /tmp/smoke.log 2>&1
```

Process the trace (expect ~12,607 rows for the coding workload):

```sh
python3 tools/expert_cache_sim.py \
  benchmarks/results/traces/phase-03-coding-lru.csv \
  benchmarks/results/phase-03-locality.json
```

Verify expert addressability (expect 144/144 checks, offsets match
Phase 2):

```sh
python3 tools/expert_slice_verify.py
```

Metal residency probe (offsets from the verified addressability JSON;
parent = L1 gate full extent 339,738,624 B):

```sh
clang -O2 -fobjc-arc -framework Foundation -framework Metal \
  tools/metal_expert_probe.m -o /tmp/metal_expert_probe
/tmp/metal_expert_probe \
  models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf \
  1121734784 339738624 \
  1121734784 1327104 1460146304 1327104 1466348672 1327104 \
  623775872 1935360 1117292672 1935360 4254951808 1327104 \
  29362808320 1935360 29705661952 1327104
```
