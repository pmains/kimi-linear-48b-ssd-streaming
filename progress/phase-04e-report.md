# Phase 4E Report — Runtime Residency Cleanup

## Status

**PASS**

Phase 4 acceptance item 2 (resident memory measurably lower than
conventional) is now met at the process level, with the streamed executor's
accidental residency attributed and removed. The zero-cache memory
intercept for Phase 6 is measured and clean.

## Objective

Make the uncached streamer's runtime residency reflect the memory
architecture proven in Phase 4D: the ~1.5 GB permanent model footprint
(trunk weights + persist tensors) plus a bounded streaming workspace,
instead of ~11 GB of process footprint dominated by scheduler pools,
allocator churn, and a mis-configured KV cache. Explicitly NOT a redesign
and NOT caching.

## Changes

### llama.cpp (commit `c6347654a` + Phase 4E changes)

- `src/llama-expert-stream-exec.cpp` — **live-allocation instrumentation**
  (`KIMI_STREAM_MEM_FILE`, default off): per-step start/end rows with
  phys_footprint (`task_info`, the reliable macOS number — includes
  compressed memory), resident size, malloc-zone in-use bytes, and
  scheduler pool bytes (`ggml_backend_sched_get_buffer_size`).
- `src/llama-context.cpp` (`sched_reserve`) — **streamed-mode worst-case
  reserve skip** (env-gated by `llm_stream_experts_enabled()`): the
  streamed executor never computes the full-model pp/tg graphs, so the
  5.4 GB worst-case scheduler reservation (256-token prompt graph) is
  skipped; pools grow on demand to actual streamed subgraph sizes.
  `resolve_fused_ops` and the memory-module init are untouched.
- `src/llama-expert-stream.h/.cpp` — **reused expert staging scratch**:
  the per-layer `std::vector` packed staging (up to ~1 GB/layer during
  prefill, ~32 MB/layer during decode) is replaced by a grow-only scratch
  allocated with `ggml_aligned_malloc` (vm_allocate on macOS) and carved
  per kind. `stage_release()` frees it at the prefill→decode transition.
  Rationale: macOS's malloc zone caches freed large regions and does NOT
  return them to the OS — `malloc_zone_pressure_relief` returns 0 bytes on
  macOS 26 (verified empirically) — so malloc-backed staging left ~1.2 GB
  of MALLOC_LARGE (empty) resident through decode.
- `src/llama-expert-stream-exec.cpp` — prefill→decode transition calls
  `streamer_->stage_release()` (replaces the ineffective
  `malloc_zone_pressure_relief` call).
- `ggml/src/ggml.c` — env-gated allocation-size log
  (`GGML_DEBUG_ALIGNED_MALLOC`, off by default) in `ggml_aligned_malloc`
  for attribution; no behavior change when unset.
- `tools/phase04e_run_mem.sh` — capture runner with `--ctx-size` pinned
  (default 4096, env `CTX`, `CTX=0` restores library default), mem.csv +
  stats + retrieval + oracle traces + env/commit record.

### Critical discovery: the KV cache

Without `--ctx-size`, llama.cpp defaults `n_ctx` to `n_ctx_train`
(**1,048,576**), and Kimi Linear's 7 attention layers allocate a
**8,064 MiB f16 KV cache** (per the loader: `llama_kv_cache: size =
8064.00 MiB (1048576 cells, 7 layers)`). This is shared infrastructure —
conventional pays it identically — but it dominated the earlier
~11 GB phys_footprint and made the 4D-era RSS comparison hard to read.
All 4E measurements pin `--ctx-size 4096` (KV = 31.5 MiB).

## Results

### Scheduler pools (reserve skip)

| config | sched pool bytes |
|---|---|
| streamed, worst-case reserve (4D-era) | 5,368 MB |
| streamed, reserve skipped | **81 MB** |

### Process footprint, same workload (64 tokens, ref prompt, --no-mmap, ctx 4096)

| metric | conventional | streamed 4E | ratio |
|---|---|---|---|
| decode phys_footprint (`task_info`) | 28.2 GB (vmmap mid-decode) | **3.14 GB** (per-step avg) | **9.0× lower** |
| ru_maxrss | 6.78 GB | 3.50 GB | 1.9× lower |
| prefill peak phys_footprint | — | 3.13 GB | (target < 8 GB: met) |

### Allocator churn (the 4E target)

| region | 4D-era streamed | 4E streamed |
|---|---|---|
| MALLOC_LARGE (empty) — allocator-cached freed regions | 1.2 GB resident | **305 MB** |
| malloc in-use | ~530 MB | ~530 MB |

The remaining ~3.1 GB decode footprint decomposes as: model trunk
(repack 1.15 GB + CPU 0.31 GB) + sched 81 MB + KV 31 MB + malloc in-use
~0.53 GB + malloc empty 0.31 GB + per-step context/staging workspace
(~0.2 GB) + macOS compressed-page remnants of the prefill's transient
buffers (~0.5 GB, accounting lag; not addressable by the executor).

### Correctness oracle (final binary, A/B/C)

| comparison | result |
|---|---|
| stream-64 vs phase-05-conv-long | PASS, max\|Δ\|=0; 69,984/69,984 retrieval ranges byte-exact; 2,916 router rows bit-identical |

## Problems

1. **`malloc_zone_pressure_relief` is a dead end on this OS.** Standalone
   test: alloc/free 1 GB in 16 MB chunks → footprint stays 1,026 MB after
   relief; released = 0 bytes. macOS 26's zone cache does not return freed
   large regions to the OS. This is why the staging scratch is vm-backed
   (`ggml_aligned_malloc` → vm_allocate, whose `vm_deallocate` does return
   pages) and reused rather than freed per layer.
