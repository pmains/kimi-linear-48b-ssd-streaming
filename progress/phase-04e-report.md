# Phase 4E Report — Runtime Residency Cleanup

## Status

**PASS** (revised by the Phase 4E Close addendum below — see "Revised
canonical numbers": the steady-state decode footprint is ~2.0 GB, not
3.1 GB, after fixing the staging-scratch release bug found by the two-pass
measurement).

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

---

# Phase 4E Close (addendum — 2026-08-14)

Status of the original report stands: **PASS**. This addendum covers the
pre-close measurement work: live-malloc attribution, a two-pass
in-one-process memory experiment, and the resulting staging-scratch bug
fix. It revises the residency numbers and the phase's headline.

## What was asked

Before closing 4E:

1. attribute the ~530 MB live malloc;
2. measure two consecutive inference passes in one process;
3. establish active floor / steady-state footprint / high-watermark
   residue as three separate quantities.

## Tooling added (kimi repo)

- `tools/phase04e_probe.cpp` — two-pass in-process memory probe. Loads the
  GGUF once (same flags as the 4E captures: `-ngl 0`, `load_mode=NONE`,
  `n_ctx=4096`), warms up like llama-cli, then runs two full prefill+decode
  passes in ONE process with `llama_memory_clear` between them. Logs
  per-call `phys_footprint / resident / compressed / malloc default-zone
  in-use / other zones / n_zones`. Optional `KIMI_PROBE_HOLD_MS` sleep
  (mid pass-2 decode) for external vmmap/malloc_history attachment, and
  optional `KIMI_PROBE_PURGE_GB` pressure test. Built standalone against
  the llama.cpp build tree (no llama.cpp cmake changes).
- `tools/phase04e_close.sh` — runner: two-pass probe + hold capture
  (vmmap summary/full, targeted malloc_history) + env/commit record.

## 1. Live-malloc attribution

Zone-level (probe CSV): the process has exactly **one** malloc zone; the
entire live malloc (~486 MB at decode) is in the default zone. Per-zone
splitting is therefore a no-op on this macOS.

Call-site attribution (`malloc_history -callTree` on a live held process,
MallocStackLogging=1):

| site | live bytes | share |
|---|---|---|
| `llama_context::sched_reserve` → `ggml_backend_sched_new` (396 MB malloc + 19.1 MB calloc: gallocr/hash-set pools) | ~415 MB | 85% |
| vocab (`llama_vocab::impl::load`, ~151k-token hash tables + pieces) | ~38 MB | 8% |
| `llm_graph_result` (KV-update result buffers) | ~16.5 MB | 3.4% |
| tokenizer BPE session | ~2.3 MB | 0.5% |
| streamer ctor + all remaining small allocations | ~14 MB | 3% |
| **total ≈ default-zone in-use** | **~486 MB** | 100% |

This resolves the earlier "~530 MB live malloc" line item: it is
**~85% scheduler graph bookkeeping**, not model or streamer state. Note
the scheduler's *malloc'd* pools (~415 MB) are separate from its
*compute-buffer* pools (81 MB, `ggml_backend_sched_get_buffer_size`) — the
original report's "scheduler 0.08 GB" only counted the latter. The
llama-cli path shows 531.6 MB in-use; the extra ~46 MB vs the probe is CLI
overhead (samplers, trace buffers, stdio, prompt state).

## 2. Two passes in one process — and a bug found

Probe runs two full prefill+decode passes in one process (memory cleared
between passes). With the then-current binary:

```
pass 1 decode steady: 3019.7 MB phys / 2540 MB resident / 486 MB malloc
pass 2 decode steady: 4032.3 MB phys / 3537 MB resident / 486 MB malloc
```

Pass 2 gained **+1012 MB phys, +997 MB resident** with malloc flat — and
12 GB of forced memory pressure did not reclaim it. A pointer-matched
trace of every `ggml_aligned_malloc`/`ggml_aligned_free` (new env-gated
`[vm-free]` print in ggml.c) showed the truth: **each prefill pass retains
exactly one ~547 MB vm buffer, freed with `size=0`** — and
`vm_deallocate(ptr, 0)` is a no-op.

Root cause (llama.cpp, project code): `src/llama-expert-stream.h`
declared

    extern "C" void ggml_aligned_free(void * ptr);

shadowing the real two-argument function. The missing size argument landed
as 0, so the grow-only staging scratch (sized to ~547 MB during prefill,
"released" at the prefill→decode transition) was **never actually freed**.
Each full re-prefill leaked another scratch. The 4E report's claim that
"releasing at the transition keeps steady-state decode at the bounded
workspace size" was silently false — `stage_release()` had been a no-op
since 4E introduced it.

Fix (`8b43eac45`): correct the declaration and pass the size in
`stage_get`'s grow path and `stage_release`.

After the fix, same probe:

```
pass 1 decode steady: 1974.0 MB phys / 1567 MB resident / 486 MB malloc
pass 2 decode steady: 1975.0 MB phys / 1566 MB resident / 486 MB malloc
```

**−1046 MB steady-state, and the per-pass growth is gone (+1 MB).** The
oracle still passes: `phase04_compare` vs conventional is bit-identical
(max|d| = 0 everywhere, 69,984/69,984 retrieval ranges byte-exact, 2,916
router rows identical).

## 3. The three quantities (fixed binary, probe, 64 decode steps/pass)

