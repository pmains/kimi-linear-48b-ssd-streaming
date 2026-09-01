# Phase 11 — Routed-Expert-ID Synchronization Fix (E4 root cause) — Validation

## Status

PASS — **E4 root cause fixed and validated.** The minimal routed-expert-ID
synchronization fix (one `ggml_backend_synchronize` after the async ids
readback in the streamed executor) restores real router-selected experts
on the default (trace-off) streamed Metal path. **Metal perplexity
recovers from ×218,541 degradation to the CPU/K1 quality class**:
full 32×512 E4 → CPU 6.7596, **Metal 6.7528** (rel −0.10%, within
noise; per-chunk ratios 0.997–1.002). All validation steps pass
(trace-off slot integrity, trace-off/on A/B, bounded E4, full E4).
Stopped for review per directive. E3 not begun.

## Objective

Implement the minimum correctness fix at the routed-ID readback
boundary established by the E2 slot-integrity experiment: the streamed
Metal path issued `ggml_backend_tensor_get_async` for the routed expert
ids and consumed `ids_host` before the backend-to-host transfer
completed, so (with trace capture disabled, i.e. the E4 configuration)
every routed expert id read back as **0** — expert 0 selected
everywhere → garbage logits → E4 PPL ~1.48M. Trace-on accidentally
masked the defect by synchronizing the backend inside the dump loops
(the E1b pattern). Validate in order: trace-off slot integrity, A/B,
bounded E4, full E4. No kernels/model math/quantization changes; no E3.

## The fix (fork, minimal)

`llama.cpp/src/llama-expert-stream-exec.cpp` — after the routed-ids
`ggml_backend_tensor_get_async`, add:

    ggml_backend_synchronize(backend);

Narrowest synchronization at the readback boundary, mirroring the
validated E1b pattern (synchronize after async get in the trace dump
loops). No-op on CPU (CPU `get_async` falls back to a synchronous
copy). Default path behavior otherwise unchanged; 10 lines added.

## Validation

### Step 1 — bounded trace-off slot-integrity config (fixed binary)

`tools/phase11_zc_slotcheck_run.sh 4096` (frozen streamed Metal config,
trace off, `KIMI_DX_ZC_VERIFY=1`):

- **routed IDs no longer zero**: `ROUTE il=2 unique=[25,78,17,136,64,
  46,30,221]`, `il=3 unique=[151,226,14,74,129,109,65,76]`, `il=4
  unique=[9,126,167,237,17,35,111,153]` … (real experts; pre-fix was
  `unique=[0]` n_slots=1 for every layer);
- **slot integrity 100% clean**: 44,136 occurrences checked, 0 failures
  (25,971 hits, 18,165 misses, **17,076 slot reuses**, 10,836 fresh-
  pread cross-checks) — the counters now show the real-routing
  signature (pre-fix trace-off: 183,936 checks / 1,224 misses / 0
  reuses / 78 cross-checks, all collapsed to expert 0);
- **output coherent**: "# Concurrent LRU Cache Implementation …" (pre-
  fix: garbage + peg-native format error);
- EXIT=0, no asserts/NaN/Inf.

### Step 2 — trace-off vs trace-on A/B (fixed binary)

Same binary/config; A = trace off, B = `KIMI_TRACE_ACT=1`. Both now
produce the **identical** routing (`unique=[25,78,17,136,64,46,30,221]`
… in both) and the identical slot-integrity summary (44,136 / 25,971 /
18,165 / 17,076 / 10,836, 0 failures), with coherent output in both.
**Routing behavior and output quality no longer depend on
KIMI_TRACE_ACT/MOE.**

### Step 3 — bounded E4 perplexity gate (8 × 512)

| arm | PPL |
|---|---:|
| cpu (reference) | 7.2777 |
| metal (fixed) | **7.2601** |

Pre-fix bounded metal was 1,374,490. Recovery confirmed; proceed to
full protocol.

### Step 4 — full original E4 protocol (32 × 512) vs frozen CPU reference

| arm | final PPL |
|---|---:|
| cpu (reference, fixed binary) | **6.7596** |
| metal (fixed) | **6.7528** |

- abs ΔPPL (Metal − CPU) = **−0.0068**
- rel ΔPPL = **−0.101%** (Metal marginally better; same quality class)
- per-chunk Metal/CPU ratio: 0.997–1.002 across all 32 chunks
  (pre-fix ratio was ×155k–×286k on every chunk)
- per-chunk max |d| = 0.0216, mean |d| = 0.0132
- CPU final exactly reproduces the accepted K1/E4 CPU reference
  (6.7596 ± 0.19) — protocol continuity confirmed on the fixed binary.

**Primary acceptance criterion MET:** Metal perplexity has returned to
the same quality class as the CPU/K1 reference (6.7528 vs 6.7596)
instead of the previous ~1.48M catastrophic result. Token/routing
identity is not required (and routing now differs in the healthy
direction — real, varied expert selection).

## Performance effect of the added synchronization (reported separately)

- Full E4 wall time (fixed binary): cpu 19:39:44→19:48:44 (9.0 min,
  ~63.3 s/pass), metal 19:48:44→19:53:50 (**5.1 min, ~37.4 s/pass**);
  Metal ≈ 1.7× CPU on this workload with correct routing.
- The fix adds one `ggml_backend_synchronize` per MoE layer-step at the
  ids readback — the same sync the trace loops already perform. The
  pre-fix "fast" metal timing (2.24 s/pass) is not a valid baseline: it
  ran the degenerate n_slots=1 (all-expert-0) path. No separate
  micro-benchmark was run (correctness is the gate per directive; do
  not optimize synchronization in this task).

## Problems

- None observed in the fixed binary across all four steps. The earlier
  "Metal numerical defect" framing is fully resolved: the kernels and
  arithmetic were sound; the default streamed-Metal path was feeding
  zeros to the router→loader boundary.

## Decisions

- Minimal fix applied at the routed-ID readback boundary only;
  rollback = revert the single `ggml_backend_synchronize`.
- Correctness gate passed; no E3; no optimization of the sync.
- E4 evidence superseded: Metal is quality-qualified on the frozen E2
  path with the fix.

## Next Phase

1. Review the fix; optionally promote the fixed fork to the live
   runtime (`runtime/live`) via the freeze workflow after review.
2. Re-run the streamed-Metal E1/E2 performance ladder if desired
   (beyond this task's correctness scope).
3. E3 (compute-side optimization) may resume on a quality-qualified
   Metal baseline — not begun.

## Reproduction

    # fork: apply the fix, rebuild
    cd llama.cpp && cmake --build build-metal -j10

    # step 1/2: trace-off and trace-on slot-integrity runs
    tools/phase11_zc_slotcheck_run.sh 4096
    # trace-on arm: same config + KIMI_TRACE_ACT=1 (see report body)

    # steps 3/4: bounded and full E4 on the fixed binary
    tools/phase11_e4_perplexity.sh cpu  benchmarks/results/phase-11/e4-fix-full/cpu 32 512
    tools/phase11_e4_perplexity.sh metal benchmarks/results/phase-11/e4-fix-full/metal 32 512
    # -> cpu 6.7596, metal 6.7528

Artifacts retained: `benchmarks/results/phase-11/e4-fix-bounded/`,
`e4-fix-full/`, `zc-slotcheck/verify-4096/`,
`zc-slotcheck/verify-4096-traceon-fixed/` (run.log + ppl-result.json +
covariates). Fork fix commit: see llama.cpp git log. Main repo commit:
this report + ROADMAP.
