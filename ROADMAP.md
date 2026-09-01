# ROADMAP.md

# Kimi Linear Streaming Inference

## Goal

Run `Kimi-Linear-48B-A3B-Instruct` productively on a 24GB Apple
Silicon Mac without keeping the complete model resident in unified memory.

The strategy is:

1. Use an established native inference runtime rather than build a new one.
2. Reuse Kimi Linear's existing MoE routing and model implementation.
3. Keep the non-routed model trunk resident where practical.
4. Keep routed experts primarily on SSD.
5. Load selected experts on demand.
6. Maintain a bounded in-memory expert cache.
7. Minimize the resident non-expert footprint; allocate the remaining
   memory budget between expert cache and operating headroom to maximize
   throughput.
8. Preserve enough throughput for productive interactive coding.

The implementation should preferentially build on `llama.cpp` / `ggml`
and its Apple Metal backend.

Phase 1 must validate that this is a suitable implementation base before
substantial development begins.

The initial streaming experiment preserves the existing quantization.

MXFP4 is a later stage. Do not combine quantization work with initial
streaming validation.

---

## Phase 1 — Runtime and Architecture Validation

### Goal

Determine whether `llama.cpp` provides a suitable foundation for
Kimi Linear expert streaming and identify the smallest integration
surface required.

### Work

Inspect the current Kimi Linear implementation in `llama.cpp`.

Identify:

- model loading and GGUF tensor handling;
- Kimi Linear model construction;
- KDA/attention execution;
- MoE routing;
- routed-expert representation;
- expert execution;
- Metal/ggml execution path;
- when and how expert weights become resident.

Determine whether routed expert weights can be virtualized without
rewriting Kimi Linear's mathematical implementation or the broader
inference runtime.

MLX or another known-working implementation may be used as a reference
for model behavior.

### Acceptance

We can document:

    GGUF/model load
          ↓
    Kimi Linear graph
          ↓
       MoE router
          ↓
    selected experts
          ↓
     expert weights
          ↓
     ggml / Metal
          ↓
        output

and identify the specific code boundaries where expert storage and
retrieval can be changed.

The phase report must explicitly recommend:

    PROCEED WITH LLAMA.CPP

or:

    REJECT LLAMA.CPP

with evidence.

If rejected, stop and select another implementation strategy before
continuing.

### Report

    progress/phase-01-report.md

---

## Phase 2 — Model Memory Inventory

### Goal

Determine what actually consumes storage and resident memory.

### Work

Inventory the model's tensors and classify them as:

- non-routed trunk;
- shared expert;
- routed experts;
- other required state.

Measure:

- total model size;
- routed-expert size;
- non-routed weight size;
- expert size by layer;
- runtime memory that cannot be paged;
- theoretical minimum resident footprint.

Distinguish checkpoint size from actual runtime memory consumption.

Establish the memory equation explicitly:

    trunk + runtime/state + context/KV + expert working set + safety margin <= 24 GB

where:

- `trunk` is the non-routed resident component (embeddings, attention, dense weights);
- `runtime/state` is unavoidable runtime memory (graph buffers, Metal buffers, tokenizer, etc.);
- `context/KV` is the KV/state cache at a chosen context size;
- `expert working set` is the resident routed-expert portion (cache);
- `safety margin` covers the OS and other processes.

Measure the equation at representative context sizes: start with 8K, 32K, and 128K, subject to what Kimi Linear/llama.cpp actually allocates. A cache budget that only works at an impractically small coding context does not count as feasible.

### Execution Plan

Keep two kinds of accounting separate and do not confuse them:

- **model-file accounting** — bytes on disk / in the GGUF (static, computable from metadata);
- **Metal resident-memory accounting** — what is actually resident in unified memory at runtime (must be measured).

An 8 GB theoretical resident set does not imply an 8 GB process RSS or Metal footprint.

The empirical equation is the one that ultimately matters:

    resident trunk + resident shared experts + runtime/Metal overhead
    + context state + active routed experts + cache/buffering
    + safety margin <= 24 GB

The especially interesting output of this phase is not merely "does it fit?"
It is **how much of the 24 GB can be converted into an expert cache at each
context size** — that determines whether SSD-backed expert streaming has any
realistic chance of performing well.

Sequencing:

1. **Static inventory first** — from the two tiny official metadata files
   (`config.json` + `model.safetensors.index.json`), with no full checkpoint
   download. This answers the architectural questions: routed/shared/trunk
   split, tensor dimensions, parameter counts, approximate quantized sizes,
   per-layer expert footprint, and the theoretical resident floor.
2. **GGUF is a separate runtime-validation dependency** — uncertainty about
   community GGUF compatibility must not block the static analysis. Once the
   inventory exists, it defines what characteristics the GGUF must have and
   which measurements actually matter. The GGUF is required only for the
   runtime-resident half of this phase (acceptance questions 4 and 5).

### Acceptance

We can answer:

1. How much of the model consists of routed experts?
2. How large is the unavoidable resident component?
3. How large are individual experts?
4. What memory remains available for an expert cache at each representative context size (8K/32K/128K, subject to actual allocations)?
5. Is an approximately 8GB model-related resident target feasible?

### Report

    progress/phase-02-report.md

---

## Phase 3 — Expert Addressability

### Goal

Characterize routed-expert locality and make individual routed experts
independently addressable from backing storage through the CPU → Metal
boundary, without materializing the complete routed-expert collection.

Keep the two workstreams separable:

- routing characterization (3A) informs Phase 6 cache design;
- expert addressability (3B) is a prerequisite for Phase 4 streaming.

### Work

#### 1. Measure routing-trace locality

Instrument unmodified Kimi Linear inference at the existing MoE routing
point.

In the current `llama.cpp` tree, `build_moe_ffn` in
`src/llama-graph.cpp` computes:

    selected_experts = ggml_argsort_top_k(
        ctx0,
        selection_probs,
        n_expert_used
    )

The resulting I32 tensor has shape `[8, n_tokens]` and is emitted as
`ffn_moe_topk`. Kimi Linear invokes this path for its 26 MoE layers.

Implement an environment-gated trace readback after graph execution, not
during graph construction. The patch must be inert when tracing is
disabled and must not alter routing mathematics.

Collect traces using CPU execution (`-ngl 0`). Metal execution is not
required for routing characterization because conventional Metal
execution OOMs and routing semantics are backend-independent.

Collect enough real inference to distinguish cold-start from
steady-state behavior, including:

- a representative coding/reasoning workload;
- a sufficiently long autoregressive decode;
- prefill and decode identified separately where practical.

For every token and MoE layer, record the eight selected routed expert
IDs.

Measure:

- distinct experts touched per token and over sliding windows;
- cumulative working-set growth;
- routing frequency and skew across all 6,656 routed experts;
- per-layer routing distributions;
- reuse distance in tokens and intervening expert accesses;
- cold-start versus steady-state locality;
- prefill versus decode locality.

Simulate cache capacities spanning the Phase 8 ladder, approximately:

    1 GB → ~218 experts
    2 GB → ~436
    4 GB → ~871
    6 GB → ~1,307
    8 GB → ~1,743
    10 GB → ~2,179
    12 GB → ~2,614

Also include the maximum practical capacities implied by the Phase 2
Metal budget where useful.

At each capacity, calculate:

- global LRU hit rate;
- per-layer LRU hit rate;
- offline Belady/OPT hit rate as an upper bound;
- cold-start and steady-state results separately.

Determine the cache capacities required to reach 90%, 95%, and 99% hit
rates where those thresholds are attainable.

The LRU-versus-OPT gap should inform whether Phase 6 needs cache policy
more sophisticated than LRU.

Do not optimize the production cache policy in this phase.

#### 2. Make routed experts independently addressable

Reuse the Phase 2 GGUF header analysis and its existing per-layer offset
table.