| quantity | definition | value |
|---|---|---|
| **active floor** | min phys_footprint across decode steps (compression-settled plateau) | **1,974 MB** |
| **steady-state footprint** | median decode phys_footprint | **1,975 MB** |
| **high-watermark residue** | prefill peak phys − steady-state; per-pass retention = pass2 − pass1 plateau | peak 2,517 MB → transient 542 MB, **retained residue ≈ 1 MB** |

Interpretation: the ~542 MB prefill peak transient (staging scratch +
per-layer loaded-expert buffers) is now genuinely released at the
prefill→decode transition. The residue is ~0; pass 2 lands on the same
plateau as pass 1. Before the fix, the residue was ~547 MB *per pass*.

## Revised canonical numbers (llama-cli path, tracing on, `fix-oracle` run)

| metric | 4E original | 4E close (fixed) |
|---|---|---|
| load | 1,929 MB | 1,929 MB |
| prefill peak | 3,133 MB | **2,566 MB** |
| decode steady-state | 3,139–3,163 MB | **~2,025 MB** |
| malloc in-use (decode) | 531.5 MB | 531.6 MB |
| sched compute pools | 81.3 MB | 81.3 MB |

The earlier "~1.2 GB unaccounted" is now itemized:

- ~0.55 GB: staging scratch never freed (size=0 free bug) — fixed;
- ~0.42 GB: scheduler malloc'd bookkeeping (sched_reserve/ggml_backend_sched_new) — real, persistent, previously mislabeled as part of the "0.08 GB scheduler";
- ~0.05 GB: malloc-empty allocator cache (MALLOC_LARGE (empty), ~299 MB virtual / ~21 MB resident + ~278 MB swapped at decode — mostly not resident once swapped);
- remainder: vocab/tokenizer/graph-result + CLI overhead, now itemized above.

Impact for Phase 6 cache budgeting: the streamer baseline is ~**2.0 GB**
(CLI) / ~1.97 GB (probe) at ctx 4096, not 3.1 GB. The ~0.5 GB of per-pass
growth risk for long-lived multi-prefill processes (server-style) is
gone.

## Problems

- The malloc-empty (MALLOC_LARGE (empty), ~299 MB) allocator-cache
  artifact remains; `malloc_zone_pressure_relief` still returns 0 on
  macOS 26 (unchanged from the original report). It is mostly swapped at
  decode but still charged to phys_footprint.
- `malloc_history` on macOS 26 requires an explicit mode argument
  (`-callTree` etc.); bare-address queries are rejected.
- The `-callTree -virtual` sizes include VM as well as malloc; the
  malloc-only breakdown above was read from the `_malloc_zone_*` leaves.
- "llama_expert_streamer: failed to allocate loaded ids buffers" appears
  in run.err for the 0-slot layer path (benign, present in prior passing
  runs); oracle confirms no functional impact.

## Decisions

- Fix the `ggml_aligned_free` signature in llama-expert-stream.h instead
  of adding a one-arg wrapper; the two-arg form is the real ABI and the
  size is required for `vm_deallocate` on macOS.
- Keep the env-gated `[vm-free]` print in ggml.c (default off) — it makes
  alloc/free pairing possible, which is how this bug was found.
- Keep `phase04e_probe` + `phase04e_close.sh` as the standard protocol
  for the three quantities going forward (Phase 6 budgeting should quote
  active floor, not raw phys_footprint).

## Reproduction

```bash
# build the probe (after building llama.cpp as usual)
g++ -O2 -std=c++17 -I llama.cpp/include -I llama.cpp/ggml/include \
    tools/phase04e_probe.cpp llama.cpp/build-metal/bin/libllama.dylib \
    -Wl,-rpath,$PWD/llama.cpp/build-metal/bin \
    -o llama.cpp/build-metal/bin/phase04e_probe

# two-pass probe, 64 decode steps per pass
./tools/phase04e_close.sh benchmarks/results/phase-04e/twopass-fix 64 0 0

# allocation trace (env-gated vm alloc/free log)
GGML_DEBUG_ALIGNED_MALLOC=1 KIMI_STREAM_EXPERTS=naive \
    llama.cpp/build-metal/bin/phase04e_probe <gguf> benchmarks/prompts/phase-04-ref.md 8 <outdir>

# malloc call-site attribution (hold + stack logging)
MallocStackLogging=1 KIMI_PROBE_HOLD_MS=90000 KIMI_STREAM_EXPERTS=naive \
    llama.cpp/build-metal/bin/phase04e_probe <gguf> benchmarks/prompts/phase-04-ref.md 32 <outdir> &
# while held: vmmap -summary <pid>; malloc_history <pid> -callTree -consolidateAllBySymbol -virtual

# oracle re-verification after the fix
python3 tools/phase04_compare.py benchmarks/results/traces/phase-05-conv-long/act.bin \
    benchmarks/results/traces/phase-05-conv-long/moe.csv \
    benchmarks/results/phase-04e/fix-oracle/act.bin \
    benchmarks/results/phase-04e/fix-oracle/moe.csv \
    benchmarks/results/phase-04e/fix-oracle/retr.csv
```

Commits: llama.cpp `8b43eac45` (fix + vm-free print); kimi repo commit for
tools/results/report. Results under `benchmarks/results/phase-04e/`:
`twopass-64` (pre-fix two-pass), `twopass-dbg2` (alloc/free trace),
`twopass-fix` (post-fix two-pass), `twopass-purge12` (pressure test),
`twopass-vmmap` (pass-2 vmmap), `malloc-attrib` (stack-logging
attribution), `fix-oracle` (post-fix capture + oracle).
