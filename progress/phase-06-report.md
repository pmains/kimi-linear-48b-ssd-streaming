# Phase 06 Report — Repacked-Expert Cache

## Status

**PARTIAL** — correctness, budget enforcement, eviction safety, and hit-rate
behavior are all PASS and measured; the performance acceptance is **not met**
as measured: the cached streamer does not beat the measured uncached
intercept (~2.6 tok/s) because the hit path's per-step placement copy
(~890 MB/step) costs as much as the SSD I/O it replaces, and large budgets
add memory pressure on the 24 GB machine. The roadmap's flagged question —
"can a hit feed `mul_mat_id` directly from the cached buffer (no per-step
copy)?" — is answered by measurement: **the copy matters and dominates**;
eliminating it requires aliasing the loaded expert tensors to cache storage,
which the STOP point defers as advanced streaming optimization.

## Objective

Make the streamer fast: add a bounded, byte-configurable cache of
backend-ready repacked expert slices between routing and expert storage, so a
hit elides both the SSD pread (~80 ms/step) and the CPU repack transform
(~240 ms/step) measured in Phase 4E/5. Preserve bit-identical execution.

## Changes

### llama.cpp (on top of `8b43eac45`; project commit `??`)

- `src/llama-expert-stream.h/.cpp` — bounded repacked-expert cache,
  env-gated (`KIMI_EXPERT_CACHE_MB`, default 0 = off → byte-identical to the
  Phase 4/5 streamer; `KIMI_CACHE_SELFCHECK`, default off):
  - key = (layer 1..26, expert 0..255), value = the **repacked** slice bytes
    for up/gate/down (per-expert granularity; the three move together);
  - LRU eviction; per-slice vm-backed storage with a **per-size free pool**
    (capped at budget/4) so eviction recycles instead of
    vm_allocate/deallocate churn (~100 ms/step in the first build);
  - **subset-repack placement**: the repack buft's `set_tensor` asserts
    `offset == 0 && size == ggml_nbytes` (full-tensor only — verified in
    `ggml/src/ggml-cpu/repack.cpp`), so per-slot partial sets are impossible.
    Misses are repacked one slice at a time through a persistent single-slice
    temp tensor `[ne0, ne1, 1]` in the repack buft, then memcpy'd into the
    loaded tensor at `slot * slice_bytes`; hits are pure memcpy from the
    cache. Stride = slice_bytes (repack buft `get_alloc_size` defaults to
    `ggml_nbytes`; per-slice contiguity proven by the self-check);
  - `cache_verify_layout` (selfcheck): per-slot byte-compare of the
    temp-slice repack vs the full tensor_set region — **PASSED on first
    run**, proving the layout invariant empirically;
  - stats: per-step cache columns in the stats CSV
    (lookups/hits/misses/evictions/hit_bytes/bytes_used/budget).
- `src/llama-expert-stream-exec.cpp` — stats CSV extended with the cache
  columns (appended at end; existing positional parsers unaffected).
- Construction fixes found by the build/run loop: `ggml_init` params are
  `{mem_size, mem_buffer, no_alloc}`; `GGML_MEM_ALIGN` is 16 KB in this
  build (temp ctx sized 1 MB).

### kimi repo

- `tools/phase06_run_ladder.sh` — ladder runner (1–14.6 GB + uncached,
  64 tokens, ref prompt, ctx pinned 4096, min-of-2 per rung, reversed order
  to decorrelate thermal drift).
- `tools/phase04_run_streamed.sh` — optional `CTX` env to pin the KV cache
  (Phase 4E convention; default unchanged); bash-3.2-safe empty-array
  expansion (macOS ships bash 3.2; `"${arr[@]}"` + `set -u` on an empty
  array aborts).
- `progress/phase-06-design.md` — design + implementation status.

## Results

### Correctness (acceptance 4) — PASS

10-token oracle, final binary, cache ON (hits + misses + evictions +
insertions), vs fresh conventional capture (`conv-10`):
`phase04_compare` OVERALL PASS — 33,291/33,291 retrieval ranges byte-exact,
router rows bit-identical, all activations/logits max|Δ| = 0. Re-verified
after the free-pool and pool-cap changes. Self-check (per-slice repack ==
full tensor_set region) passes.

### Budget (acceptance 1) — PASS