Do not duplicate GGUF parsing or recompute information already
established there.

Map:

    (layer, expert_id, tensor)
             ↓
    GGUF byte offset + length
             ↓
            pread()
             ↓
    independently managed expert bytes
             ↓
    standalone/shared Metal buffer region

Use direct byte-range retrieval with `pread`, based on the Phase 2
relationship:

    offset = tensor.offset + expert_id * per_expert_bytes

Preserve the existing GGUF quantization:

- Q4_K gate;
- Q4_K up;
- Q6_K down.

Validate retrieval by comparing `pread` results byte-for-byte against
the corresponding mmap view for multiple experts, layers, and tensor
types, including both Q4_K and Q6_K boundaries.

#### 3. Prove independent Metal residency

Build a minimal standalone Metal probe and reusable per-expert buffer
utility.

Evaluate the minimum mechanism necessary for later streaming, such as:

- independently allocated per-expert `MTLBuffer`s; or
- subranges of a bounded shared-mode Metal arena.

Measure rather than infer residency behavior.

Demonstrate that:

- one expert's retrieved quantized bytes can be placed into
  Metal-accessible shared memory;
- allocation occurs at approximately per-expert scale rather than
  parent-tensor scale;
- no Metal buffer covers or materializes the complete parent 3D expert
  tensor extent;
- loading additional experts increases managed residency according to
  expert-scale allocations.

Record the chosen mechanism and evidence supporting it.

Do not integrate these buffers into the Kimi Linear compute graph in
this phase. Graph surgery and streamed execution belong to Phase 4.

### Acceptance

Phase 3 passes when:

1. A reproducible routing trace has been collected from real Kimi Linear
   inference at the existing `ffn_moe_topk` routing point.
2. Routing locality has been quantified across realistic cache
   capacities, including global LRU, per-layer LRU, and offline OPT
   curves, with cold-start and steady-state behavior distinguished.
3. Cache capacities required for 90%, 95%, and 99% hit rates have been
   identified where attainable.
4. An arbitrary routed expert can be located directly from
   `(layer, expert_id, tensor)` using the Phase 2 GGUF offset metadata.
5. Q4_K and Q6_K expert slices retrieved through `pread` have been
   verified byte-for-byte against their corresponding mmap ranges.
6. An individual expert can be placed in independently managed
   Metal-accessible memory at expert-scale allocation granularity.
7. Measured Metal behavior confirms that doing so does not allocate,
   upload, or make resident the complete parent 3D expert tensor.
8. Existing quantization and model mathematics remain unchanged.

Routing locality is a characterization result, not a pass/fail
threshold. Poor locality does not fail Phase 3; it informs the
feasibility and cache-policy decisions of subsequent phases.

### Report

    progress/phase-03-report.md

Keep routing-characterization and expert-addressability results as
distinct report sections.

### Next Phase

Phase 4 consumes the independently addressable expert-buffer mechanism
to implement streamed execution.

Preserve the Phase 3 routing tracer as a reusable diagnostic. Its
recorded router decisions can later serve as an oracle for Phase 5
correctness comparisons, while its locality results inform Phase 6 cache
design.

---

## Phase 4 — Uncached Expert Streaming

### Goal

Execute Kimi Linear while routed experts remain primarily on SSD, and
deliberately expose the worst-case miss path so its components can be
measured and decomposed.

Phase 3 showed that a ~90% expert-cache hit rate requires ~14.6 GB, and
that OPT improves the large-cache result by only ~3 pp. Policy is not the
escape hatch; miss-path latency is. Phase 4 therefore measures the cost
of a cache miss in the proposed architecture BEFORE any caching exists.

### Work

Change the expert-weight access path so that the existing router selects
experts normally, but required expert weights are obtained from backing
storage on demand, uncached: every routed expert access is a deliberate
miss.

Conceptually:

    existing router
          ↓
    selected experts
          ↓
    expert storage layer (uncached)
          ↓
      ggml / Metal

Discipline: do not build caching in this phase. No cache, no prefetch
policy, no eviction. Each expert access exercises the full storage →
Metal path so the worst case is measured directly.

Instrument and decompose the per-expert-access miss path into:

- storage read (`pread`);
- buffer preparation / copy if any (RAM → Metal-accessible buffer);
- Metal synchronization;
- kernel execution;
- total expert-access latency.

### Acceptance

1. The model generates correctly while routed experts are loaded on
demand from backing storage (uncached).
2. Resident memory is measurably lower than conventional execution.
3. A latency decomposition of the miss path is produced, covering the
components above, from real inference on the target model.
4. The decomposition is combined with the Phase 3 routing trace to
project expected tok/s across the 1–12 GB cache ladder (and the Phase 2
Metal budget), BEFORE Phase 6 cache implementation.
5. No cache, eviction, or prefetch policy is implemented or measured as
an optimization in this phase.

End-to-end tok/s is not the primary acceptance criterion; the
component-level decomposition and the resulting tok/s projection are.

### Report

    progress/phase-04-report.md

### Status vs Plan (2026-08-13 project-health audit)

Phase 4 is **PARTIAL**. The streamed executor runs and retrieves
correct bytes, but does not yet reproduce conventional inference.
Every acceptance item below is annotated with its true state and the
evidence location. Do not read the Phase 4 prose above as describing
completed work.

| Item | State | Evidence |
|---|---|---|
| 4A.1 refactor equivalence (route/compute split bit-identical) | PASS | conventional oracle re-captured post-refactor at llama.cpp `646723879`; identical to pre-refactor traces |
| 4A.2 retrieval equivalence | PASS | 36,288/36,288 expert ranges byte-exact vs mmap (occurrence-aware) |
| 4A.2 numerical equivalence | PASS (bit-identical) | A/B/C oracle post-fix: 36,288/36,288 retrieval exact, 1,512 router rows bit-identical, all activations + logits bit-identical. Root cause: CPU "repack" buffer type (see report). |
| 4B latency decomposition | PASS | `stats.csv` per-step deltas valid; `build_us` small positive. See `phase-04-miss-path.json`. |
| 4C cache-ladder projection | PASS | `phase-04-projection.csv`: 1.42 → 2.12 tok/s across 1→14.6 GB. |
| Acceptance 1 (correct generation, uncached) | PASS | generates; numerically bit-identical to conventional. |
| Acceptance 2 (resident memory measurably lower) | NOT MET | streamed executor reuses the unchanged model load, so the full 256-expert collection (repacked ~28 GB) stays resident; only the compute path changed. Requires skipping/paging expert tensors at load → Phase 4D. |
| Acceptance 3 (miss-path latency decomposition) | PASS | pread 417 ms / copy 242 ms / sync 0 / kernel 42 ms / trunk 110 ms / build 29 ms = 841 ms (1.19 tok/s). |
| Acceptance 4 (tok/s projection from decomposition + phase 3 trace) | PASS | conservative lower bound; floor 1.42 tok/s @1 GB → 2.12 tok/s @14.6 GB. |
| Acceptance 5 (no cache/eviction/prefetch implemented) | PASS | by construction: single-use expert buffers, no reuse |

Canonical failure statement (one paragraph):

> The streamed path executes Kimi Linear with routed experts retrieved
> individually from the GGUF via pread instead of a resident expert
> collection. Retrieval and compact-slot mapping are independently
> verified (36,288 byte-exact ranges). Inference completes at roughly
> 4.0 t/s prefill / 1.3 t/s decode (provisional — correctness fails),
> but it does not yet reproduce conventional inference: the router
> trace first differs at row 13 (layer 7, token 1 — same expert set,
> ordering swapped) and activations first diverge at layer 3, before
> the first router difference. Phase 4 therefore remains PARTIAL. The
> next diagnostic question is whether the row-13 routing mismatch is
> causal or symptomatic of the earlier activation drift.