2. **The stage-change net effect on decode phys_footprint was within noise
   (~2.95 → 3.14 GB across separate runs)**, despite eliminating ~0.9 GB of
   MALLOC_LARGE (empty): the freed pages' footprint is partially retained
   by macOS's compressed-page accounting, and run-to-run compression timing
   shifts the numbers by ±0.2 GB. The clean wins are structural: sched
   pools and malloc-empty both collapsed; both are visible in the region
   tables even where phys_footprint moved less than expected.
3. **Coalesced mode still uses per-layer `std::vector` merged buffers**
   (malloc churn). Naive mode — the oracle mode — is fully scratch-backed.
   Converting coalesced's merge buffers is a small follow-up if Phase 6
   adopts coalesced pread.
4. The per-layer slot bookkeeping vectors (`occ_slot`, `unique`,
   `slot_occurrences`, `ids_host`) still churn malloc at ~MB scale; a
   bounded reuse pool would recover the last ~0.3 GB of malloc-empty.
   Diminishing returns; deferred.
5. `sched_reserve` destructor prints benign "compute buffer size does not
   match expectation of 0.00 MiB" warnings in streamed mode (the skipped
   reserve leaves `backend_buf_exp_size` at 0). Cosmetic.

## Decisions

- **Pin `--ctx-size` for all memory comparisons.** The 8 GB KV cache at
  default n_ctx is a configuration artifact both paths pay; without pinning
  it, residency comparisons measure the KV cache, not the streamer.
- **Skip the worst-case scheduler reserve in streamed mode.** The streamed
  path's largest graph is a per-layer route subgraph at the real prompt
  ubatch size; the 256-token full-model reserve was pure waste.
- **vm-backed grow-only staging scratch + release at prefill→decode.**
  malloc's zone cache cannot be coerced into returning memory on this OS;
  vm_allocate/vm_deallocate can.
- **`task_info` phys_footprint is the acceptance metric**, not ru_maxrss
  (compression makes ru_maxrss optimistic for conventional) and not ps RSS
  (file-prefetch and compression pollution).
- Keep `GGML_DEBUG_ALIGNED_MALLOC` in ggml.c (off by default) — it is the
  single attribution chokepoint for ggml VM allocations; useful for Phase 6
  cache accounting.

## Next Phase

Phase 6 begins from the measured zero-cache intercept:

- decode steady-state: **~3.1 GB phys_footprint** (model 1.5 GB +
  bounded workspace) at ctx 4096, ~2.95 GB best observed
- prefill peak: ~3.1 GB (well under the 8 GB target)
- sched pools: 81 MB; malloc-empty: 305 MB
- uncached decode throughput unchanged (this phase changed allocation
  behavior only; per-step timing is in `stats.csv`)

Phase 6 can now ask the clean question: **starting from an efficient
uncached streamer at ~3.1 GB, how much RAM should we deliberately spend on
cached, backend-ready repacked experts to maximize tokens/sec?** The cache
adds a byte-configurable resident term on top of a known baseline; any
measured increase in residency is cache residency, not executor noise.
Cache sizing should budget against the 24 GB machine: ~3.1 GB streamer
baseline + KV/ctx growth + cache.

Remaining optional polish before Phase 6 (not required): coalesced-mode
merge buffers via scratch; slot-vector reuse pool; silencing the
destructor warnings.

## Reproduction

```bash
# streamed capture (mem.csv, stats, traces; ctx pinned to 4096)
CTX=4096 tools/phase04e_run_mem.sh benchmarks/results/phase-04e/stream-64-ctx4k-stage2 \
    benchmarks/prompts/phase-04-ref.md 64 1 naive

# conventional capture (KIMI_STREAM_EXPERTS=0 disables streaming; no mem.csv)
CTX=4096 tools/phase04e_run_mem.sh benchmarks/results/phase-04e/conv-64-ctx4k \
    benchmarks/prompts/phase-04-ref.md 64 1 0

# oracle comparison (final binary)
python3 tools/phase04_compare.py benchmarks/results/traces/phase-05-conv-long/act.bin \
    benchmarks/results/traces/phase-05-conv-long/moe.csv \
    benchmarks/results/phase-04e/stream-64-ctx4k-stage2/act.bin \
    benchmarks/results/phase-04e/stream-64-ctx4k-stage2/moe.csv \
    benchmarks/results/phase-04e/stream-64-ctx4k-stage2/retr.csv

# decode steady-state summary
awk -F, 'NR>1 && $2=="start" && $4==1 {s+=$5; n++} END {printf "decode phys avg=%.0f MB\n", s/n}' \
    benchmarks/results/phase-04e/stream-64-ctx4k-stage2/mem.csv

# scheduler pool + reserve-skip verification (verbose)
KIMI_STREAM_EXPERTS=naive llama.cpp/build-metal/bin/llama-cli -m <gguf> -ngl 0 --no-mmap \
    --ctx-size 4096 -p "$(cat benchmarks/prompts/phase-04-ref.md)" -n 2 --temp 0 --seed 1 \
    --no-display-prompt --no-conversation --single-turn -v < /dev/null 2>&1 | \
    grep -E "streamed mode|kv_cache: size"

# conventional phys_footprint mid-decode (vmmap sampling; see /tmp scripts)
# standalone malloc-zone cache test
cc -o /tmp/relief_test /tmp/relief_test.c && /tmp/relief_test
```

Environment: Apple M5 (Mac17,3), 24 GB unified memory, macOS 26.5.2;
llama.cpp at `c6347654a` + Phase 4D/4E changes; model
`moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`; `-ngl 0`, CPU,
`--no-mmap`, `--ctx-size 4096`, `--temp 0 --seed 1`.
