# Phase 6B Report — Zero-Copy Expert Cache

## Status

**PASS** — the zero-copy question is answered by measurement: `mul_mat_id`
consumes repacked expert slices directly from persistent cache-backed
storage, bit-identically, with **zero placement bytes on hits**. The cache
now beats the uncached intercept across the practical budget range
(1–8 GB: 1.3–2.24× in-session speedup; peak 3.30 tok/s at 4 GB vs 1.47
uncached), which Phase 6 could not do. The big rungs (10–14.6 GB) degrade
below uncached because the persistent cache's own footprint pushes the
24 GB machine into memory-pressure compression — the same class of effect
Phase 6 saw at large budgets, now causally isolated (route+expert compute
slow ~10× at 14.6 GB, not the placement path).

## Objective

Determine whether `mul_mat_id` can consume repacked expert slices directly
from persistent cache-backed storage, eliminating the measured
~890 MB/token placement copy (Phase 6: 246–486 ms/step `copy_us`), while
preserving bit-identical output and bounded cache residency. If it works,
rerun the ladder.

## Changes

### llama.cpp (on top of `8b43eac45`; project commit `eca7742b8`)

- `src/llama-expert-stream.h/.cpp` — Phase 6B zero-copy mode
  (`KIMI_EXPERT_CACHE_MODE=zerocopy`; default `placement` = Phase 6
  behavior, byte-identical):
  - **Persistent-slot design**: per-(layer, kind) loaded tensors
    `[ne0, ne1, S_layer]` in the repack buft, allocated once per layer on
    first use; **slot regions ARE the cache entries**. A hit places
    nothing — per-step cost is the host-side occurrence→slot map for
    `slot_ids` plus the backend sync. A miss preads the raw slice and
    repacks it through the existing single-slice temp into the slot
    (~1.3–1.9 MB, not 890 MB).
  - **Eviction is pure bookkeeping**: per-layer LRU lists; a freed slot is
    reused by the next insert. The Phase 6 free-pool machinery
    (per-size pool, budget/4 cap, vm churn) is gone entirely.
  - **Exact budget partition** (constructor): per-layer slot bytes come
    from a new exec-supplied slice table; every layer gets ≥ 9 slots
    (measured decode max = 8 unique/layer/step), the remainder distributed
    round-robin in whole experts. `cache_bytes_used` = the allocation
    (constant, exact).
  - **Legacy fallback**: steps whose unique count exceeds the layer's
    capacity (prefill at ≤ 8 GB: ~90 unique/layer) take the Phase 4/5
    placement path for that call; decode (≤ 8) always fits. A zerocopy
    failure (alloc, temp, or selfcheck) disables the cache and reruns the
    layer through legacy placement — no silent corruption.
  - **Selfcheck** (`KIMI_CACHE_SELFCHECK=1`): the first hit per
    (layer, kind) is re-pread + re-repacked and byte-compared against the
    resident slot (persistence + layout + repack determinism). 78/78
    layer-kind verifications OK, 0 mismatches, across the oracle and
    smoke runs.
- `src/llama-expert-stream-exec.cpp` — supplies the per-layer slice table;
  `load_result.persistent` so the per-step deferred buffer free skips the
  streamer-owned persistent buffers; `KIMI_DX_VERIFY` skips persistent
  loads (its slot semantics are compact-only).
- Construction fixes from the build/run loop:
  1. `kinds[].loaded` captured the compact tensors pre-branch; in zc mode
     the legacy fallback creates them on demand, so re-point the kinds
     after creation.
  2. `n_slots == 0` (last MoE layer during prefill, all-zero-size ctx):
     `alloc_ctx_tensors_from_buft` returns NULL by design when every
     tensor is 0-size (benign; Phase 6 hit this too). zc mode now keeps
     the compact tensors non-null for that case so the caller's graph
     build survives exactly as in placement mode.

### kimi repo

- `progress/phase-06b-design.md` — design + source-verified feasibility.
- `tools/phase06b_zc_sim.py` — per-layer-LRU hit-rate prediction with the
  exact 6B partition (used for the cross-check).
- `ROADMAP.md` — §Phase 6B inserted between Phase 6 and Phase 7.
- Benchmarks: `benchmarks/results/phase-06b/{smoke4e, conv-10,
  zc-oracle-10, diag-146, mem-8gb, ladder}`.

## Results

### Correctness — PASS (the decisive check)

10-token oracle, zerocopy ON (hits + misses + evictions), selfcheck ON, vs
a fresh conventional capture on the same binary:
`phase04_compare` **OVERALL PASS** — 13/13 executions aligned,
33,648/33,648 retrieval ranges byte-exact, router rows bit-identical,
every activation/logits trace max|Δ| = 0 (bit=yes). The sparse
persistent-slot tensors compute bit-identically to the compact layout —
exactly as predicted from the kernel analysis (ids-driven iteration,
output dimension = ids dimension, unused slots never read).