---

## Phase 4D — Pageable Model Loading

### Status (2026-08-14)

**PARTIAL** — correctness proven, memory half-open. See
`progress/phase-04d-report.md`.

- Virtualized load implemented (env-gated, inert when streaming disabled):
  the repack buffer drops from 28,356 MiB to 1,153 MiB; 26.56 GiB of routed
  experts are never copied or repacked at load. Loaded experts still allocate
  in the CPU_REPACK buft (hard requirement).
- All three Phase 5 oracle comparisons are bit-identical (max|Δ| = 0) under
  the virtualized loader; conventional path byte-unchanged when disabled.
- Acceptance 2 is **not yet met at the process level**: the streamed
  runtime's own allocations (~1 GB steady-state; ~5-6 GB prefill peak) keep
  process RSS ≥ conventional. Model-weight residency is eliminated; the
  remaining item is streamed-runtime residency (executor/scheduler pool
  sizing and per-layer buffer churn). The scoped follow-up is **Phase 4E**
  below.

### Goal

Close Phase 4 acceptance item 2: make routed-expert weights **non-resident at
load** while retaining enough metadata and backing-file information for the
streamer to retrieve them on demand.

This is not Phase 6 groundwork. It is the final missing proof of the original
streaming premise: that inference can run correctly while the ~28 GB repacked
expert collection is *not* materialized in unified memory. Phase 6 must begin
with a genuinely low-residency streamer.

### Work

Modify model loading (streamed mode only, env-gated, inert by default) so the
routed-expert parent tensors are not materialized into resident buffers:

    GGUF
      ↓
    load trunk/shared weights normally (resident)
      ↓
    skip resident routed experts (virtual parents: metadata only, no data)
      ↓
    pread selected experts from backing storage
      ↓
    repack into the correct CPU buffer type (same layout the conventional
    path computes over)
      ↓
    compute
      ↓
    bit-identical output

Code-reading basis (llama.cpp at `60dc29e64` + streamer):

- Routed experts are the 3-D `blk.*.ffn_{gate,up,down}_exps.weight` tensors
  consumed by `GGML_OP_MUL_MAT_ID`; on ARM they are assigned to the CPU repack
  buffer type, so the repack-buft context contains exactly the routed-expert
  tensors.
- Authoritative GGUF offsets are already recorded per tensor at load
  (`llama_model_base::create_tensor` → `tensor_offsets`), and the streamer
  already preads from those offsets — the retrieval half is independent of
  parent residency.
- The streamed compute path reads parent tensors for **metadata only**
  (`ne`/`nb`/`type`/`name`/`buffer`); the only `parent->data` dereferences are
  in env-gated `KIMI_DX_VERIFY` diagnostics (off by default).
- Integration point: the backend-buffer allocation loop in
  `llama_model::load` (`src/llama-model.cpp`). In streamed mode, skip real
  buffer allocation and data load for the expert-only repack-buft context
  (dummy buffer, excluded from `load_all_data`), so no bytes are copied or
  repacked at load.
- Caveat: `load_layer` derives the loaded-experts' buffer type from
  `t_up->buffer`. Virtualization must preserve the repack buft identity
  (e.g. dummy buffer allocated from the same repack buft), or the ~1e-8
  repack-layout seed returns and bit-identity is lost.
- Guard Metal offload so virtual expert tensors are skipped there too
  (defensive; the CPU oracle is the validation target).

Then rerun the Phase 5 oracle. The target pipeline is the diagram above, and
the expected result is bit-identical output with resident memory measurably
lower than conventional load.

### Acceptance

1. Streamed generation remains bit-identical to the conventional oracle with
   the expert parents virtualized (same A/B/C comparator, same prompts).
2. Resident memory is measurably lower than conventional execution (RSS
   measurement, streamed-vs-conventional, same workload).
3. The conventional path is byte-unchanged when streamed mode is disabled.
4. No cache, eviction, or prefetch policy is introduced (still uncached).

### Report

    progress/phase-04d-report.md

### Exit

Phase 4D PASS closes Phase 4 (acceptance 2 satisfied). Phase 5 is then
re-validated on the low-residency streamer and becomes authoritative (no
longer provisional).

---

## Phase 4E — Runtime Residency Cleanup

### Status (2026-08-14)

**PASS** — see `progress/phase-04e-report.md`. Acceptance item 2 is met at
process level: decode phys_footprint (ctx 4096, --no-mmap) is 3.1 GB vs
conventional 28.2 GB (9× lower); prefill peak 3.1 GB; scheduler pools
5,368 → 81 MB; malloc-cached empty regions 1.2 GB → 305 MB; oracle
bit-identical. Key findings: (1) the streamed executor never needs the
5.4 GB worst-case scheduler reservation — skipped in streamed mode;
(2) the 8 GB KV cache at default n_ctx (1,048,576) is a shared config
artifact — memory comparisons must pin --ctx-size; (3) macOS's malloc zone
never returns freed large regions to the OS (pressure relief = 0 bytes),
so expert staging is vm-backed scratch (ggml_aligned_malloc) reused and
released at the prefill→decode transition.

### Goal

Make the uncached streamer's runtime residency reflect the memory
architecture already proven: the ~1.5 GB permanent model footprint (trunk
weights + persist tensors) plus a bounded streaming workspace during
steady-state decode. Eliminate accidental executor residency so Phase 6's
cache is a deliberate, controllable memory cost — measurable against a
clean zero-cache intercept — rather than being confounded with executor
churn.

### Work

Measured churn sources in the streamed executor
(`src/llama-expert-stream-exec.cpp`, `src/llama-expert-stream.cpp`):

- `stream_begin` pre-allocates a fresh ggml context with a fixed 32 MiB
  slack (`stream_ctx_size`) per subgraph; one step creates ~98 contexts
  (preamble + 2×48 layer graphs + epilogue + per-layer load ctx), all
  freed the same step — ~3 GB/step of ggml pool alloc/free churn.
- `load_layer` allocates a fresh load context, three expert backend
  buffers, and packed host staging per layer (freed one layer late).
- The scheduler's per-backend pools grow to the largest graph ever
  allocated (prefill) and are retained through decode
  (`ggml_backend_sched_reset` does not shrink).

Work items:

1. **Right-size and reuse ggml contexts.** Measure actual per-subgraph
   pool growth for decode vs prefill; size contexts to real decode need
   (the 32 MiB slack is ~300× the decode-graph metadata) and reuse a
   bounded pool of contexts instead of per-step init/free churn.
2. **Reuse expert staging buffers.** Keep the load context, the
   loaded-expert backend buffers, and the packed host staging across
   layers/steps, sized to a bounded worst case, instead of per-layer
   alloc/free (malloc retains ~1.2 GB of freed large regions).
3. **Bound scheduler pools to the decode graph.** Give steady-state
   decode a decode-sized scheduler path so it does not retain
   prefill-sized pools; prefill keeps its own path.
4. **Instrument live allocations.** Env-gated per-step report of process
   footprint (`task_info` phys_footprint, the reliable macOS number —
   includes compressed memory), malloc-zone in-use bytes, and scheduler
   pool bytes (`ggml_backend_sched_get_buffer_size`) to a CSV.

### Acceptance

1. The Phase 5 oracle stays bit-identical (A/B/C, max|Δ| = 0).
2. No cache, eviction, or prefetch policy is introduced (still uncached).
3. Steady-state decode memory is materially lower than conventional on
   the same metric. Primary metric: per-step phys_footprint during decode
   (not ru_maxrss, which compression makes optimistic for conventional).
4. Prefill peak RSS (--no-mmap) below ~8 GB per the 4D report target.

### Report

    progress/phase-04e-report.md

### Exit

