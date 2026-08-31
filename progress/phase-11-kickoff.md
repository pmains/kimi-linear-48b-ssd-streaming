# Phase 11 Kickoff — Native Runtime Optimization (selected by K1)

## Status

KICKOFF — analysis and planning only. No code changes made. K1 remains
the frozen baseline (`progress/kimi-k1-mxfp4-report.md`); this document
does not modify K1's acceptance criteria, results, or artifacts.

## Objective

Transition from the frozen K1 result to Phase 11 native-runtime work:

1. Reconcile the existing Phase 11 / K2 plan with what K1 established.
2. Locate the measured MXFP4 advantage as precisely as the retained
   evidence permits.
3. Diagnose the Metal streaming failure as an architectural boundary
   problem.
4. Identify which parts of the CPU streamed path should become native
   runtime primitives.
5. Propose the smallest ordered set of experiments that preserves K1's
   CPU gains, enables Metal-compatible streamed experts, reduces
   repack/copy overhead, and keeps the existing correctness/quality
   gates.
6. Define the first bounded experiment (one architectural question).

## Current Evidence

### K1 (frozen, authoritative)

| result | value |
|---|---|
| cached decode (4096 MiB) | median S = 1.479, 95% CI [1.311, 1.564] (+48%) |
| uncached decode (cache=0) | median S = 1.871, 95% CI [1.468, 2.077] (+87%) |
| wikitext-2-raw PPL (32×512, streamed) | Q4_K_M 6.6681 ± 0.190 vs MXFP4 6.7596 ± 0.193 (+1.37%) |
| routing divergence (B vs A moe.csv) | 91.84% of (token, layer) rows select a different top-8 expert set; deterministic |
| generated text | byte-identical for the first 649 chars, then diverges (outputs are NOT token-identical) |
| invariants | phase07 30/30 PASS in both sessions; A-run moe.md5/retr identity preserved |
| acceptance | criteria unchanged; no experiments added; K1 frozen |

All accepted K1 performance measurements are CPU-only (`-ngl 0`,
`KIMI_STREAM_EXPERTS=naive`, workers=1, zerocopy cache mode).

### New: per-step time attribution (retained K1 stats.csv)

Decode rows after the frozen WARMUP=3, per-step medians, workers=1.

Uncached (cache=0) — one decode step:

| component (us/step) | A Q4_K_M | B MXFP4 | Δ |
|---|---|---|---|
| **repack** (storage→compute layout) | 239,446 (58.1%) | 61,964 (27.8%) | **−177,482 (94% of Δtotal)** |
| pread (I/O) | 113,390 (27.5%) | 100,747 (45.1%) | −12,643 (7%) |
| route compute (dense trunk) | 20,581 (5.0%) | 22,808 (10.2%) | +2,227 |
| expert matmul compute | 17,656 (4.3%) | 17,828 (8.0%) | **≈0** |
| build / other / sync | 23,525 | 21,290 | −2,235 |
| **total** | 411,991 | 223,292 | **−188,699** |

Cached (4096 MiB) — one decode step:

| component (us/step) | A Q4_K_M | B MXFP4 | Δ |
|---|---|---|---|
| **repack** | 76,263 (38.7%) | 9,694 (7.2%) | **−66,569** |
| placement (memcpy) | 4,574 | 4,490 | ≈0 |
| pread (I/O) | 62,436 (31.7%) | 59,216 (43.9%) | −3,220 |
| route compute | 19,660 (10.0%) | 27,020 (20.0%) | +7,360 |
| expert matmul compute | 16,395 (8.3%) | 17,556 (13.0%) | +1,161 |
| build / other / sync | 12,303 | 13,166 | +863 |
| **total** | 196,982 | 134,786 | **−62,196** |

Hit-class behavior (cached): zc_hits 138 vs 137, placement_hits 0 vs 0,
hit rate 0.663 vs 0.659, pread bytes 301 vs 267 MB/step (−12%) — the
cache behavior of the two models is effectively identical. Placement is
zero in the uncached configuration (miss path repacks in place).

### Interpretation (evidence-based, no invented mechanism)

- The MXFP4 decode advantage is **almost entirely the repack stage**:
  94% of the uncached Δtotal and more than 100% of the cached Δtotal
  (offset by slightly higher route compute, from the Q8_0 trunk) come
  from repack cost. Expert matmul compute is flat or marginally higher
  for B. Pread contributes a modest −12% (bytes) / −7% (time).
