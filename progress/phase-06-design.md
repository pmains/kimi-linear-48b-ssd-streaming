# Phase 6 Design — Repacked-Expert Cache

Status: DRAFT (2026-08-14), written after Phase 5 close (`ae05b2d`) and the
Phase 4E close (`8b43eac45`).

## Objective

Make the streamer fast. Phases 4/4D/4E/5 closed correctness and residency;
Phase 6 solves performance only, per `ROADMAP.md` §Phase 6.

The measured zero-cache intercept (Phase 4E close, llama-cli, ctx 4096,
--no-mmap, CPU):

- decode steady-state phys_footprint: **~2.0 GB** (active floor 1,974 MB)
- prefill peak: **~2.5 GB** (target < 8 GB)
- sched pools 81 MB, malloc in-use ~0.53 GB (85% scheduler bookkeeping)

Uncached decode step (naive mode, `kimi_stream_stats.csv`, ref prompt):

| component | per-step | share |
|---|---|---|
| pread (SSD) | ~78 ms | 20% |
| copy/repack (`tensor_set` into repack buft) | ~242 ms | 63% |
| route/expert compute + sync + build | ~68 ms | 17% |
| **total** | **~388 ms → ~2.6 tok/s** | |

The cache target: elide **pread + repack** on hits (~320 ms of the 388 ms
step). The roadmap's stated cache value is exactly this — "the expensive
artifact `mul_mat_id` actually wants": the **backend-ready repacked expert
bytes**, not the raw GGUF slices.

## Integration point

`llama_expert_streamer::load_layer` (`llama.cpp/src/llama-expert-stream.cpp`).
The load path per (layer, kind ∈ {up, gate, down}):

1. dedupe router occurrences → unique experts (slot j = j-th unique)
2. allocate loaded tensors `[.., .., n_slots]` in the parent **repack buft**
   (bit-identity requirement from Phase 4 `c111f595f`; do not touch)
3. pread raw GGUF slices into shared staging scratch
4. `ggml_backend_tensor_set(loaded, packed, 0, slice_bytes*n_slots)` —
   this call performs the CPU repack transform (~242 ms)
5. sync

Cache sits between step 2 and step 4: on hit, skip steps 3–4's work and
place cached bytes directly into the loaded tensor's physical memory.

## Cache design

### Key and entry

    key = (layer 1..26, expert_id 0..255, kind {up, gate, down})
    value = repacked physical slice bytes for that expert, as laid out in
            the repack buft's buffer for a [.., .., n_slots] loaded tensor
            (per-expert contiguous slice; stride verified by self-check)

Granularity per roadmap: gate/up/down of one (layer, expert) insert and
evict together. One LRU node per (layer, expert), holding 3 slices.

### Byte budget

Env-gated: `KIMI_EXPERT_CACHE_MB` (default 0 = off → identical to the
Phase 4/5 uncached streamer; the oracle default stays uncached). Budget is
charged per entry using the **measured physical slice bytes** (insert-time
readback size), not the raw file size — repacked size may differ and the
budget must be honest about resident bytes.

### Storage