Phase 4E PASS closes Phase 4 acceptance item 2. Phase 5 remains
authoritative. Phase 6 then begins from the measured zero-cache
memory/performance intercept and asks the clean question: how much RAM
should we deliberately spend on cached, backend-ready experts to maximize
tokens/sec?

---

## Phase 5 — Correctness Validation

### Status (2026-08-14)

**PASS (closed)** — streamed execution is bit-identical to conventional
across all three oracle comparisons (10/32/64 tokens, two prompts,
max|Δ| = 0), re-verified on the final low-residency streamer (`8b43eac45`,
`fix-oracle` run). The provisional tag from the 4D-era hold is removed:
Phase 4 acceptance 2 was closed by Phase 4E PASS (~2.0 GB decode vs
28.2 GB conventional phys_footprint). See `progress/phase-05-report.md`.

### Goal

Prove that streaming changes storage behavior, not model behavior.

### Work

Compare streamed execution against an established reference.

Where practical validate:

- router decisions;
- selected experts;
- routing weights;
- intermediate outputs;
- logits;
- generated tokens.

### Acceptance

Reference and streamed execution agree within documented numerical
tolerances appropriate to the existing quantization.

### Report

    progress/phase-05-report.md

---

## Phase 6 — Repacked-Expert Cache

### Status (2026-08-14)

**PARTIAL** — correctness fully closed, performance not met. The cache
(correctness, budget, eviction, hit-rate validation vs the Phase 3 sim: all
PASS; oracle bit-identical on the final binary) does not beat the measured
uncached intercept (~2.6 tok/s): the hit path's ~890 MB/step placement copy
costs ~300–480 ms/step — as much as the I/O it replaces — and large budgets
add memory pressure on 24 GB. Measured answer to the roadmap's flagged
question: the per-step copy matters; hits must feed `mul_mat_id` directly
from the cached buffer (zero-copy aliasing) for the cache to win — the
deferred advanced optimization per the STOP point. See
`progress/phase-06-report.md`. The projection floor (1.42→2.12) is stale:
the uncached streamer already exceeds it (measured pread ~80–150 ms/step,
not the 417 ms the floor assumed); the honest bar is cached-vs-uncached
on the same binary.

### Goal

Make the streamer fast, now that correctness and residency are closed by
Phases 4/4D/4E/5. Phase 6 solves performance only: the miss path costs ~417 ms
SSD read + ~242 ms repack per step (Phase 4 4B decomposition), so the cache
stores the **expensive artifact `mul_mat_id` actually wants** — the
backend-ready repacked expert buffer.

Cache value:

    (layer, expert_id, tensor-kind) → backend-ready repacked expert buffer

A hit elides both the SSD pread and the repack transform. Cache granularity
and accounting are per expert (gate/up/down move and evict together).

### Work

Add a bounded, byte-configurable cache between routing and expert storage.

Begin with a simple replacement policy such as LRU unless measurements
justify something else.

Conceptually:

    router
      ↓
    selected experts
      ↓
    expert cache (repacked, backend-ready)
      ├── HIT  → no pread, no repack
      └── MISS → pread + repack + insert
      ↓
    ggml / Metal

Note: the repack buft exposes no `get_tensor`, so cached values are the
repacked byte blobs the streamer already stages before `set_tensor`; a hit
skips pread+repack and goes straight to placement. Whether a hit can feed
`mul_mat_id` directly from the cached buffer (no per-step copy) is a
performance question Phase 6 must answer with measurements, not assumptions.

### Acceptance

The cache:

- respects its configured memory limit (bytes);
- handles hits and misses correctly;
- evicts safely;
- preserves bit-identical execution against the conventional oracle;
- measures hit-path cost (pread/repack removed) and the resulting tok/s
  ladder — Phase 6 must beat the Phase 4 projection floor
  (1.42 → 2.12 tok/s over the 1–14.6 GB ladder).

### Report

    progress/phase-06-report.md

---

## Phase 6B — Zero-Copy Expert Cache (selected from Phase 6 measurements)

### Status (2026-08-14)

**PASS** — zero-copy hits measured at 0 placement bytes, oracle still
bit-identical (max|Δ| = 0, 33,648/33,648 retrieval ranges byte-exact), and
the cache now beats the uncached intercept across the practical budget
range (1–8 GB: 1.3–2.24× in-session; peak 3.30 tok/s at 4 GB vs 1.47
uncached) — Phase 6 could not do this. The big rungs (10–14.6 GB) fall
below uncached because the persistent cache's own footprint pushes the
24 GB machine into memory-pressure compression (route+expert compute slow
~10× at 14.6 GB; the placement path is not the problem there). See
`progress/phase-06b-report.md`. Phase 6 is now effectively closed by 6B:
the original caching thesis produces a real throughput win, bounded to
≤ ~8 GB of cache on this hardware.

### Goal

Determine whether `mul_mat_id` can consume repacked expert slices
directly from persistent cache-backed storage, eliminating the per-step
placement copy while preserving bit-identical output and bounded cache
residency. If it works, rerun the ladder.

### Feasibility (verified against llama.cpp `8b43eac45` + Phase 6)

- The CPU repack kernel (`ggml/src/ggml-cpu/repack.cpp`
  `forward_mul_mat_id`) is ids-driven: unused slots are skipped
  (`cne1 == 0 → continue`), and each output element is one independent
  `vec_dot` over the slice selected by the id value. Slot index values
  never enter the numerics.
- The mul_mat_id output's expert dimension is the ids dimension
  (`n_used` = `ids->ne[0]`), not src0's slot capacity; downstream MoE
  reduction touches only occurrence rows. Slots beyond the ids are
  never read.
- Consequence: a persistent loaded tensor `[ne0, ne1, S_layer]` whose
  slot regions hold the correct experts' repacked bytes computes
  bit-identically to the compact per-step layout.

### Design (summary)

The cache and the working tensors become the same object: persistent
per-(layer, kind) loaded tensors in the repack buft, allocated once;
slot regions ARE the cache entries. Hit → nothing to place (build
slot_ids + sync only). Miss → pread + single-slice repack into the
slot (~1.3–1.9 MB). Eviction is pure bookkeeping; the Phase 6 free-pool
machinery disappears. Budget = the allocation, exact. `S_layer ≥ 9`
(max measured per-step unique experts/layer) so every decode step fits;
prefill steps exceeding capacity fall back to the legacy placement path.
Gated by `KIMI_EXPERT_CACHE_MODE=zerocopy` (default `placement` = Phase 6
behavior). CPU-only, as Phase 6.

### Acceptance

- Zero-copy hits measured at 0 MB/step placement with the oracle still
  bit-identical (Phase 5 comparator, max|Δ| = 0).
- Ladder (1–14.6 GB + uncached, ctx 4096, min-of-2, reversed order):
  cached ≥ uncached intercept on the same binary where hit-rate savings
  exceed the memory-pressure tax — the re-baselined acceptance from the
  Phase 6 report (the stale 1.42 → 2.12 projection floor is not the
  bar).
- Measured hit rate matches per-layer-LRU sim predictions within the
  Phase 6 tolerances.

### Report

    progress/phase-06b-report.md

---

## Phase 7 — Instrumentation

### Status (2026-08-14)

