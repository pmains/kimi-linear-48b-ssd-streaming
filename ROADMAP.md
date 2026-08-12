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
7. Target approximately 8GB or less of model-related resident memory.
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

### Acceptance

We can answer:

1. How much of the model consists of routed experts?
2. How large is the unavoidable resident component?
3. How large are individual experts?
4. What memory remains available for an expert cache?
5. Is an approximately 8GB model-related resident target feasible?

### Report

    progress/phase-02-report.md

---

## Phase 3 — Expert Addressability

### Goal

Make individual routed experts independently addressable from backing
storage.

### Work

Use the existing runtime/model format where practical.

Create only the additional indexing or storage machinery required to map:

    (layer, expert_id, tensor)
              ↓
        backing storage

Do not duplicate GGUF functionality unnecessarily.

Do not change quantization.

Do not change model mathematics.

### Acceptance

An arbitrary routed expert can be located and retrieved without requiring
the complete routed-expert collection to become resident.

### Report

    progress/phase-03-report.md

---

## Phase 4 — Uncached Expert Streaming

### Goal

Execute Kimi Linear while routed experts remain primarily on SSD.

### Work

Change the expert-weight access path so that the existing router selects
experts normally, but required expert weights are obtained from backing
storage on demand.

Conceptually:

    existing router
          ↓
    selected experts
          ↓
    expert storage layer
          ↓
      ggml / Metal

Do not optimize yet.

### Acceptance

The model generates correctly while routed experts are loaded on demand,
and resident memory is measurably lower than conventional execution.

Performance is not yet an acceptance criterion.

### Report

    progress/phase-04-report.md

---

## Phase 5 — Correctness Validation

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

## Phase 6 — Bounded Expert Cache

### Goal

Reduce backing-storage traffic while enforcing a hard memory budget.

### Work

Add a bounded expert cache between routing and expert storage.

Begin with a simple replacement policy such as LRU unless measurements
justify something else.

Cache capacity must be configurable by bytes.

Conceptually:

    router
      ↓
    selected experts
      ↓
    expert cache
      ├── HIT  → resident expert
      └── MISS → backing storage
      ↓
    ggml / Metal

### Acceptance

The cache:

- respects its configured memory limit;
- handles hits and misses correctly;
- evicts safely;
- preserves correct model execution.

### Report

    progress/phase-06-report.md

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
- approach approximately 8GB or less of model-related resident memory,
  if permitted by the measured unavoidable resident state;
- maintain throughput and latency sufficient for productive coding.

The central question is:

> Can kimi-k3-in-c-inspired expert paging make Kimi Linear 48B-A3B a practical
> local coding model on a 24GB Apple Silicon Mac without requiring the
> complete model to reside in unified memory?
