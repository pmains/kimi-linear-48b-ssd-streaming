# Phase 6B Design — Zero-Copy Expert Cache

Status: DRAFT (2026-08-14). Defined after Phase 6 PARTIAL close
(`812fe40`), per the Phase 6 report's measured finding and the roadmap's
deferred "advanced streaming optimization" question.

## Objective (narrow, per Phase 6B definition)

Determine whether `mul_mat_id` can consume repacked expert slices
**directly from persistent cache-backed storage**, eliminating the
measured ~890 MB/token placement copy (Phase 6: 246–486 ms/step of
`copy_us`; the entire cache budget's worth of I/O savings) while
preserving:

- bit-identical output vs conventional inference (the Phase 5 oracle
  bar: max|Δ| = 0);
- bounded cache residency (the configured `KIMI_EXPERT_CACHE_MB` budget).

If it works, rerun the ladder. That result decides whether the original
caching thesis produces a throughput win.

## Feasibility — verified at the source (2026-08-14)

The question is whether a loaded tensor whose `ne[2]` (slot capacity)
exceeds the per-step used-slot count, with slot regions holding the
correct experts' repacked bytes in arbitrary (stable) slot numbers,
computes bit-identically to today's compact per-step layout. Verified
against `llama.cpp` `8b43eac45` + Phase 6 changes:

1. **The repack kernel is ids-driven, not slot-driven.**
   `ggml/src/ggml-cpu/repack.cpp` `forward_mul_mat_id` (the kernel that
   actually runs for these weights) groups work by ids entries
   (`MMID_MATRIX_ROW`, per-slot row counts) and skips unused slots
   (`if (cne1 == 0) continue;`). Each output element is produced by one
   independent `vec_dot` over the src0 slice selected by the id value.
   Slot index *values* never enter the numerics; the accumulation order
   per element is fixed by the slice's repacked bytes and the vec_dot
   type path — identical for any slot numbering. The generic
   `ggml-cpu.c` `ggml_compute_forward_mul_mat_id` has the same
   structure.

2. **The output dimension is the ids dimension, not the capacity.**
   `ggml_mul_mat_id` dst rows are keyed by id *position*
   (`{id, iid1}` mapping: position-in-ids, token), i.e. the output's
   expert dimension is `n_used` = `ids->ne[0]`, independent of src0's
   `ne[2]`. Downstream (`build_moe_ffn_compute` in `src/llama-graph.cpp`:
   `ggml_mul(experts, weights)` with weights `[n_used, n_tokens]`,
   per-occurrence views `i * experts->nb[1]`, Σ over
   `hparams.n_expert_used`) touches only occurrence rows. **Slots beyond
   the ids are never read** — stale bytes there are harmless.

3. **Thread partition is count-deterministic.** Chunking per slot group
   depends on (src0 `ne01`, ids-row count for that slot) — identical for
   used slots regardless of total capacity; every output element is
   written exactly once by exactly one thread.

Consequence: a persistent loaded tensor `[ne0, ne1, S_layer]` in the
repack buft whose slot regions contain the correct experts' repacked
bytes computes bit-identically to today's compact `[.., .., n_slots]`
tensor, provided (a) slot ids < `S_layer` (kernel assert), and (b) each
used slot holds that expert's repacked bytes. Phase 6 already proved the
per-slice contiguity invariant (stride == slice_bytes; self-check
PASSED), so the persistent tensor is the same shape family.

**Verdict: feasible.** The design below is the minimal change that
realizes it.

## Design: persistent-slot cache — the cache IS the working tensor

### Current (Phase 6) hit path — the measured problem

    cache block ──memcpy──▶ freshly allocated per-step loaded tensor ──▶ mul_mat_id
    (per-entry vm-backed blocks, ~890 MB/step of memcpy + page fault-in)

### Target (Phase 6B)

    persistent per-layer loaded tensor [ne0, ne1, S_layer] (repack buft, allocated ONCE)
        │  slot regions ARE the cache entries
        ├── HIT  → nothing to place; slot already holds the expert's repacked bytes
        └── MISS → pread → tensor_set(single-slice temp) → memcpy temp→slot (~1.3–1.9 MB)
        │
        ▼
    mul_mat_id reads the persistent tensor directly (zero copy)

### Cache structure

- **Storage**: persistent per-(layer, kind) loaded tensors, created once
  in a streamer-owned `ggml_context` (same mechanism as `temp_ctx_`),
  allocated from the repack buft (`expert_buft`, the Phase 4 bit-identity
  requirement). `S_layer` slots per layer; `nb[2]` = slice_bytes
  (Phase 6 self-check-proven contiguous layout).
- **Entry**: key `(layer, expert)` → slot number in that layer's three
  tensors (up/gate/down move together, as in Phase 6) + LRU list node.
- **Budget**: `Σ_layers S_layer × (2×1,327,104 + down_bytes(il)) ≤
  KIMI_EXPERT_CACHE_MB`. Per-layer slice bytes (measured, retrieval CSV):
  up/gate 1,327,104 B (Q4_K) all layers; down 1,935,360 B (Q6_K, 13
  layers) or 1,327,104 B (Q4_K, 13 layers). `cache_bytes_used` becomes
  exact and constant (= the allocation); the Phase 6 free-pool machinery
  (per-size pool, budget/4 cap, vm_allocate churn) **disappears entirely**
  — eviction is pure bookkeeping (a freed slot is reused by the next
  insert; zero memory operations).
- **S_layer floor**: max measured per-step unique experts per layer = 9
  (`kimi_retrieval.csv`, decode); S_layer ≥ 9 guarantees every decode
  step fits. At 1 GB budget: 9 slots/layer ≈ 1.003 GB decimal — fits.
- **Insert (miss)**: pread (naive, oracle mode) → `tensor_set(temp_[kind],
  slice)` (repack) → memcpy `temp->data` → slot region → mark occupied.
  Same operations as today's miss path minus the separate cache block.
- **Per-step work on hits**: build the occurrence → slot mapping and
  `slot_ids` (host-side, `n_used` ints) + sync. No weight bytes move.
- **Prefill fallback**: prefill steps route ~90 unique/layer, exceeding
  per-layer capacity at ≤ 8 GB budgets. When `n_slots > S_layer[il]`,
  that `load_layer` call falls back to the legacy per-step placement
  path (today's behavior) — correct, one-time cost per prompt. Decode
  (≤ 9 unique/layer) always uses the zero-copy path.
- **Gating**: `KIMI_EXPERT_CACHE_MODE=zerocopy` (default `placement` =
  Phase 6 behavior, byte-identical to today). Preserves the Phase 6
  cache as the reference path; both are env-gated, both default off
  (uncached streamer remains the oracle default).
- **CPU-only** (as Phase 6): the repack buft is the CPU path; Metal
  falls back to the uncached path. Unchanged scope.

### Expected economics (from Phase 6 measured numbers)

Decode step floor ≈ route 33 ms + expert compute 17 ms + build 26 ms +
sync 8 ms ≈ 84 ms, plus miss-only costs:

| capacity | hit | miss/step | step ≈ | tok/s ≈ |
|---|---:|---:|---:|---:|
| 4 GB | 0.58 | ~370 MB → ~100 ms copy + ~50 ms pread | ~240 ms | ~4.2 |
| 8 GB | 0.77 | ~210 MB → ~60 ms + ~30 ms | ~180 ms | ~5.6 |
| 12–14.6 GB | 0.89–0.92 | ~70–90 MB → ~25 ms + ~15 ms | ~125 ms | ~8 |

(Estimates; memory pressure at the big rungs and thermal drift on the
fanless Air apply as in Phase 6. The honest bar remains cached-vs-
uncached on the same binary — acceptance re-baselined per the Phase 6
report's recommendation.)

## Verification plan

1. **Self-check**: extend `KIMI_CACHE_SELFCHECK` to the persistent
   layout — byte-compare temp-slice repack vs the slot region on insert
   (per-slot contiguity in the persistent tensor).
2. **Oracle**: zero-copy cache on (hits + misses + evictions), 10-token
   run vs conventional capture, Phase 5 comparator — must be OVERALL
   PASS, max|Δ| = 0, retrieval ranges byte-exact.
3. **Sim cross-check**: `tools/expert_cache_sim.py` already models
   per-layer LRU — run per-layer-capacity predictions per rung vs
   measured hit rate (expect a small delta vs the Phase 6 global-LRU
   table; decode traffic is layer-uniform, max 9 everywhere).
4. **Ladder**: 1/2/4/6/8/10/12/14.6 GB + uncached, ctx pinned 4096,
   min-of-2, reversed order (`tools/phase06_run_ladder.sh`); compare vs
   the Phase 6 ladder and the 2.63 tok/s uncached intercept.
5. `progress/phase-06b-report.md` + commit at phase end.

## Changes (smallest surface)

- `llama.cpp/src/llama-expert-stream.h/.cpp`:
  - persistent per-layer tensors + slot map + LRU in `zerocopy` mode;
  - per-call path selection (`n_slots ≤ S_layer[il]` → zerocopy, else
    legacy placement);
  - `cache_get`/`cache_put` reworked to slot bookkeeping (no block
    storage, no pool);
  - stats: `cache_hit_bytes` semantics = bytes elided from placement
    (hits place 0); keep `copy_us` honest (miss-slice copies only).
- `llama.cpp/src/llama-expert-stream-exec.cpp`:
  - `load_result.persistent` flag → skip the per-step
    `pending_expert_buf` free for persistent tensors (today the exec
    loop frees `load.up->buffer` deferred — must not free the
    streamer-owned persistent buffer);
  - `KIMI_EXPERT_CACHE_MODE` pass-through.
- `tools/expert_cache_sim.py`: per-layer-capacity ladder predictions
  (mostly present; wire the 6B partition).
- `progress/phase-06b-design.md` (this file), `ROADMAP.md` §Phase 6B.

## Risks / open items

1. **Per-layer capacity vs global LRU hit rate**: fixed per-layer
   partition loses the global pool's layer-skew flexibility. Measured
   decode traffic is layer-uniform (max 9 unique/layer everywhere), so
   the delta should be small — sim-predictable, then measured.
2. **S_layer floor vs prefill**: prefill uses the legacy fallback at
   ≤ 8 GB (correct by construction). At 14.6 GB (≈ 141 slots/layer)
   prefill fits in the persistent tensors and can use zero-copy too.
3. **Exec buffer-lifetime change**: the persistent buffer must not be
   freed per step (the deferred-free logic in `llama-expert-stream-
   exec.cpp`). Single-point change, flagged above.
4. **Big-budget memory pressure** (~17 GB resident + page cache at
   14.6 GB) unchanged from Phase 6; ladder protocol (ctx 4096 pin,
   min-of-2, reversed order) carried over.
5. **Metal** remains on the uncached path (CPU-only scope, as Phase 6).

## Definition of done (Phase 6B acceptance)

- Zero-copy hits measured at 0 MB/step placement (copy_us = miss-slices
  only) with the oracle still bit-identical.
- Ladder shows cached ≥ uncached at the rungs where hit rate × miss
  savings exceeds the memory-pressure tax (the re-baselined acceptance);
  the Phase 7 instrumentation phase proceeds only from this result.