**PASS** — see `progress/phase-07-report.md`. Instrumentation and
observability phase on the frozen Phase 6B baseline (llama.cpp
`eca7742b8`, project `51fff51`); no repack fixes, no cache-policy
changes, no Metal work. Key additions: (1) hit-class decomposition —
zero-copy hits (0 bytes moved) vs placement hits (memcpy only) vs
misses (pread + repack + placement) are counted separately, never
collapsed into a single hit rate; (2) repack/placement byte+time split
(`copy_us = repack_us + placement_us`, both byte-counted); (3) per-layer
cache observability (`cache_layers.csv`: cap slots, live entries,
per-kind bytes per MoE layer); (4) measured build time vs the legacy
residual (`build_us_measured`, `other_us`); (5) stats.csv extended 20 →
30 columns (appended; all Phase 4–6B tooling stays valid). Validation:
oracle bit-identical with full instrumentation (33,648/33,648 retrieval
ranges, 1,512 router rows, max|Δ| = 0; 78/78 selfchecks); ladder hit
rates EXACTLY reproduce Phase 6B (0.305/0.585/0.756 at 1/4/8 GB) and
track the per-layer-LRU sim to the same 1–4 pp tolerance; 4 GB remains
the peak (4.73 tok/s vs 2.52 in-session uncached, 1.88×); instrumentation
overhead quantified at ~0–3% (uncached A/B −0.6%; 10-run alternating
drift fit +3.0%; direct attribution < 0.5%), i.e. not distinguishable
from the fanless Air's ~17% thermal drift. Phase 6B baselines (4 GB
sweet spot, ≥10 GB memory-pressure degradation, ~1.1–1.6 ms per-slice
miss repack) are preserved as baselines, not touched.

### Goal

Measure the system rather than infer its behavior.

### Work

Instrument enough of the runtime to characterize:

- resident and peak memory;
- expert-cache behavior;
- backing-storage traffic;
- prompt processing;
- decode throughput;
- latency.

Store benchmark results in machine-readable form.

### Acceptance

Benchmark output can explain the relationship between:

    memory budget
         ↕
    expert locality
         ↕
      SSD I/O
         ↕
    inference speed

### Report

    progress/phase-07-report.md

### Next Phase

Phase 7B (see below) supersedes this ladder as the authoritative
performance baseline. Phase 8 consumes the Phase 7B summaries directly:
`benchmarks/results/phase-07b/baseline/baseline-summary.csv` (plus
`baseline-runs.csv`, `baseline-ratios.csv`).

---

## Phase 7B — Controlled Performance Baseline

### Status (2026-08-14)

**PASS** — see `progress/phase-07b-report.md`. Re-measured the Phase 7
performance ladder under a controlled protocol (3 reps/rung, interleaved
uncached controls, rotated cap order, 15 s settle, ambient-load
sampling) to separate structural results from thermal noise. 24 runs,
24/24 invariant PASS, zero dispersion on deterministic columns, Phase
6B hit rates reproduced bit-identically (0.305/0.461/0.585/0.682/
0.756/0.810/0.877). Findings: (1) every cache rung beats its in-session
uncached control (1.38–2.06×); (2) 4 GB is the median peak (4.913 tok/s)
but 4–10 GB is a statistically flat plateau (4.36–4.91); (3) SSD
traffic monotonic 850 → 106 MB/token; (4) the Phase 6B "≥10 GB
throughput cliff" does NOT reproduce as a collapse — 10/12 GB stay above
uncached — only the component-level compute degradation reproduces
(expert 15.5 → 91.2 ms at 12 GB). Absolute tok/s remain cross-session
incomparable (thermal); use in-session controls. Phase 7B is the
authoritative pre-Phase-8 performance baseline.

### Protocol

    tools/phase07b_run_baseline.sh OUTDIR_ROOT [CAPS_GB...] [--reps N]
    python3 tools/phase07b_summarize.py --root OUTDIR_ROOT

---

## Phase 8 — Memory Ladder

### Status (2026-08-14)

**PASS** — see `progress/phase-08-report.md` and
`benchmarks/STREAMING_RESULTS.md`. Three workloads (ref, coding,
reasoning) through the full 1–12 GB ladder under the Phase 7B
controlled protocol (72 runs, 72/72 invariant clean, zero dispersion
on deterministic columns). Every cache rung beats its in-session
uncached control (1.31–2.11×). Peak is workload-dependent: ref/coding
peak at 4 GB (4.91/5.39 tok/s), reasoning at 8 GB (4.96 tok/s);
practical range is a 4–8 GB plateau (4.5–5.4 tok/s). SSD traffic
monotonic 850 → 106–127 MB/token; residency = baseline + cache budget
(5.5–5.6 GB at the recommended 4 GB). ≥10 GB memory-pressure compute
inflation reproduces in all workloads but does not collapse throughput
below uncached. **Verdict: expert streaming is practically useful on
the 24 GB target**; recommended operating point 4 GB (code) / 8 GB
(reasoning).

### Goal

Determine whether expert streaming is practically useful on the target
24GB Apple Silicon Mac.

### Work

Start from the authoritative Phase 7B baseline
(`benchmarks/results/phase-07b/baseline/baseline-summary.csv`, protocol
`tools/phase07b_run_baseline.sh`) — its cache/residency/hit-rate/tok/s/
SSD-traffic columns map 1:1 onto the acceptance table below. Extend it
with realistic coding workloads (the 7B ladder used only the 64-token
ref prompt). Compare every budget against an in-session uncached
control; cross-session absolute tok/s are not comparable (thermal).

Run identical workloads across multiple expert-cache budgets.

At minimum test:

    1GB
    2GB
    4GB
    6GB
    8GB
    12GB

Include realistic coding workloads.

Measure at minimum:

| Cache | Resident RAM | Hit Rate | Decode tok/s | SSD Traffic |
|---:|---:|---:|---:|---:|
| 1GB | | | | |
| 2GB | | | | |
| 4GB | | | | |
| 6GB | | | | |
| 8GB | | | | |
| 12GB | | | | |

### Acceptance

We can answer:

1. What is the minimum practical resident footprint?
2. What cache size provides useful locality?
3. How much SSD traffic occurs per generated token?
4. What throughput is achieved at each memory budget?
5. Is the system usable for interactive coding?
6. What limits performance?

### Deliverables

    benchmarks/STREAMING_RESULTS.md
    progress/phase-08-report.md

---

# STOP POINT

Stop after Phase 8.

Do not optimize further merely because optimization opportunities exist.

Review the measurements and determine whether the architecture warrants
additional engineering.

Post-Phase-8 continuation is explicitly selected work, not automatic:

- Phase 9G (benchmark methodology) PASSED 2026-08-29 and froze the
  measurement protocol;
- Phase 10 packages the frozen scientific baseline for external
  reproduction — it does not change the experimental result;
- Phase 11 validates which findings generalize to other sparse-MoE
  architectures under the frozen 9G methodology;
- the K-track (K1 MXFP4, K2 native runtime, …) is the only authorized
  optimization work, each phase gated on measured evidence and
  evaluated under the frozen 9G protocol.

Archived-baseline comparisons are NOT valid optimization gates.

## Phase 9 Selection Gate (2026-08-27)

Before any Phase 9 work, the selection gate ran a trace replay of the
Phase 8 artifacts (routing traces from moe.csv through per-layer slot
caches, LRU vs Belady-OPT, validated 100% against observed stats).

Result: **GO for a scoped Phase 9** — an offline oracle cuts decode SSD
traffic 40% at 4 GB (286.4 → 171.1 MB/token, coding) and 35% at 8 GB
(180.5 → 118.1). Recommended scope, in order: (1) replacement
policy/pinning + prefetch/prefill warm-up (fix the prefill cache bypass);
(2) async expert loading (I/O-compute overlap); (3) coalesced reads +
storage layout only if (1)+(2) measurements warrant.

Details: `progress/phase-09-selection-gate.md`,
`benchmarks/results/phase-09-selection-gate.json`,
`tools/phase09_selection_gate.py`.

---

## Parallel Track — SERVICE-ROADMAP.md

Productionization of this runtime for persistent local-agent inference
is a separate, parallel engineering track, maintained in
`SERVICE-ROADMAP.md` — not merged into this file.

- Service work (launchd/service lifecycle, OpenClaw integration,
  KV-session persistence, cold-start behavior, agent-latency
  decomposition) belongs to `SERVICE-ROADMAP.md`, NOT to Phase 9+ of
  this file.