- This **refines** the frozen K1 wording ("much cheaper MXFP4 compute
  path"): at the macro level B's execution is faster (the K1 statement
  stands, frozen); at the micro level the measured mechanism is the
  repack/placement stage, which is part of the execution path but is
  NOT the expert matmul compute. MXFP4's block layout (32 elems /
  17-byte blocks, uniform packed bytes) repacks far cheaper than
  Q4_K_M's (256 elems / 144-byte blocks with scale tables).
- The repack stage is the dominant cost of the *frozen baseline*:
  239 ms of a 412 ms Q4_K_M decode step (58%). This is the largest
  single measured bottleneck in the entire streamed path.

## Architectural Diagnosis

### Current decode path (CPU, frozen)

`llama_decode` → `process_ubatch_streamed` (llama-expert-stream-exec.cpp:153,
dispatched from llama-context.cpp:1356) executes per layer:

1. route subgraph (dense trunk: attention/norm/KDA + router) on the
   compute backend;
2. routed expert ids read back to host;
3. expert byte ranges pread from the GGUF (llama_expert_streamer,
   naive/coalesced; `KIMI_EXPERT_READ_WORKERS`),
4. per-block **repack** from storage layout to compute layout into slot
   tensors (cache: zero-copy hits skip pread+repack; misses do
   pread+repack),
5. stateless expert-FNN subgraph over the loaded tensors.

All validated on the CPU backend (`-ngl 0`). The model file is mmap'd;
expert slot tensors are allocated with the buffer type of the source
model tensor (CPU) or the compute backend's default buffer type
(llama-expert-stream.cpp:403–410).

### Metal streaming failure (measured)

- Non-streamed full offload (`-ngl 999`): model 27.2 GB + KV + compute
  buffers exceed the 24 GB unified budget →
  `kIOGPUCommandBufferCallbackErrorOutOfMemory` at the first ubatch;
  `llama-cli` exits 0 having produced nothing (exit code is NOT a
  success signal).
- Streamed on Metal (`KIMI_STREAM_EXPERTS=naive`, both zerocopy and
  non-zerocopy cache): SIGABRT (exit 134) in
  `process_ubatch_streamed` → `ggml_metal_cpy_tensor_async`
  (backtrace frame 2; `ggml_metal_get_tensor_async` inlined at +0) →
  `GGML_ASSERT(buf_dst)` at ggml-metal-context.m:359 —
  `[device newBufferWithBytesNoCopy:data ...]` returned nil because the
  passed data pointer is NULL.
- Structural cause: the streamer stages loaded expert tensors in CPU
  buffers (model tensor buft) and hands them to the Metal-backed
  compute sched; the cross-backend host→Metal path for these streamed
  tensors has no Metal destination buffer allocated, so the async copy
  receives NULL and aborts. The two-phase streamed execution was built
  and validated only on CPU; nothing in the streamer allocates loaded
  expert tensors on the Metal backend or registers them with the Metal
  sched.
- The Metal *compute* side is not the blocker: the frozen tree already
  contains `kernel_mul_mv_mxfp4_f32` / `kernel_mul_mv_id_mxfp4_f32`
  and accepts GGML_TYPE_MXFP4 in the small-batch mat-mv path
  (ggml-metal-ops.cpp:2349). The boundary failure is tensor staging.

## Hypotheses Ranked by Evidence

1. **Repack cost is the decode bottleneck and the MXFP4 advantage
   source** (measured directly). Implication: eliminating or
   native-izing repack is the highest-value Phase 11 target and would
   benefit the frozen Q4_K_M baseline as much as MXFP4.
2. **Metal streaming fails at staging, not compute** (measured crash).
   Allocating slot tensors on the compute backend's buffer type (or an
   explicit host→Metal copy at the subgraph boundary) should unblock
   Metal with no change to the expert math.
3. **MXFP4-on-Metal upside is bounded by the non-compute stages**
   (indirect): expert matmul is only 4–8% of the CPU step, so GPU
   compute alone cannot explain a large gain; the repack/I/O stages
   must move to Metal or be eliminated for Metal to beat the CPU path.
4. **Route compute is slightly higher for B** (Q8_0 trunk, +2.2 ms
   uncached / +7.4 ms cached) and routing changes 91.84% of
   selections. Trunk quantization is a quality/cost knob, not a Phase
   11 target yet.
5. **Overlap of repack with pread** (`hidden_us == 0` at workers=1):
   pipelined repack exists (9F) but is serial here; overlap would hide
   at most the pread slice (~27% of the step), not the repack itself.

## Proposed Implementation Sequence

Smallest ordered set preserving K1 CPU gains, enabling Metal streamed
experts, and reducing repack/copy overhead. Each step is env-gated,
defaults to the frozen path, and is measured under the frozen 9G
protocol against the frozen K1 baseline.