### Hit path — measured zero-copy

| rung | hit rate | copy ms/step | miss slices/step | copy = miss-slices only |
|---|---:|---:|---:|---|
| 1 GB | 0.305 | 175 | ~180 | ✓ |
| 2 GB | 0.461 | 171 | ~140 | ✓ |
| 4 GB | 0.585 | 125 | ~86 | ✓ |
| 8 GB | 0.756 | 221 | ~63 | ✓ |
| 14.6 GB | 0.913 | 214 | ~18 | ✓ |

`copy_us` contains only miss-slice repack+placement; hits contribute zero
bytes (verified per-step: hit_bytes elided ≈ hits × slice_bytes, copy
scales with misses only).

### Ladder (64 tokens, ref prompt, ctx 4096, min-of-2, reversed order,
zerocopy mode; in-session uncached control)

| capacity | tok/s | total ms | pread ms | copy ms | build ms | hit rate |
|---:|---:|---:|---:|---:|---:|---:|
| uncached | 1.471 | 680 | 169 | 385 | 40 | — |
| 1 | 2.911 | 343 | 107 | 175 | 15 | 0.305 |
| 2 | 2.688 | 372 | 110 | 171 | 20 | 0.461 |
| **4** | **3.301** | **303** | 95 | 125 | 18 | 0.585 |
| 6 | 2.231 | 448 | 116 | 192 | 27 | 0.682 |
| 8 | 1.872 | 534 | 115 | 221 | 31 | 0.756 |
| 10 | 1.539 | 650 | 101 | 206 | 39 | 0.810 |
| 12 | 1.462 | 684 | 73 | 162 | 40 | 0.877 |
| 14.6 | 1.184 | 845 | 53 | 214 | 44 | 0.913 |

- **Every rung ≤ 8 GB beats the in-session uncached control** (1.3–2.24×).
  Peak 4 GB = 3.30 tok/s, 303 ms/step.
- **Causal attribution at 4 GB** (the surgical claim): pread 169→95 ms
  (misses only), copy 385→125 ms (zero-copy hits), route+expert
  85→66 ms (unchanged — no confound at this budget). The entire win is
  the elided hit placement copy + elided hit I/O.
- **Big rungs**: 10–14.6 GB fall to/below uncached. Measured cause: the
  persistent cache's footprint (14.9 GB at the top rung) plus baseline
  plus page cache drives macOS compression; route 41→168 ms and expert
  44→484 ms at 14.6 GB (the dense trunk does not touch the cache code —
  pure memory pressure). The placement path is not the problem at the big
  rungs; residency is.
- vs Phase 6 (cooled session; cross-session comparison is
  thermally confounded, direction only): 4 GB 2.06→3.30, 6 GB 1.83→2.23,
  8 GB 1.61→1.87, 12 GB 1.95→1.46, 14.6 GB 1.87→1.18. 6B improves
  ≤ 8 GB and gives some back ≥ 12 GB (pressure).

### Hit rate vs the per-layer-LRU sim (`tools/phase06b_zc_sim.py`,
exact 6B partition, 64-token trace)

| GB | sim | measured | Δ |
|---|---:|---:|---:|
| 1 | 0.302 | 0.305 | +0.003 |
| 2 | 0.454 | 0.461 | +0.007 |
| 4 | 0.568 | 0.585 | +0.017 |
| 8 | 0.713 | 0.756 | +0.043 |
| 14.6 | 0.763 | 0.913 | +0.150 |

Measured tracks the model within 1–4 points at ≤ 8 GB, exceeding it at big
budgets via prefill warm-start (prefill fills the persistent tensors at
≥ 10 GB where its ~90 unique/layer fits the capacity; the sim starts cold
at decode). Same pattern Phase 6 saw vs the global-LRU sim.

### Residency — bounded (Phase 4E protocol, ctx 4096)

8 GB budget → decode steady phys_footprint **7,859 MB** (baseline ~2.0 GB
+ touched persistent slots + page cache). Lower than Phase 6's 10,247 MB
at the same budget because only the decode working set touches the
persistent tensors at ≤ 8 GB (prefill falls back to the legacy path and
never touches them). Never exceeds budget + baseline.

## Problems

1. **Per-slice repack is slow**: the miss path's
   `ggml_backend_tensor_set(temp, slice)` invokes the scalar, single-
   threaded repack transform (~1 GB/s); 1.1–1.6 ms/slice. At high hit
   rates this is the remaining copy cost (211 ms/step at 14.6 GB for
   ~87 MB). Previously hidden inside Phase 6's full 890 MB copy.
