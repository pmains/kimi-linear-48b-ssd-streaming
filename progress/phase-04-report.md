# Phase 04 Report

## Status

**PASS** on the critical path (numerical equivalence + miss-path decomposition
+ cache-ladder projection). Acceptance item 2 (resident memory measurably
lower) remains **unmeasured** — see Problems.

The layer-1 `moe_out` seed that blocked Phase 4 is resolved: the streamed
executor now reproduces conventional inference **bit-for-bit** on the A/B/C
oracle.

## Objective

Phase 4 (uncached expert streaming) was PARTIAL: the streamed executor ran
end-to-end and retrieved every routed expert byte-exact from the GGUF, but did
not reproduce conventional inference. Every captured input to layer 1's MoE
was bit-identical while `moe_out` differed (~1e-8), with attention amplifying
the seed ~250x–5000x past the 1e-5 comparator threshold. This session named
the seed mechanism, fixed it, re-verified, and produced the 4B/4C latency work
(decomposition + tok/s projection) that Phase 4 exists to deliver.

## Changes

### llama.cpp (commit `c111f595f`, on top of `9f72fc0aa`)

- `src/llama-expert-stream.cpp` — `load_layer` now allocates the loaded expert
  tensors (`up`/`gate`/`down`) in the **same buffer type as the resident parent
  tensors** (`ggml_backend_buffer_get_type(t_up->buffer)`), instead of the
  default CPU buffer. `ggml_backend_tensor_set` then repacks the pread raw
  bytes into the identical in-memory layout. The `ids` tensors (I32) remain in
  the default buffer (the repack buffer only repacks quantized MUL_MAT_ID
  weights and has `get_tensor = nullptr`). Also guarded the `n_slots == 0`
  case (last MoE layer during prefill, before any output token is selected)
  where a 0-size expert context makes `alloc_ctx_tensors_from_buft` return
  `nullptr` benignly — previously this aborted at the layer-26 dense-branch
  assert.
- `src/models/kimi-linear.cpp` — the `router_weights_v` trace capture now views
  `[n_used, cols]` instead of `[n_used, 1]` so its `n_tokens` field matches the
  conventional `router_weights` capture (diagnostic-only; the readback still
  compares the token-0 column).

### project repo

- `tools/phase04_compare.py` — `norm_keys` folds stream-only virtual roles onto
  their conventional counterparts so the structural record-set check passes.
- `tools/phase04_project.py` (new) — miss-path decomposition + ladder projection.
- `benchmarks/results/phase-04-miss-path.json`, `phase-04-projection.csv` (new).
- `benchmarks/results/traces/phase-04-fix-conv/` and `phase-04-fix-stream/` —
  the post-fix oracle captures (conventional + streamed).
- `benchmarks/results/phase-04-compare-current.txt` is now the post-fix PASS
  comparator output; the pre-fix failing output was renamed to
  `phase-04-compare-pre-fix.txt`.

## Results

### Seed mechanism (M1)

The conventional path stores routed expert weights in the CPU backend's
**"repack" buffer type** (`GGML_USE_CPU_REPACK`, interleaved K-quant layout
`block_q4_Kx8` / `block_q6_Kx8`, selected because `weight_buft_supported` lets
the repack buft claim 3-D MUL_MAT_ID weights and it precedes the plain CPU buft
in `make_cpu_buft_list`). The streamed loader allocated its compact
`[n_embd, n_ff, n_slots]` experts in the **default** CPU buffer (raw file
layout). The `mul_mat_id` kernels for the two layouts accumulate in different
fp32 order, so identical inputs + identical (value-wise, byte-reordered)
weights produced ~1e-8 differences — the seed. Evidence: the `KIMI_DX_VERIFY`
diagnostic showed the parent tensor's `data` outside the mmap region with
`first_byte=2` for Q4_K (interleaved `d[0]` at bytes 0-1, `d[1]` at byte 2,
exactly the `block_q4_Kx8` header) and the `/tmp/dx_l1_up_parent.bin` dump
`c90b b20a 190b 950b …` vs the file-layout `c90b b018 fcf6 e6ec …`. The repack
is value-lossless; the divergence is accumulation order, not data.

### Fix (M2)

Allocate the loaded experts in the parent's buffer type. Minimal, preserves the
conventional path bit-for-bit (untouched).

### A/B/C re-verification (M3)

```
A Retrieval:  PASS  (36,288/36,288 byte ranges exact vs mmap)
B Routing:    PASS  (1,512 rows bit-identical)
B First divergence: none within criterion
C Logits:     PASS  max|d|=0.0  mean|d|=0.0  top-1/top-5 agree
OVERALL:      PASS
```

Every captured activation (`l_in`, `attn_out`, `ffn_inp`, `ffn_normed`,
`router_logits`, `router_weights`, `moe_up/gate/down`, `moe_out`, `l_out`) and
the logits are **bit-identical** (`max|d|=0.0`), not merely within the 1e-5
tolerance.

### 4B miss-path decomposition (M4+M5, decode, per step)

