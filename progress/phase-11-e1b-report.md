# Phase 11 E1b Report — Restore Trace Observability on Streamed Metal + CPU-vs-Metal Localization

## Status

PASS. E1 remains frozen as the Phase 11 Metal-execution baseline; E1b
adds restored trace observability (`KIMI_TRACE_ACT` / `KIMI_TRACE_MOE`)
on the streamed Metal path and delivers the first bounded CPU-vs-Metal
divergence localization. E2 has NOT been started.

## Objective

Per the E1b directive: reproduce and localize the
`KIMI_TRACE_ACT`/`KIMI_TRACE_MOE` Metal-load SIGTRAP; determine whether
it is a zero-size/NULL boundary, an invalid CPU readback assumption, a
Metal buffer-type/lifetime issue, or something else (without assuming
the cause from E1); implement the smallest env-gated fix; then use the
working trace to localize where Metal execution first materially
diverges from the coherent CPU reference.

## Changes

- `llama.cpp/src/llama-graph.cpp` — **the E1b fix** (both trace dump
  functions; these run only when `KIMI_TRACE_ACT`/`KIMI_TRACE_MOE` are
  set, so the frozen/default path is byte-identical):
  1. `llm_trace_act_dump`: after `ggml_backend_tensor_get_async(...)`
     into the per-record host vector, call `ggml_backend_synchronize(backend)`
     before reading/freeing the buffer.
  2. `llm_trace_moe_dump`: same synchronize after the `rec.topk`
     readback, plus a `buf.empty()` guard (0-element topk, the same
     0-size class as E1's readback guard).
- No other code changed. E1's two fixes and this fix are all
  env-gated; unset env = frozen path.

## Results

### Root cause of the trace SIGTRAP (evidence, not assumption)

- Reproduced deterministically: streamed MXFP4 `-ngl 999` with
  `KIMI_TRACE_ACT=1` (+/or `KIMI_TRACE_MOE=1`) → SIGTRAP (EXIT=133) at
  the layer-1 route subgraph, act.bin partially written (~46 KB),
  moe.csv header-only.
- Crash reports (three independent runs, identical stack):
  `EXC_BREAKPOINT/SIGTRAP` in `_xzm_xzone_malloc_freelist_outlined`
  (hardened malloc detecting freelist corruption) ← `operator new`
  ← `llm_trace_act_dump +472` ← `process_ubatch_streamed +5736`.
  The allocation being attempted is 9216 B = n_embd×4 (a sane size),
  i.e. the heap was ALREADY corrupt when the next record's host vector
  was allocated.
- Mechanism (matches all evidence): the trace dumps issue
  `ggml_backend_tensor_get_async` into a short-lived host vector, then
  read and free that vector at the end of the loop iteration. On the
  CPU backend `get_tensor_async` is NULL → the wrapper falls back to
  `synchronize + synchronous get`, so the host buffer is valid when
  read. On the Metal backend `get_tensor_async` queues a REAL async
  blit (`newBufferWithBytesNoCopy:` wrapping the host pointer, encoder
  copy, no wait) that writes into the host buffer LATER — after the
  vector has been freed → write into freed heap → malloc freelist
  corruption → next allocation traps.
- Classification: **invalid CPU readback assumption** (the trace code
  assumed an async get had completed before the host buffer was
  consumed; true only for the CPU backend's synchronous fallback).
  NOT a zero-size/NULL boundary (that was E1's bug) and not a Metal
  buffer-type issue.

### Acceptance (all pass)

| check | result |
|---|---|
| streamed MXFP4 Metal execution exits cleanly (trace envs on) | EXIT=0 |
| `KIMI_TRACE_ACT` + `KIMI_TRACE_MOE` produce intended artifacts, no SIGTRAP | act.bin 191,812,299 B, moe.csv 7,667 rows |
| phase07 invariants remain PASS (Metal, trace envs on) | PASS (0 violations) |
| normal E1 Metal execution remains clean with tracing disabled | EXIT=0 (259.6/20.9 t/s smoke) |
| CPU trace behavior unchanged | EXIT=0; act.bin and moe.csv **byte-identical** to the pre-fix CPU trace run |

### CPU-vs-Metal divergence localization (first bounded pass)

Using the restored traces (CPU reference `e1b-cpu-trace`, Metal
`e1b-metal`; same prompt/seed/config):

- Trace capture set is identical: 23,035 act records per run, 0 key
  mismatches (same phase/pos/layer/name set); moe.csv 7,666 rows each.
- First material activation divergence: **layer 1 `attn_out`,
  first prefill step** — max|d| ≈ 1.2e-3 (ph=0, sp=0, n_tokens=2,
  il=1). Growth is monotonic with depth: il=0 l_out 1.7e-3 → il=6
  moe_out 0.29 → il=18 l_out 1.55 → il=26 l_out 2.68; first
  max|d|>0.01 at il=1 `router_logits`, >0.1 at il=6 `moe_out`, >1.0
  at il=22 `moe_up`. Overall max|d| across common records: 89.2.
- Routing divergence: first row where the top-8 expert SET differs at
  prefill layer 11 (row 20); earlier rows (from layer 8) show
  permutation-only differences (same set, different order) consistent
  with tie/order sensitivity. Set-divergent rows: 2,494/7,666;
  permutation-only: 2,054/7,666. Prefill divergence density grows
  with depth (il=1: 49/241 → il=25: 146/241); decode is ~45–57/63 per
  layer.
- Interpretation: the Metal path is numerically trustworthy at the
  START (sub-2e-3 at layer 0/1) and drifts to O(1) divergence by the
  middle/upper layers — the pattern of small per-op numeric differences
  (accumulation order / MXFP4 dequant on Metal vs CPU) amplified
  through 26 MoE layers and the routing argmax, not a single broken
  op. This is consistent with the garbled Metal output seen in E1.
  Per the directive, the numeric divergence is NOT fixed here: its
  cause is not trivial/unambiguous (candidate: Metal accumulation order
  in attention/router/expert matmuls), so it is recorded as
  localization evidence for E2/E4.

## Problems

- The trace SIGTRAP was a latent host-buffer lifetime bug in the trace
  dumps, invisible on CPU (synchronous fallback) and on Metal without
  trace envs. Fixed as above.
- The CPU-vs-Metal numerical divergence (activation drift from layer 1,
  routing set divergence from layer 11) is unresolved by design —
  documenting, not fixing, per directive. It is the E2/E4 quality
  gate: Metal path must be shown numerically trustworthy (e.g. PPL
  within the K1 band) before any Metal performance claim.

## Decisions

- Keep the fix env-gated (trace envs already gate these functions);
  default path unchanged; rollback = unset trace envs / revert commit.
- Synchronize-per-record (not one sync after the loop): each record's
  host vector is freed at iteration end, so each readback must be
  complete before the next iteration.
- Localization stays bounded (single 64-token pair of runs) per the
  directive; no new experiments added.

## Next Phase

- E2 (repack/native-runtime optimization) remains the candidate, but
  per the directive it should begin only after the Metal-path numeric
  question is understood. First step for that: a bounded PPL or
  per-layer numeric comparison on Metal vs the frozen CPU reference
  (the restored act/moe traces are the instrument for it).
- E1b did not add experiments to the frozen K1/E1 baselines.

## Reproduction

Trace SIGTRAP (pre-fix behavior; current tree fixed):

    env KIMI_STREAM_METAL_STAGE=1 KIMI_STREAM_EXPERTS=naive \
      KIMI_EXPERT_CACHE_MODE=zerocopy KIMI_EXPERT_CACHE_MB=4096 \
      KIMI_TRACE_ACT=1 KIMI_TRACE_ACT_FILE=act.bin \
      KIMI_TRACE_MOE=1 KIMI_TRACE_MOE_FILE=moe.csv \
      llama.cpp/build-metal/bin/llama-cli -m <MXFP4 gguf> -ngl 999 -c 4096 \
      -f benchmarks/prompts/phase-03-coding-lru.md -n 64 --temp 0 --seed 7 \
      --no-display-prompt --no-conversation --single-turn < /dev/null
      # pre-fix: SIGTRAP (EXIT=133), heap corruption in llm_trace_act_dump
      # post-fix: EXIT=0, act.bin + moe.csv produced

Acceptance run (post-fix), artifacts retained at
`benchmarks/results/phase-k1/e1b-metal/` (CPU reference at
`benchmarks/results/phase-k1/e1b-cpu-trace/`):

    # same command as above (trace envs on) + phase07 capture envs:
    # KIMI_STREAM_STATS_FILE/RETR_FILE/MEM_FILE/CACHE_LAYERS_FILE
    python3 tools/phase07_summarize.py benchmarks/results/phase-k1/e1b-metal
    # -> invariants: PASS (0 violation(s))

Localization analysis:

    python3 - <<'PY'   # compare act.bin + moe.csv CPU vs Metal
    ... (parse TCAT records; align by (phase,start_pos,il,name); report
         first max|d|>thr and routing set divergence; see report text)
    PY
