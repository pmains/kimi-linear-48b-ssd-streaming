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

---

## Phase 8 — Memory Ladder

### Goal

Determine whether expert streaming is practically useful on the target
24GB Apple Silicon Mac.

### Work

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

---

## Potential Phase 9 — Streaming Optimization

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

## Potential Phase 10 — MXFP4

Proceed only after routing and streaming work correctly.

### Goal

Determine whether MXFP4 can improve the memory/performance frontier
without unacceptable quality loss.

Evaluate:

- model storage;
- resident memory;
- expert-cache density;
- model quality;
- inference throughput;
- cost of decoding or directly computing MXFP4 weights.

Prefer existing ggml/Metal functionality where available.

Implement new kernels only when required and justified by measurement.

### Report

    progress/phase-10-report.md

---

## Potential Phase 11 — Native Runtime Optimization

The project is already expected to use a native runtime.

This phase therefore does NOT mean "rewrite the project in C."

It means implementing lower-level specialized functionality only where
profiling demonstrates that the existing llama.cpp / ggml / Metal path
cannot efficiently support the required behavior.

Potential work includes:

- specialized ggml operations;
- custom Metal kernels;
- native MXFP4 operations;
- direct I/O;
- specialized memory management.

Do not build a new inference engine unless evidence demonstrates that the
existing runtime architecture fundamentally prevents the desired result.

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