- **E1 — Metal staging for streamed experts** (first bounded
  experiment; see below). Unblocks the Metal boundary.
- **E2 — Repack elimination / native repack**. Target `repack_us`
  (239 ms/step for Q4_K_M). Candidates, chosen by a quick
  measurement-driven probe: (a) storage-side pre-repacked MoE layout
  (repack once at model-open, preads then read compute-ready bytes);
  (b) NEON/SIMD repack kernel; (c) MXFP4-style block layout for the
  MoE region. Gate: repack_us collapse with invariants intact.
- **E3 — Repack/prefill overlap** (only if E2 leaves a serial
  conversion): pipeline repack under the pread phase at workers=1;
  measure `hidden_us`.
- **E4 — End-to-end MXFP4-on-Metal** (if E1+E2 pass): bounded
  memory/throughput/PPL comparison vs the frozen CPU baseline — the
  production-value question.

## Correctness / Performance Gates

Unchanged from the frozen protocol; no acceptance-criteria changes:

- paired bracketed measurement (phasek1 harness for cross-model,
  phase09g for same-model) with position-bias/warmup/drift checks;
- phase07 per-step invariants 30/30 on every run;
- A-run byte-identity (moe.md5 vs frozen baseline, retr.csv identity);
- PPL within the K1 band (≤ +1.4% vs Q4_K_M) for any quality-relevant
  change;
- K1 artifacts untouched (frozen baseline for all Phase 11
  comparisons).

## Rollback Points

- Every change is env-gated and defaults to the frozen path; the
  frozen tree (commit caea707b7) and the Phase 10 release bundle
  (runtime/release-9f) are the rollback anchor.
- E1 failure → revert to CPU-only streaming; K1 numbers are unaffected
  by construction.
- One git commit per experiment so any step can be reverted without
  touching others.

## First Bounded Experiment — E1: Metal staging for streamed experts

**Single architectural question:** Is the Metal streaming failure a
fixable tensor-staging boundary bug (streamed expert tensors never
allocated on the Metal backend), or a fundamental incompatibility of
the two-phase streamed execution with Metal?

**Scope (minimal, no new kernels):**
1. In the streamer load path (llama-expert-stream.cpp:403–410),
   allocate the loaded-expert slot tensors on the compute backend's
   buffer type when it is Metal (instead of the CPU model-tensor
   buft), or insert an explicit host→Metal copy of the repacked slot
   bytes at the subgraph boundary.
2. Env-gate with `KIMI_STREAM_METAL_STAGE=1`; default off = frozen
   path.
3. Run the streamed MXFP4 model with `-ngl 999` (streamed expert
   memory is bounded, so full offload does NOT hit the 24 GB ceiling
   the non-streamed case hit), 64 tokens, coding prompt, seed 7.

**Acceptance:**
- No abort; process exits cleanly; generated text present.
- phase07 per-step invariants PASS on the run.
- Record decode tok/s and resident MB vs the frozen CPU run (64-token
  smoke, n=1 — descriptive, not a gate).
- If the abort persists, capture the new backtrace and document the
  exact remaining boundary (e.g., the copy path, buffer type
  mismatch) as evidence for E1b.

**Success definition:** streamed MXFP4 executes on Metal without
aborting and passes invariants. This answers the architectural question
and gates E2's Metal variant and E4.

## Reproduction

Attribution analysis (no new experiments; re-derives the tables above):

    python3 - <<'PY'
    # decode rows after WARMUP=3 from:
    #   benchmarks/results/phase-k1/uncached/uncached/b*-{A-*,B}/stats.csv
    #   benchmarks/results/phase-k1/full/coding-cap4/b*-{A-*,B}/stats.csv
    # median per-step: repack_us placement_us pread_us pread_wall_us
    #   route_compute_us expert_compute_us build_us_measured other_us total_us
    PY

Metal crash reproduction (known failing, current state):

    env KIMI_STREAM_EXPERTS=naive KIMI_EXPERT_CACHE_MODE=zerocopy KIMI_EXPERT_CACHE_MB=4096 \
      llama.cpp/build-metal/bin/llama-cli \
      -m models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
      -ngl 999 -c 4096 -p "$(cat benchmarks/prompts/phase-03-coding-lru.md)" \
      -n 64 --temp 0 --seed 7 --no-display-prompt --no-conversation --single-turn \
      < /dev/null   # SIGABRT, GGML_ASSERT(buf_dst) at ggml-metal-context.m:359

## Next Phase

E1 implementation + measurement, then E2 selection probe (storage-side
repack vs native kernel vs MXFP4-style layout) driven by the E2 gate.