- Phase 9+ in this file remains inference-runtime work only: Phase 10
  (reproducible release of the frozen baseline), Phase 11
  (cross-architecture validation), and the K-track (MXFP4, native
  kernels, storage/repack pipeline). All optimization is evaluated
  under the frozen 9G protocol.
- Inference optimization work (repack batching, prefetching,
  asynchronous I/O, cache-policy changes, new kernels) belongs here,
  NOT in `SERVICE-ROADMAP.md`.

The two tracks converge here:

    OpenClaw → persistent llama-server/session → optimized streamed inference runtime

---

## Potential Phase 9 — Streaming Optimization

**PAUSED (2026-08-28); superseded (2026-08-29).** Optimization subphases
9A–9F are complete; the 9E end-to-end prediction was falsified by
cross-session baseline instability (see `progress/phase-09f-report.md`),
and 9G PASSED on 2026-08-29, freezing the paired bracketed protocol.
9A–9F remain closed. No further streaming-optimization subphase will be
started under the old numbering; future optimization proceeds through
the K-track (K1, K2, …) under the frozen 9G protocol. Archived-baseline
comparisons are NOT valid optimization gates.

Proceed only if Phase 8 demonstrates that streaming is viable.

Optimize measured bottlenecks.

Potential work includes:

- asynchronous expert loading;
- batched reads;
- prefetching;
- cache pinning;
- improved replacement policies;
- I/O/computation overlap;
- storage-layout optimization;
- mmap/filesystem-cache optimization.

The benchmark results determine which of these, if any, should be built.

---

## Phase 9G — Benchmark Methodology (2026-08-28)

### Goal

Make measurements of how much faster one configuration is than another
trustworthy and reproducible, after Phase 9F demonstrated that archived
cross-session baselines are unstable enough to falsify a correct mechanism.

### Work

- Quantify variance components (within-run, run-to-run, drift,
  session-to-session) of the frozen control and candidate paths.
- Validate a paired bracketed protocol A→B→A (experimental unit: one
  bracket; quantity: paired speedup S_i = B_i / mean(A_before,i,
  A_after,i); report the distribution of S_i, not a point comparison).
- Validate against a null control (A→A→A) and a positive control (W=4
  pipelined vs W=1 frozen, same-session).
- Determine how many brackets are needed to distinguish 5%, 10%, 20%
  effects (paired test, α=0.05, β=0.20) from measured variance.
- Freeze the protocol (harness, analysis tooling, environment capture)
  for all subsequent phases; ban archived-baseline optimization gates.

### Design / status

`progress/phase-09g-design.md`; seed variance analysis in
`benchmarks/results/phase-09g/variance-9d-9f.json`.

Status (2026-08-29): **PASS** — full experiment complete (sessions 1–3,
30 null + 30 positive brackets, coding-cap4, seeds 1787983806 /
1788018038 / 1788029761). Null pooled median S = 1.002 (median log S
+0.0018, empirical FPR 2.7% vs nominal 5%); positive control (W4 vs W1)
pooled median S = 1.180 with all session CIs excluding the 1.10 gate;
power table from measured sd(log S) = 0.0559: 11 / 3 / 1 brackets for
5 / 10 / 20% effects. Harness frozen at `c40c008`; protocol documented
in `TOOLS.md`. Full results: `progress/phase-09g-report.md`.

### Report

    progress/phase-09g-report.md

---

## Phase 10 — Reproducible Release

### Goal

Turn the frozen Kimi Linear storage-backed inference implementation into
a reproducible public artifact that another technically competent user
can install, run, benchmark, and validate without knowledge of the
project's development history.

Phase 9 remains the frozen scientific baseline. This phase packages that
baseline for external use; it does not change the experimental result.

### Build

Provide a documented build process from a clean checkout.

Prefer:

1. standard llama.cpp build mechanisms;
2. minimal required patches or maintained fork;
3. automated dependency detection where practical.

A clean machine should not require undocumented manual source changes.

### Model Setup

Document:

- supported Kimi Linear model/version;
- supported GGUF quantization;
- model acquisition;
- expected file size/checksum where appropriate;
- required context/cache configuration.

Do not redistribute model weights unless permitted.

### Runtime

Provide a simple supported launch path.

The user should not need to understand the internal experimental
architecture to start the server.

Expose important configuration explicitly, including:

- expert-cache size;
- expert-read worker count;
- context size;
- storage/model path;
- relevant performance options.

Defaults should represent the validated Phase 9 configuration where
appropriate.

### Validation

Provide a short reproducibility test that verifies:

- model loads successfully;
- storage-backed expert execution is active;
- routing/retrieval invariants hold;
- generated output is valid;
- expected instrumentation is produced.

Provide a separate benchmark/reproduction path for users who want to
replicate the Phase 9 performance results.

Installation success and scientific reproduction should not require
running the entire historical Phase 1–9 workflow.

### Documentation

README should contain a clean path:

    clone
    build/install
    obtain model
    run
    verify
    benchmark

Document:

- supported hardware/OS;
- expected RAM and disk requirements;
- known limitations;
- expected approximate performance;
- configuration knobs;
- troubleshooting.

### External Reproduction Gate

Before declaring Phase 10 complete, perform at least one clean-room
installation from the public instructions.

Preferably, obtain an independent reproduction from another user or
machine without providing undocumented intervention.

Record failures as reproducibility findings and fix the installation
process rather than coaching around them.

### Report

    progress/phase-10-report.md

---

## Phase 11 — Cross-Architecture Validation

**DEFERRED (2026-08-31).** The active Phase 11 work is the Native
Runtime Optimization kickoff selected by the frozen K1 result (see the
Phase 11 (Active) section below). The cross-architecture plan in this
section is unchanged and remains a valid future phase; it resumes when
the native-runtime track's first experiments conclude or the owner
reprioritizes.

Proceed only after Phase 10 establishes a reproducible installation,
runtime, and benchmark path.

### Goal

Determine which findings from the Kimi Linear storage-backed inference
work generalize to other sparse-MoE architectures and which are
model-specific.

This phase is primarily a validation and characterization phase, not an
optimization phase.

Select at least one substantially different sparse-MoE architecture that
can use the storage-backed execution framework. Prefer a model that
differs meaningfully from Kimi Linear in expert count, routing behavior,
expert size, or architecture.

Do not require the second model to reproduce Kimi's absolute performance.
The objective is to compare the underlying behavior.

### Evaluate

Using the Phase 9/9G methodology where applicable, measure:

- routed-expert working set;
- expert activation distribution;
- expert reuse-distance distribution;
- compulsory versus reload traffic;
- LRU expert-cache behavior;
- Belady-OPT bound;
- fraction of the LRU-to-OPT gap recoverable by practical online
  policies;
- storage traffic per token;
- cache-capacity sensitivity;
- bounded expert-read parallelism;
- stability of parallel-I/O compression;
- read/repack overlap opportunities;
- resident memory and storage requirements;
- inference throughput;
- correctness and exactness under storage-backed execution.

Where the second architecture requires implementation changes, separate
changes required for architectural support from changes intended to
improve performance.

Avoid model-specific optimization until the baseline characterization is
complete.

### Comparison

Compare the second architecture directly with the frozen Kimi Linear
results.

For each major Phase 8/9 finding, classify it as:

- reproduced;
- directionally reproduced;
- architecture-dependent;
- not reproduced; or
- not applicable.

Particular attention should be paid to:

1. whether expert routing exhibits enough temporal locality for caching;
2. whether a large LRU-to-Belady gap exists;
3. whether that theoretical gap is recoverable by causal online policies;
4. whether storage traffic is dominated by compulsory reads or reloads;
5. whether bounded parallel reads materially reduce exposed I/O latency;
6. where the bottleneck moves after I/O parallelism;
7. whether the Phase 9 pipelining opportunity appears on the second
   architecture.