| component | µs/step | share |
|---|---|---|
| storage pread | 417,286 | 49.6% |
| buffer prep / repack copy | 241,799 | 28.7% |
| sync | 2 | ~0% |
| kernel (MoE expert compute) | 42,326 | 5.0% |
| trunk (attention + route) | 110,405 | 13.1% |
| build residual | 29,454 | 3.5% |
| **TOTAL** | **841,274** | **1.19 tok/s (uncached)** |

Storage pread sustains ~2.0 GiB/s over 0.89 GB/step (208 distinct experts ×
~4.3 MB avg). `build_us` is now a small positive residual (the M4 accounting
fix — step-start snapshot + per-step deltas — is confirmed by the stats.csv:
`pread_calls` constant at 624/step, `build_us` 19–59 ms).

### 4C cache-ladder projection (M5)

Conservative lower bound (`t_step(C) = total − pread·h(C)`; a hit removes only
the SSD pread, no prefetch/overlap/copy-elision assumed), global-LRU steady
hit rates from `phase-03-locality.json`:

| cache GB | steady hit % | tok/s |
|---|---|---|
| 1 | 32.7 | 1.42 |
| 2 | 44.3 | 1.52 |
| 4 | 58.2 | 1.67 |
| 6 | 67.5 | 1.79 |
| 8 | 74.2 | 1.88 |
| 10 | 79.1 | 1.96 |
| 12 | 84.8 | 2.05 |
| 14.6 (Phase 2 headroom) | 88.4 | 2.12 |

Key insight: the miss path (pread + repack copy ≈ 659 ms) dominates, so the
ceiling even at ~88% hit is only ~2.1 tok/s. The copy is now a **repack**
(241 ms), not a plain memcpy — a Phase 6 cache should store already-repacked
experts so a hit elides both the pread and the re-repack.

## Problems

1. **Acceptance 2 (resident memory measurably lower) is unmeasured.** Both
   captures used `-ngl 0` (CPU, mmap-backed weights), so "resident memory" is
   dominated by the mmap page cache rather than a resident expert collection.
   The streamed design is structurally single-use with `n_slots`-bounded expert
   buffers (guaranteeing lower expert residency than the conventional 256-expert
   tensors), but no RSS/allocated-size measurement has been taken. This is the
   only acceptance item still open.
2. **`KIMI_DX_VERIFY` is now moot and would crash if re-enabled.** It read back
   the loaded tensor via `ggml_backend_tensor_get`, which the repack buffer type
   does not support (`get_tensor = nullptr`). Its original purpose (compare
   parent vs file bytes) is answered: the difference was the repack layout.
3. **The repack copy is a real cost.** Making the streamed path bit-identical to
   conventional means each loaded expert is repacked on every access (241 ms of
   the 841 ms step). This is correct (it mirrors what the conventional loader
   pays once at load), but it is now on the miss path every step.

## Decisions

- Loaded experts use the **parent tensor's buffer type** rather than the default
  backend buffer. This is the single source of truth for "the same layout the
  conventional path computes over" and guarantees bit-identity without
  hard-coding the repack buft.
- Projection uses a **conservative lower bound** (hit removes only the pread).
  It deliberately does not assume prefetch/overlap, so the numbers are a floor
  that Phase 6 must beat.
- The conventional path is byte-unchanged; it remains the permanent oracle.

## Next Phase

Phase 5 (correctness validation) can begin: CPU equivalence is stable and
bit-identical. Phase 5 needs (a) a decision on whether acceptance 2 (memory)
must close before Phase 4 is declared complete, and (b) the note that a Phase 6
cache must store repacked expert bytes to avoid re-paying the 241 ms repack on
every hit.

## Reproduction

Environment: Apple M5, 24 GB unified memory, macOS 26.5.2. Model:
`models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`
(Q4_K_M, 30,061,058,720 B). llama.cpp at `c111f595f` (the manifest records
`9f72fc0aa` + dirty because the captures ran before the commit).

```bash
# build
cmake --build llama.cpp/build-metal --target llama-cli -j6

# conventional oracle
tools/phase04_run_capture.sh benchmarks/results/traces/phase-04-fix-conv \
    benchmarks/prompts/phase-04-ref.md 10 1

# streamed oracle
tools/phase04_run_streamed.sh benchmarks/results/traces/phase-04-fix-stream \
    benchmarks/prompts/phase-04-ref.md 10 1 naive

# compare (A/B/C)
python3 tools/phase04_compare.py \
    benchmarks/results/traces/phase-04-fix-conv/act.bin \
    benchmarks/results/traces/phase-04-fix-conv/moe.csv \
    benchmarks/results/traces/phase-04-fix-stream/act.bin \
    benchmarks/results/traces/phase-04-fix-stream/moe.csv \
    benchmarks/results/traces/phase-04-fix-stream/retr.csv

# 4B/4C decomposition + projection
python3 tools/phase04_project.py \
    benchmarks/results/traces/phase-04-fix-stream/stats.csv \
    benchmarks/results/phase-03-locality.json
```