`cache_bytes_used` tracks the configured budget at every rung (1022/1024,
2047/2048, 4094/4096, 6143/6144, 8190/8192, 10236/10240, 12286/12288,
14949/14950 MB). Residency verified with the Phase 4E memory protocol
(ctx 4096): 8 GB budget → **10,247 MB decode phys_footprint** = ~2.0 GB
baseline + 8.2 GB cache. (An unpinned `--ctx-size` run measured 18.3 GB —
that is the default 8 GB f16 KV cache, the Phase 4E artifact, not the
cache; all memory comparisons pin ctx.)

### Eviction (acceptance 3) — PASS

LRU eviction exercised at every rung (8,131 evictions at 1 GB down to 521 at
14.6 GB over 57 decode steps) with the oracle still bit-identical. The free
pool (evicted blocks recycled per size class, capped at budget/4) was added
after the first build showed ~100 ms/step of vm_allocate/deallocate churn;
a measured thrash scenario showed the uncapped pool could hold ~8 GB of dead
blocks — the cap bounds resident cache memory to ≤ 1.25× budget.

### Hit-rate behavior — PASS (validates the Phase 3 sim)

| capacity GB | measured hit | sim steady | Δ |
|---|---|---|---|
| 1 | 0.337 | 0.327 | +0.010 |
| 2 | 0.466 | 0.443 | +0.023 |
| 4 | 0.582 | 0.582 | 0.000 |
| 6 | 0.688 | 0.675 | +0.013 |
| 8 | 0.765 | 0.742 | +0.023 |
| 10 | 0.834 | 0.792 | +0.042 |
| 12 | 0.886 | 0.848 | +0.038 |
| 14.6 | 0.917 | 0.884 | +0.033 |

The implementation's global LRU reproduces the sim's predictions within
1–4 points at every capacity — the cache behaves exactly as designed.

### Performance (acceptance 5) — NOT MET as measured

Two ladder passes (ladder1: hot machine, ctx unpinned; ladder2: cooled
machine, ctx 4096, min-of-2, reversed order). Steady decode (steps ≥ 8),
64 tokens, ref prompt:

| capacity GB | tok/s (best of passes) | avg step ms | pread ms | copy ms | build ms |
|---|---|---|---|---|---|
| uncached | **2.63** (ladder2, ctx-pinned) | 381 | — | — | — |
| 1 | 1.78 | 561 | 146 | 316 | 40 |
| 2 | 1.83 | 546 | 130 | 284 | 41 |
| 4 | 2.06 | 485 | 122 | 372 | 54 |
| 6 | 1.83 | 547 | 122 | 445 | 66 |
| 8 | 1.61 | 623 | 96 | 440 | 63 |
| 10 | 1.74 | 575 | 64 | 343 | 53 |
| 12 | 1.95 | 513 | 49 | 486 | 71 |
| 14.6 | 1.87 | 534 | 31 | 396 | 35 |

- The cache **beats the Phase 4 projection floor (1.42 → 2.12 tok/s) at the
  low rungs** (1: 1.78 > 1.42, 2: 1.83 > 1.52, 4: 2.06 > 1.67) but not at
  ≥ 6 GB. **However the floor itself is stale**: it was built on a 417 ms
  SSD-read estimate, while the measured pread is 80–150 ms/step — the
  uncached streamer already clears the floor's top (2.63 > 2.12). The honest
  bar is cached-vs-uncached on the same binary, and **the cache does not
  clear it**: the best cached run (2.06 tok/s at 4 GB) is below the uncached
  intercept (2.63).
- Root cause, measured: the hit path still copies every selected slice into
  a freshly-allocated loaded-expert buffer each step (~890 MB/step of
  memcpy + fresh vm-page fault-in), costing ~300–480 ms/step in `copy_us` —
  the same order as the I/O it replaces. At large budgets, the cache itself
  (~14 GB) plus baseline (~2 GB) plus page cache approaches the 24 GB limit
  and macOS compression adds further tax.
- The roadmap's measurement question is answered: **the per-step copy
  matters; a hit must feed `mul_mat_id` directly from the cached buffer**
  (loaded tensors aliased to cache storage) for the cache to win. That is
  the deferred "advanced streaming optimization" per the STOP point.

## Problems

1. **Performance acceptance not met** (details above). The cache is a
   correct, honest, validated mechanism that currently trades SSD time for
   copy time at ~parity on this machine.
2. **Benchmark environment is hostile**: the fanless Air's thermal drift
   swamps ±50% timing differences; back-to-back runs degrade progressively
   (uncached measured 381–618 ms/step across sessions). Mitigations used:
   min-of-2, reversed order, ctx pinning, same-session controls. Even so,
   rung-to-rung timing noise exceeds the cache's effect size below ~4 GB.