Do not assume that an optimization effective on Kimi Linear should be
effective on another model.

### Experimental Method

Performance claims must use contemporaneous controls under the frozen
Phase 9G benchmark methodology.

Archived-baseline comparisons may be reported descriptively but may not
be used as optimization gates.

Preserve routing traces and other artifacts needed for offline cache and
locality analysis.

### Independent Reproduction

Use the Phase 10 public release to solicit independent reproduction where
practical.

External validation may include:

- reproduction of Kimi Linear storage-backed execution;
- reproduction of Phase 9 performance findings on different hardware;
- execution of the second architecture using the published framework.

Independent reproduction strengthens the result but is not required to
begin or complete the cross-architecture experiments performed by this
project.

Record hardware, operating system, storage, memory, model, quantization,
configuration, and software revision for all external results.

### Exit Question

Can the principal findings from Kimi Linear be stated as properties of
storage-backed sparse-MoE inference more generally, or must they remain
claims about Kimi Linear and the tested hardware?

The answer may be mixed. Architecture-dependent results are valid
findings and should not be treated as failures.

### Exit Criteria

Phase 11 is complete when:

1. at least one substantially different sparse-MoE architecture executes
   correctly through the storage-backed framework;
2. the major Phase 8/9 measurements have been reproduced where
   applicable;
3. Kimi and the second architecture have been compared under a common
   analysis framework;
4. similarities and differences in routing, caching, storage traffic,
   and I/O behavior are documented;
5. the evidence is sufficient to delimit which research claims
   generalize beyond Kimi Linear.

### Report

    progress/phase-11-report.md

---

## Kimi Optimization K1 — MXFP4

### Goal

Determine whether MXFP4 can improve the practical memory/performance
frontier without unacceptable quality loss.

Evaluate:

- model storage;
- resident memory;
- expert-cache density;
- SSD traffic per token;
- model quality;
- inference throughput;
- MXFP4 decode/conversion overhead;
- direct MXFP4 computation where supported.

Particular attention should be paid to whether increased expert-cache
density reduces reload traffic enough to materially change the storage
bottleneck observed in Phase 8/9.

Prefer existing ggml/Metal functionality where available.

Implement new kernels only when required and justified by measurement.

### Promotion

Promote to the Kimi production baseline only if the measured practical
benefit justifies the quality and implementation costs.

### Report

    progress/kimi-k1-mxfp4-report.md

---

## Kimi Optimization K2 — Native Runtime Optimization

### Goal

Implement lower-level specialized functionality only where profiling
demonstrates that the existing llama.cpp / ggml / Metal path cannot
efficiently support the required behavior.

Potential work includes:

- specialized ggml operations;
- custom Metal kernels;
- native MXFP4 operations;
- direct I/O;
- specialized memory management;
- storage/repack pipeline improvements.

Do not build a new inference engine unless evidence demonstrates that
the existing runtime architecture fundamentally prevents the desired
result.

Each intervention should target a measured bottleneck and be evaluated
against the current production baseline using the Phase 9G protocol.

### Report

    progress/kimi-k2-native-runtime-report.md

With the possibility of K3, etc. if future investigation warrants.

---

## Phase 11 (Active) — Native Runtime Optimization — K1 Kickoff (2026-08-31)

Selected by the frozen K1 result (`progress/kimi-k1-mxfp4-report.md`,
PASS): MXFP4 delivers +48% (cached) / +87% (uncached) decode with
+1.37% PPL cost. All accepted K1 measurements are CPU-only; Metal +
expert streaming is NOT validated, and the per-step timers (retained
K1 stats.csv) show the MXFP4 advantage is concentrated in the
repack/placement stage (storage→compute layout conversion), not in
expert matmul compute. Full analysis: `progress/phase-11-kickoff.md`.

Active Phase 11 scope (immediate native-runtime work under the K2
umbrella):

1. Fix the streamed-expert Metal boundary. It currently aborts at
   `ggml_metal_cpy_tensor_async` / `GGML_ASSERT(buf_dst)`
   (ggml-metal-context.m:359): a NULL destination buffer, because
   streamed expert tensors are staged in CPU buffers and never
   allocated on the Metal backend.
2. Attack the measured repack bottleneck: 239 ms of the 412 ms Q4_K_M
   decode step (58%); MXFP4's repack is 74–87% cheaper and is the
   primary reason MXFP4 decodes faster. A native/storage-side repack
   primitive is the highest-value target.
3. Preserve all K1 CPU gains. Every intervention is evaluated under
   the frozen 9G protocol against the frozen K1 baseline; acceptance
   criteria and the K1 artifacts are not modified.

First bounded experiment: E1 — Metal staging for streamed experts
(scope, gates, and rollback in the kickoff document). No code changes
before E1.

**E1 result (PASS, 2026-08-31):** streamed MXFP4 now executes on Metal
end-to-end with the env-gated fixes (`KIMI_STREAM_METAL_STAGE=1`, default
off = frozen path): 64-token smoke EXIT=0, phase07 invariants PASS
(0 violations), 708.7 t/s prompt / 24.3 t/s generation, ~10.9 GB peak
RSS (bounded streamed expert memory — no full-offload OOM). The abort
was NOT expert staging (loaded experts were already Metal-allocated,
MTL0); the measured blockers were (a) a 0-size routing-ids readback on
the last MoE layer during prefill (`ffn_moe_topk-26 (copy)`,
`GGML_ASSERT(buf_dst)`), and (b) a teardown leak of the persistent
`stream_persist_` Metal buffers (`GGML_ASSERT([rsets->data count] == 0)`).
Known limitations: Metal generated text is garbled vs coherent CPU text
(quality gate for E4), and act/moe trace capture (`KIMI_TRACE_ACT`/
`KIMI_TRACE_MOE`) still aborts at load on Metal (E1b). Report:
`progress/phase-11-e1-report.md`; fork commit `6c1895bff`.

**E1b result (PASS, 2026-08-31):** trace observability restored on the
streamed Metal path. The trace SIGTRAP was host-side heap corruption
(EXC_BREAKPOINT in malloc freelist), not a Metal assert: the trace
dumps issued `ggml_backend_tensor_get_async` into short-lived host
vectors and read/freed them before the Metal async blit completed
(CPU falls back to a synchronous copy, so CPU never crashed — an
invalid CPU readback assumption). Fixed with
`ggml_backend_synchronize(backend)` after each async readback in both
dump loops, plus a 0-size topk guard — env-gated by the trace envs
themselves, default path byte-identical (CPU trace output verified
identical to the pre-fix run). Acceptance: Metal trace run EXIT=0,
act.bin (191 MB) + moe.csv (7,667 rows) produced, phase07 invariants
PASS, tracing-off E1 Metal run still clean, CPU trace unchanged.
First bounded CPU-vs-Metal localization: activations diverge from the
very first layers (layer-0 l_out max|d| ≈ 1.7e-3, layer-1 attn_out ≈
1.2e-3) and grow monotonically with depth (O(1) by layer 18+; overall
max|d| 89.2); routing order flips from prefill layer 8 and expert SET
differences appear from layer 11 (2,494/7,666 set-divergent rows).
Interpretation: small per-op numeric differences (accumulation order /
MXFP4 dequant on Metal vs CPU) amplified through 26 MoE layers and the
routing argmax — NOT a single broken op. The numeric divergence is NOT
fixed here (cause not trivial/unambiguous); it is the E2/E4 quality
gate. Report: `progress/phase-11-e1b-report.md`; fork commit
`82335c59b`.