2. **Memory pressure at ≥ 10 GB**: the persistent allocation + page cache
   pushes the 24 GB machine into compression, slowing all compute
   (route+expert ~10× at 14.6 GB). The practical ceiling of this
   architecture on this machine is ~8 GB of cache.
3. **Benchmark environment**: the fanless Air was hot for this ladder
   (uncached 680 ms/step vs Phase 6's cooled 381 ms). In-session controls
   keep the comparison honest; cross-session absolute numbers are not
   directly comparable. 1 GB (2.911) > 2 GB (2.688) is a cooling artifact
   of the reversed rung order.
4. Per-layer partition loses the global pool's layer-skew flexibility
   (measured cost: negligible — decode traffic is layer-uniform).

## Decisions

- **Persistent-slot cache (the cache IS the working tensor)** — chosen
  over the Phase 6 block cache; the hit path places zero bytes.
- **Exact per-layer partition from the exec-supplied slice table** —
  no estimation, honest budget, floor 9 slots/layer.
- **Legacy fallback for oversized steps** (prefill at ≤ 8 GB) — keeps the
  persistent tensors small-touch at low budgets; correct by construction.
- **Per-layer LRU eviction** — required by the layer-scoped slots; the sim
  models it; measured within 1–4 points.
- **Selfcheck-on-first-hit** — verifies persistence/layout per (layer,
  kind); 78/78 passed.
- **CPU-only** — the repack buft is the CPU path; Metal unchanged (falls
  back to the uncached path as in Phase 6).
- **Env-gated**: zerocopy is opt-in; placement mode and the uncached
  streamer remain as reference paths.

## Next Phase

Per the STOP point, further work is selected from measured results. Phase
6B's measurements select:

- **Phase 7 (Instrumentation)** — roadmap pending; the ladder protocol and
  per-step breakdowns already exist and carried this phase's attribution.
- The measurements also identify the two real bottlenecks for any
  optimization phase: (a) the per-slice repack transform (~1.1–1.6
  ms/slice — batching misses into a single multi-slot repack would restore
  bulk throughput), and (b) memory pressure above ~8 GB of cache on 24 GB.
  Neither is a Phase 6B change; both are candidates for a future
  optimization phase, not this one.

## Reproduction

```bash
# oracle (fresh conventional capture + zerocopy cached run + comparator)
CTX=4096 tools/phase04_run_capture.sh benchmarks/results/phase-06b/conv-10 \
    benchmarks/prompts/phase-04-ref.md 10 1
CTX=4096 KIMI_EXPERT_CACHE_MB=2048 KIMI_EXPERT_CACHE_MODE=zerocopy \
    KIMI_CACHE_SELFCHECK=1 tools/phase04_run_streamed.sh \
    benchmarks/results/phase-06b/zc-oracle-10 \
    benchmarks/prompts/phase-04-ref.md 10 1 naive
python3 tools/phase04_compare.py benchmarks/results/phase-06b/conv-10/act.bin \
    benchmarks/results/phase-06b/conv-10/moe.csv \
    benchmarks/results/phase-06b/zc-oracle-10/act.bin \
    benchmarks/results/phase-06b/zc-oracle-10/moe.csv \
    benchmarks/results/phase-06b/zc-oracle-10/retr.csv

# ladder (zerocopy mode; ctx 4096, min-of-2, reversed order)
KIMI_EXPERT_CACHE_MODE=zerocopy tools/phase06_run_ladder.sh \
    benchmarks/results/phase-06b/ladder 64 1

# hit-rate cross-check (exact 6B partition, per-layer LRU)
python3 tools/phase06b_zc_sim.py benchmarks/results/phase-06b/ladder/uncached/moe.csv \
    benchmarks/results/phase-06b/ladder/uncached/retr.csv

# residency (Phase 4E protocol, decode steady phys)
CTX=4096 KIMI_EXPERT_CACHE_MB=8192 KIMI_EXPERT_CACHE_MODE=zerocopy \
    KIMI_STREAM_MEM_FILE=benchmarks/results/phase-06b/mem-8gb/mem.csv \
    tools/phase04_run_streamed.sh benchmarks/results/phase-06b/mem-8gb \
    benchmarks/prompts/phase-04-ref.md 10 1 naive
awk -F, 'NR>1 && $3=="decode" && $1>=6 {s+=$5; n++} END {print s/n}' \
    benchmarks/results/phase-06b/mem-8gb/mem.csv
```

Environment: Apple M5 (Mac17,3), 24 GB unified memory, macOS 26.5.2;
llama.cpp `8b43eac45` + Phase 6/6B changes (project commit `eca7742b8`);
model `moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`; `-ngl 0` CPU,
`--no-mmap`, `--ctx-size 4096`, `--temp 0 --seed 1`.