Grow-only vm-backed scratch via `ggml_aligned_malloc` (same mechanism as
the Phase 4E staging scratch — macOS vm_allocate, actually returned to the
OS on free; malloc's zone cache is a dead end on macOS 26). Cache lives for
the streamer lifetime (or until `stage_release`-style teardown; Phase 6
keeps it for the process lifetime since the streamer is per-context).

### Policy

LRU (roadmap default; `expert_cache_sim.py` already predicts global-LRU hit
rates per ladder capacity from real routing traces — the implementation's
measured hit rate will be compared against those predictions).

### Hit path (the measurement question)

Placement is a **physical memcpy** into the loaded tensor's repack-buft
buffer:

    unsigned char * dst = (unsigned char*) k.loaded->data + slot * stride;
    memcpy(dst, cached_slice, stride);

Rationale: `ggml_backend_tensor_set` on a repack-buft buffer always
re-repacks; there is no "place pre-repacked bytes" API. The repack buft's
buffer base is host memory on the CPU backend (the Phase 6 validation
target, `-ngl 0`), so direct memcpy is valid there. **Scope note: Phase 6
direct placement is CPU-backend-only**; Metal (`-ngl > 0`) falls back to
the uncached path (or tensor_set re-repack) until a later phase measures
whether a Metal variant is worth it. This keeps Phase 6 scoped and the
oracle (CPU) authoritative.

The roadmap's "no per-step copy" question is answered by measurement:
per-step hit cost = memcpy of 8 slots × 3 kinds × ~1.4–2 MB ≈ ~30–40 MB per
layer-step ≈ ~2–4 ms/layer at memcpy bandwidth. If profiling shows the
memcpy dominating, a later phase can attempt buffer aliasing (loaded
tensor data pointing into the cache region) — deferred, not assumed.

### Insert path

Miss: pread as today → tensor_set as today → **read back the physical
slice bytes** from the loaded buffer (memcpy from `data + slot*stride`)
into the cache. Insert cost = miss cost + one readback memcpy (small).

### Self-check (stride discovery + correctness)

`KIMI_CACHE_SELFCHECK=1` (default off): on every hit, after memcpy
placement, re-run `tensor_set` from the staged raw bytes and byte-compare
the two physical regions; assert equal. This (a) verifies the per-slice
contiguity/stride assumption empirically on the first run, and (b) proves
hit placement is byte-identical to what the uncached path would have
produced — the cache is then bit-identical by construction, and the Phase 5
oracle (max|Δ| = 0 vs conventional) must still pass with the cache on.

Stride determination: `stride = ggml_backend_buft_get_alloc_size(buft,
loaded) / n_slots`; assert divisibility at runtime. (Conventional-path
`mul_mat_id` indexes the 256-expert parent by expert id, so the repacked
layout is per-expert contiguous by construction; the self-check proves it
for the loaded tensors.)

### Stats

Extended `llama_expert_stream_stats` (env-gated CSV or stderr, mirrors the
existing per-step stats):

- `n_cache_lookups`, `n_cache_hits`, `n_cache_misses`
- `cache_hit_bytes` (pread+repack bytes elided)
- `cache_evictions`, `cache_bytes_used`, `cache_budget_bytes`
- per-step `cache_saved_us` (estimated: hits × measured miss-cost split)

## Acceptance mapping (ROADMAP §Phase 6)

| acceptance | evidence |
|---|---|
| respects configured byte limit | budget accounting in stats; RSS check at budget vs budget+1 ladder rungs |
| hits and misses correct | self-check + oracle |
| evicts safely | LRU eviction stress (small budget → thrash); oracle still bit-identical |
| preserves bit-identical execution vs conventional oracle | Phase 5 A/B/C comparator with cache on (hits + misses exercised) |
| beats projection floor 1.42 → 2.12 tok/s (1–14.6 GB ladder) | ladder benchmark at 1, 2, 4, 6, 8, 10, 12, 14.6 GB; also report vs measured uncached intercept (~2.6 tok/s) and vs sim-predicted hit rates |

Projection note: the roadmap floor was computed from the Phase 4 miss-cost
estimate (417 ms SSD + 242 ms repack); the measured intercept is already
~2.6 tok/s uncached (pread is ~78 ms, not 417 ms). The ladder is expected
to clear the floor comfortably; the honest comparison is uncached-vs-cached
on the same binary, plus the sim's predicted hit-rate curve.

## Risks / open items

1. **Repacked stride assumption** — high confidence (kernel indexes experts
   by id in the conventional path), proven by self-check before the ladder.
2. **Physical readback cost** on insert — small memcpy, measured in stats.
3. **CPU-only direct placement** — Metal falls back to uncached; acceptable
   for Phase 6 (oracle is CPU; roadmap defers Metal work).
4. **Memory accounting honesty** — budget uses measured physical bytes;
   the ~2.0 GB streamer baseline is the zero-cache intercept, so any
   measured increase is cache residency (the Phase 4E close's stated
   purpose).
5. **Coalesced mode** — Phase 6 cache works on the naive path (the oracle
   mode); coalesced can adopt it later via the same hooks.

## Steps

1. Implement cache (storage, LRU, hit/miss, stats, env gating) in
   `llama-expert-stream.h/.cpp`.
2. Self-check run (10 tokens, cache on, `KIMI_CACHE_SELFCHECK=1`) → stride
   verified, hit placement byte-equal.
3. Oracle: cached (hits+misses) vs conventional, Phase 5 comparator.
4. Ladder benchmark 1–14.6 GB vs projection floor and uncached intercept;
   measured hit rate vs sim predictions.
5. `progress/phase-06-report.md` + commits.

---

## Implementation status (2026-08-14, during ladder2)

Implemented in llama.cpp (project commits, on top of `8b43eac45`):

- `llama-expert-stream.h/.cpp`: bounded repacked-expert cache (env
  `KIMI_EXPERT_CACHE_MB`, default 0 = off; `KIMI_CACHE_SELFCHECK`), LRU per
  (layer, expert) with gate/up/down moving together; vm-backed per-slice
  storage with a **per-size free pool** for evicted blocks (first cached
  runs showed ~100 ms/step of vm_allocate/vm_deallocate churn; the pool
  eliminates it — eviction recycles, fresh vm_allocate only when the pool
  is empty). **The pool is capped at budget/4**: an uncapped pool can hold
  ~8 GB of dead blocks in a thrash scenario, doubling residency (measured;
  the cap bounds resident cache memory to ≤ 1.25× budget).
- **Subset-repack placement**: the repack buft's `set_tensor` asserts
  `offset == 0 && size == ggml_nbytes` (full-tensor only, verified in
  `ggml/src/ggml-cpu/repack.cpp`), so per-slot partial sets are impossible.
  Misses are repacked one slice at a time through a single-slice temp
  tensor `[ne0, ne1, 1]` in the repack buft, then memcpy'd into the loaded
  tensor at `slot * slice_bytes`; hits are pure memcpy from the cache.
- `cache_verify_layout` (selfcheck): byte-compares the temp-slice repack
  against the full tensor_set region per slot — **PASSED** on first run,
  proving the per-slice contiguity/stride invariant
  (stride == slice_bytes == repacked size; `get_alloc_size` defaults to
  `ggml_nbytes` for the repack buft).
- Stats CSV extended with per-step cache columns
  (lookups/hits/misses/evictions/hit_bytes/bytes_used/budget).
- Two construction fixes found by the build+run loop:
  `ggml_init` params are `{mem_size, mem_buffer, no_alloc}` (not
  `{mem_size, no_alloc}`), and `GGML_MEM_ALIGN` is 16 KB in this build so
  every tensor object eats a padded 16 KB slot (temp ctx sized 1 MB).

### Correctness

- 10-token oracle (cached with hits+misses+evictions vs conventional):
  **OVERALL PASS**, max|Δ| = 0, 33,291/33,291 retrieval ranges byte-exact,
  router rows bit-identical. Re-verified after the free-pool change.

### Ladder 1 (hot machine, single pass, 64 tokens, ref prompt)

| capacity GB | tok/s | avg step ms | hit rate | sim steady |
|---|---|---|---|---|
| uncached | 1.62 | 618 | — | — |
| 1 | 1.78 | 561 | 0.337 | 0.327 |
| 2 | 1.44 | 697 | 0.466 | 0.443 |
| 4 | 2.06 | 485 | 0.582 | 0.582 |
| 6 | 1.83 | 547 | 0.688 | 0.675 |
| 8 | 1.61 | 623 | 0.765 | 0.742 |
| 10 | 1.65 | 606 | 0.834 | 0.792 |
| 12 | 1.95 | 513 | 0.886 | 0.848 |
| 14.6 | 1.22 | 821 | 0.917 | 0.884 |

**Diagnosis**: hit rates validate the sim's per-capacity predictions
(within ~1-3 points); the timings are dominated by thermal drift on the
fanless Air (back-to-back runs, load ~4; 14.6 GB measuring 821 ms is
physically impossible for a cache that elides 92% of I/O). Ladder 2
re-runs on a cooled machine in reversed order with min-of-2 per rung;
uncached control on the cool machine measures 406 ms/step (2.46 tok/s).

### Known residual (timing accounting)

Hit placement memcpy (~624 slices × ~1.4 MB ≈ 880 MB/step) is the
roadmap's "no per-step copy" question: it currently runs at ~15 GB/s
(~60 ms/step at 100% hits) and is the practical ceiling for this hit
path. A future phase could alias loaded tensors to cache storage to
remove it; Phase 6 keeps the memcpy (safe, CPU-backend-only).