**E2 result (PASS, 2026-08-31):** direct placement on the streamed
Metal miss path (`KIMI_STREAM_E2_DIRECT_PLACE`, default off, requires
the E1 gate). On Metal the packed MXFP4 slice is already the compute
layout (no CPU-style repack buft exists), so the temp-tensor round
trip (tensor_set + memcpy) is redundant — E2 memcpys packed bytes
directly into the loaded slot / persistent zc slot. The phase07
B-arm failure this exposed was an instrumentation-contract violation,
not a computational failure: the frozen invariant
`repack_bytes == pread_bytes` assumes every miss byte is repacked, and
the direct path performs no physical repack. Fixed with compatibility
accounting (repack_bytes counted logically on the direct path;
invariant and acceptance criteria UNCHANGED); repack_us stays 0 on the
direct path and is the honest physical-repack evidence. Measured
(64-tok A/B, same binary): repack_us −89% (256 MiB) / −93% (4096 MiB)
on the prefill miss path, copy_us −33% on the 4096 MiB config; decode
steady-state flat within noise (0 misses there — the zc cache already
absorbed the cost; residual decode cost is compute ≈73%, E3
territory). All 6 configs (A/B × 0/256/4096) EXIT=0 and phase07
invariants PASS; E1 baseline unchanged (A path byte-identical by
construction). Metal output quality remains the open E4 gate. Report:
`progress/phase-11-e2-report.md`; fork commit (E2 + accounting fix)
recorded in the fork log. STOPPED for review per directive — E2 not
expanded; instrumentation contract unchanged.

**E4 result (EXECUTED — Metal NOT quality-qualified, 2026-08-31):**
perplexity quality gate on the frozen E2 Metal path, K1 methodology
(wikitext-2-raw, 32 × 512-token chunks, ctx=512, zerocopy cache 4096
MiB, streamed naive path, same binary). Fresh CPU MXFP4 reference
re-run: **PPL 6.7596 ± 0.19264** — exact reproduction of the accepted
K1 reference. Metal candidate (frozen E2 path): **PPL 1,477,252.66 ±
42,507.67** — relative degradation **×218,541**, every one of 32 chunks
> ×10^5 (min ×155,291), no NaN/Inf/asserts, no clean chunks, no
outliers: systematic, not isolated. This quantifies the E1b numeric
divergence (drift from layer 1 amplified through 26 MoE layers +
routing argmax). Per the E4 decision rule: **stop before E3** and
investigate Metal numerical quality/correctness. No source changes
(frozen fork `32c02b145`); no kernels touched, no repair attempted.
Driver retained: `tools/phase11_e4_perplexity.sh` (cpu|metal).
Report: `progress/phase-11-e4-report.md`. E3 is NOT started.

**Metal numerical localization — first bounded experiment (PASS,
2026-08-31):** the first causally meaningful CPU-vs-Metal divergence is
localized. Env-gated trace captures at every KDA attention boundary
feeding `attn_out` (attn_norm, Q/K/V projection mul_mat outputs,
post-conv, g1/beta, l2-norm, delta-net, gating, wo). Layer-0 first
prefill step: `l_in` and `attn_norm` are BIT-IDENTICAL CPU vs Metal;
the first non-zero divergence is the layer-0 **Q/K/V projection
`mul_mat` (MXFP4 weights)** — max|d| ≈ 1e-2, rel ≈ 1.4–2.2e-2,
~2,700–3,500× above the f32 accumulation-order noise floor; errors
scattered per-element (not a uniform scale/layout bug); everything
downstream just propagates the seed (layer-1 `l_in` already carries it
at 1.06e-3). Classification: **identical inputs + different CPU/Metal
operation result** (op-level), the directive's case 1. Large/structured
enough to explain the E4 catastrophe (1e-2 seed through 26 MXFP4 layers
+ routing argmax). Candidate by evidence: MXFP4 dequant/accumulation in
the Metal mul_mat path — NOT assumed; next step is a controlled
substitution (pin projection mul_mat to CPU on the Metal run) to
discriminate dequant vs accumulation before any kernel change. No
kernels modified; diagnostic captures only (env-gated, default path
byte-identical). Report:
`progress/phase-11-localize-report.md`. STOPPED for review; E3 not
begun.

**Controlled CPU substitution (PASS, 2026-08-31):** the layer-0 Q/K/V
projection `mul_mat` is causally confirmed as a divergence source, and
the defect is shown to affect MXFP4 `mul_mat` generally. On the frozen
Metal path with ONLY the layer-0 Q/K/V projection routed through CPU
(`KIMI_STREAM_LOCALIZE_SUB_QKV_CPU`, default off; pin re-applied after
`ggml_backend_sched_reset`, which otherwise wipes user backend
assignments), that op's branch collapses to CPU agreement: kda_Q/K/V
proj max|d| ~1e-2 → ~5–7e-7, post-conv/l2-norm → ~1e-7–1e-8. The other
MXFP4 matmuls in the same layer (g1 f_a/f_b, ssm_beta, gate g_a/g_b)
still diverge at their original magnitudes (1.438 / 1.37e-3 / 6.75e-3)
— not substituted, unchanged. Layer-0 attn_out improves ~26%
(1.11e-4 → 8.2e-5); layer-1+ essentially unchanged (seed re-injected
by every other MXFP4 matmul). Classification: defect is general to
Metal MXFP4 `mul_mat`, not specific to this projection; dequant vs
accumulation NOT concluded (next experiment: compare dequantized values
from the same MXFP4 blocks/scales). No kernels modified; substitution
env-gated and rollback-able. Report:
`progress/phase-11-substitute-report.md`. STOPPED for review; E3 not
begun.

**Dequantization isolation (PASS, 2026-08-31):** the defect lies AFTER
dequantization. Standalone probe (real Metal shader execution vs exact
ggml CPU dequant on identical raw blocks) shows CPU and Metal
reconstruct the SAME values: q8_0 (the ACTUAL localized type) is
bit-identical on all non-NaN values; mxfp4 is bit-identical except
subnormal flush-to-zero on Metal at ~3.5e-38 (negligible, not the error
class). PREMISE CORRECTION (verified from the GGUF): the layer-0
projection tensors localized as the first divergence are q8_0, not
MXFP4 — only MoE experts (and MLA K/V bias) are mxfp4; the substitution
conclusion is corrected from "Metal MXFP4 mul_mat" to "Metal quantized
mul_mat (q8_0 dense trunk first)". Representation decoding (q8_0 and
mxfp4) provisionally cleared per the directive's decision tree; next
experiment isolates the mul_mat arithmetic (partial products/
accumulations with identical inputs). No kernels modified; fork
untouched. Report: `progress/phase-11-dequant-report.md`;
`tools/phase11_dequant_probe.m`. STOPPED for review; E3 not begun.

---

# Progress Tracking

Every phase must end with:

    progress/phase-XX-report.md

A phase is not complete until its report has been written.

Each report records:

- status;
- work completed;
- files changed;
- tests performed;
- measurements;
- failures and limitations;
- decisions;
- unresolved questions;
- reproduction commands;
- information required by the next phase.

The roadmap records the plan.

`progress/` records reality.

Later phases must use the progress reports rather than assume earlier
roadmap expectations proved correct.

---

# Definition of Success

The experiment succeeds if `Kimi-Linear-48B-A3B-Instruct` can:

- execute correctly using SSD-backed routed experts;
- preserve reference model behavior;
- operate within a bounded resident-memory footprint;
- keep the resident non-expert footprint as small as measured reality
  allows, and convert the remaining budget into expert cache and
  operating headroom to maximize throughput;
- maintain throughput and latency sufficient for productive coding.

The central question is:

> Can kimi-k3-in-c-inspired expert paging make Kimi Linear 48B-A3B a practical
> local coding model on a 24GB Apple Silicon Mac without requiring the
> complete model to reside in unified memory?