3. **Unpinned `--ctx-size` is a trap** (8 GB KV at default n_ctx). ladder1's
   big rungs ran with 8 GB KV + up to 14.6 GB cache ≈ 25 GB — severe
   memory pressure. ladder2 pins 4096. All Phase 6 numbers above are
   ctx-pinned unless noted.
4. The projection floor (1.42 → 2.12) is not a meaningful acceptance bar —
   the uncached streamer already exceeds it (stale 417 ms SSD estimate vs
   measured ~80–150 ms). Recommend re-baselining the acceptance to
   cached-vs-uncached on the same binary.

## Decisions

- **Subset-repack placement** (single-slice temp + memcpy) instead of
  per-slot `tensor_set`, which the repack buft forbids (assert
  offset == 0). Verified byte-identical by the self-check and the oracle.
- **Free pool with cap**: vm_allocate per slice on the miss path is
  acceptable; per-eviction vm_deallocate is not. Pool recycles evicted
  blocks (zero vm churn in steady state); capped at budget/4 so residency
  stays honest.
- **CPU-only direct placement**: Phase 6's hit path is CPU-backend-only
  (the oracle mode); Metal falls back to the uncached path. Metal work is
  out of scope per the roadmap.
- **Cache is env-gated off by default**; the uncached streamer remains the
  default path, so Phase 4/5 behavior is unchanged unless the cache is
  explicitly enabled.
- **Acceptance re-baseline recommended**: the performance acceptance should
  be cached-vs-uncached speedup, not the stale projection floor.

## Next Phase

Phase 6 is PARTIAL; per the operating rules, Phase 7 (Instrumentation) does
not auto-progress. The measured evidence makes the required next step clear:
**zero-copy hits** — allocate the per-step loaded expert tensors so their
data points into the cache's repacked blocks (a repack-buft buffer wrapping
cache storage), eliminating the ~890 MB/step placement copy. This is the
roadmap's deferred "advanced streaming optimization"; the STOP point says
further work must be selected from measured results — this measurement
(placement copy ≈ 300–480 ms/step, the entire cache budget's worth) is that
selection. Also recommended before any continuation: pin `--ctx-size` in all
streamed capture tooling by default, and re-baseline the ladder acceptance.

Phase 6 also leaves for Phase 7: coalesced-mode cache adoption (same hooks),
and per-kind temp reuse accounting.

## Reproduction

```bash
# oracle (final binary; conv-10 is the conventional reference)
CTX=4096 KIMI_EXPERT_CACHE_MB=2048 tools/phase04_run_streamed.sh \
    benchmarks/results/phase-06/final-oracle benchmarks/prompts/phase-04-ref.md 10 1 naive
python3 tools/phase04_compare.py \
    benchmarks/results/phase-06/conv-10/act.bin benchmarks/results/phase-06/conv-10/moe.csv \
    benchmarks/results/phase-06/final-oracle/act.bin benchmarks/results/phase-06/final-oracle/moe.csv \
    benchmarks/results/phase-06/final-oracle/retr.csv

# ladder (ctx pinned, min-of-2, reversed order)
tools/phase06_run_ladder.sh benchmarks/results/phase-06/ladder3 64 1

# memory protocol at a budget (Phase 4E three-quantity protocol)
CTX=4096 KIMI_EXPERT_CACHE_MB=8192 KIMI_STREAM_MEM_FILE=<out>/mem.csv \
    tools/phase04_run_streamed.sh <out> benchmarks/prompts/phase-04-ref.md 10 1 naive
# decode steady phys: awk -F, 'NR>1 && $3=="decode" && $1>=6 {s+=$5; n++} END {print s/n}' <out>/mem.csv

# selfcheck (one-time layout verification)
KIMI_EXPERT_CACHE_MB=2048 KIMI_CACHE_SELFCHECK=1 \
    tools/phase04_run_streamed.sh <out> benchmarks/prompts/phase-04-ref.md 10 1 naive
```

Environment: Apple M5 (Mac17,3), 24 GB unified memory, macOS 26.5.2;
llama.cpp `8b43eac45` + Phase 6 changes; model
`moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`; `-ngl 0` CPU,
`--no-mmap`, `--ctx-size 4096` (ladder/memory runs), `--temp 0 --seed 1`.
Benchmark results: `benchmarks/results/phase-06/{conv-10, final-oracle,
selfcheck-10b, mem-8gb, ladder, ladder2}`.
