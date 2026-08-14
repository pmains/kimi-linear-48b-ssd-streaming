# Phase 4D Report — Pageable Model Loading

## Status

**PARTIAL** — with the split clearly measured:

- **Correctness: PASS.** Streamed execution with virtualized routed-expert
  parents is bit-identical to conventional inference (all three oracle
  comparisons, max|Δ| = 0), and the hard buft-identity requirement holds:
  loaded experts still allocate in the CPU_REPACK buffer type.
- **Memory (acceptance 2): PARTIAL.** The routed-expert collection is no
  longer materialized at load — backend allocations prove it: the repack
  buffer drops from **28,356 MiB to 1,153 MiB** (26.56 GiB of expert weights
  never copied or repacked). However, process peak RSS is **not yet lower
  than conventional**: the streamed executor's own runtime allocations
  (per-step scheduler pools + allocator-retained buffer churn) add ~1 GB
  steady-state and a ~5-6 GB prefill-time peak, offsetting the weight
  savings at the process level.

Phase 4 therefore remains PARTIAL on acceptance item 2; the remaining gap is
precisely scoped below (streamed-runtime residency, not model residency).

## Objective

Eliminate resident routed-expert weights: modify model loading (streamed
mode only, env-gated) so the routed-expert parent tensors are never
materialized into the normal resident buffers, while retaining their
metadata and backing-file information for the streamer. The target pipeline:

    GGUF → load trunk/shared weights normally → skip resident routed experts
         → pread selected experts → repack into the correct CPU buffer type
         → compute → bit-identical output

## Changes

### llama.cpp

- `src/llama-expert-stream.h` — shared Phase 4D helpers:
  `llm_is_routed_expert_tensor_name()` (identifies the 3-D
  `blk.N.ffn_{gate,up,down}_exps.weight` routed-expert parents) and the
  existing `llm_stream_experts_enabled()` declaration moved here so both the
  model loader and the streamer share one gate (`KIMI_STREAM_EXPERTS`).
- `src/llama-model.cpp` (`llama_model_base::load_tensors`, backend-buffer
  allocation loop) — **Phase 4D virtualization branch** (streamed mode only,
  real load only, inert when `KIMI_STREAM_EXPERTS` is unset):
  - For the context containing routed experts (the CPU_REPACK context on
    ARM), each expert tensor's `data` is pre-set to a dummy byte before
    `ggml_backend_alloc_ctx_tensors_from_buft`, which skips tensors with
    `data != NULL` — so the repack buffer is sized and allocated for the
    **trunk tensors only** (243 quantized trunk tensors stay in the repack
    buffer and are repacked exactly as before, preserving bit-identity of
    trunk matmuls).
  - The 78 expert tensors are assigned a **0-size dummy buffer from the same
    (CPU_REPACK) buft** — `ggml_backend_buffer_get_type` still returns
    CPU_REPACK, so `load_layer` keeps allocating loaded experts in the repack
    buft (the hard bit-identity requirement; a null buffer would fall back to
    the default buft and re-seed the ~1e-8 repack-layout divergence).
  - The context stays in `ctx_buf_maps` so `load_all_data` repacks the trunk;
    the expert tensors are skipped there (below).
  - The `no_alloc` fit probe (`common_fit_params` at CLI startup) is
    excluded from the branch, so the probe still reports full model sizes and
    keeps its one-buffer-per-context invariant.
- `src/llama-model-loader.cpp` (`load_all_data`) — in streamed mode, skip
  routed-expert tensors by name: their bytes are never copied or repacked at
  load. Inert when streaming is disabled.
- Diagnostics (KIMI_STREAM_DEBUG-gated): per-context tensor/expert counts,
  the virtualization summary line (`virtualized 78 experts ... 26.56 GiB not
  materialized; trunk buf size=1153.63 MiB`), and per-buffer allocation
  sizes (`[4d] buffer <buft> size=...`).

No caching, eviction, prefetching, async I/O, or executor changes were made.
The conventional load path is byte-unchanged when streamed mode is disabled
(verified: conventional run with the new binary materializes the same
28,356 MiB repack buffer and produces identical output).

## Results

### Buft identity (hard requirement) — PASS

```
[load] il=1 alloc (buft=CPU_REPACK)
[load] il=2 alloc (buft=CPU_REPACK)
[load] il=3 alloc (buft=CPU_REPACK)
```

Loaded experts still allocate into the same CPU_REPACK buffer type used by
conventional inference. The bit-identical oracle below is the confirming
evidence (the repack layout determines accumulation order).

### Virtualization — PASS

```
[4d] virtualized 78 experts in buft=CPU_REPACK (26.56 GiB not materialized; trunk buf size=1153.63 MiB)
```

### Correctness oracle (A/B/C, same standard as Phase 4/5)

| comparison | prompt | tokens | retrieval | router rows | activations/logits | text |
|---|---|---|---|---|---|---|
| stream-10 vs baseline-conv | phase-04-ref | 10 | 36,288/36,288 exact | 1,512 bit-identical | max\|Δ\|=0 | identical |
| stream-64 vs phase-05-conv-long | phase-04-ref | 64 | 69,984/69,984 exact | 2,916 bit-identical | max\|Δ\|=0 | identical (extracted text) |
| stream-alt-32 vs phase-05-alt-conv | phase-03-reasoning | 32 | 169,416/169,416 exact | 7,059 bit-identical | max\|Δ\|=0 | identical |

All captures ran under the virtualized loader (`KIMI_STREAM_EXPERTS=naive`,
`-ngl 0`, CPU). Conventional references are the pre-existing Phase 4/5
captures at `60dc29e64` (deterministic, `--temp 0 --seed 1`).

### Memory

**Backend allocations (the definitive model-residency evidence):**

| config | CPU buft | CPU_REPACK buft |
|---|---|---|
| conventional (env unset) | 305.71 MiB (+ mmap span 28,040 MiB) | **28,356.13 MiB** |
| streamed 4D | 305.71 MiB | **1,153.63 MiB** + 0.00 MiB dummy |

The routed-expert collection (26.56 GiB) is no longer allocated, copied, or
repacked at load. Trunk (attention/SSM/FFN weights) is allocated and repacked
identically to conventional.

**Peak RSS (`/usr/bin/time -l`, 10-token ref prompt, same workload):**

| config | mode | max RSS |
|---|---|---|
| conventional | mmap | 11.79 GB |
| conventional | --no-mmap | 8.67 GB |
| streamed-with-resident-parents (pre-4D binary `60dc29e64`) | mmap | 17.98 GB |
| streamed 4D | mmap | 12.07 GB (page-cache polluted; see below) |
| streamed 4D | --no-mmap | 10.68 GB |

**Steady-state decode RSS (ps sampling, 64-token, --no-mmap):**
conventional ~4.6-4.9 GB; streamed 4D ~5.0-5.4 GB.

**Measurement caveats (important):**
- On this 24 GB machine, macOS memory compression masks conventional's real
  footprint: the 28 GB repack heap is written then aggressively compressed
  (vmmap on the 4D run shows 6.4 GB swapped out under pressure). `ru_maxrss`
  understates conventional; on a system without memory pressure conventional
  would show ~30 GB resident and 4D ~5 GB.
- mmap mode is additionally polluted by the 30 GB `MADV_WILLNEED` file
  prefetch (`llama_model_loader::init_mappings(prefetch=true)`), which faults
  the whole GGUF into the page cache and attributes shared file pages to
  process RSS. `--no-mmap` runs avoid this and are the cleaner RSS signal.
- The 4D run's `--no-mmap` peak (10.68 GB) is dominated by **streamed-runtime
  allocations, not model weights**: vmmap shows model buffers at 1.46 GB
  total, with the remainder in per-step scheduler pools and
  allocator-retained freed buffer space (MALLOC_LARGE (empty) ~1.2 GB
  resident; 16 GB total writable virtual, 10.8 GB written over the run).

## Problems

1. **Acceptance 2 (RSS measurably lower) is not met at the process level.**
   Model-weight residency is eliminated (backend allocations: 28,356 →
   1,153 MiB), but the streamed executor's runtime footprint (~1 GB
   steady-state above conventional; ~5-6 GB prefill peak) keeps process RSS
   ≥ conventional. The runtime footprint is out of Phase 4D scope (it is an
   executor/scheduler allocation concern, not a loader concern). Remaining
   work: reduce streamed-runtime residency — per-step scheduler pool sizing
   and per-layer expert-buffer churn (allocator retention). This is a
   candidate small follow-up before Phase 6, or the first Phase 6 question
   once Phase 4 closes.
2. **Pre-existing benign message:** `llama_expert_streamer: failed to
   allocate loaded ids buffers` appears exactly once per streamed run (also
   present in Phase 5 logs at `60dc29e64`; not a 4D regression). The runs
   complete and pass the oracle; the boundary case (final-layer output-selection
   ids allocation) is handled by the existing fallback path.
3. mmap-mode max RSS is not a reliable comparison metric on this machine
   (compression + file prefetch); see caveats above.

## Decisions

- **Virtualize per-tensor inside the repack context** (trunk stays repacked;
  only the 78 routed experts go virtual) rather than diverting experts to a
  separate context: trunk matmuls must keep the identical repacked layout as
  conventional or the ~1e-8 repack-layout divergence returns.
- **Preserve buft identity via a 0-size dummy buffer from the same
  (CPU_REPACK) buft** — `ggml_backend_buft_alloc_buffer(buft, 0)` returns a
  valid meta buffer whose `buft` is the repack buft, so
  `ggml_backend_buffer_get_type(t_up->buffer)` in `load_layer` is unchanged.
- **Exploit the allocator's data!=NULL skip** (`ggml_backend_alloc_ctx_tensors
  _from_buft` sizes and allocates only tensors with `data == NULL`) to size
  the repack buffer for trunk only — no custom allocation math, no new
  buffer-type plumbing.
- **`load_all_data` skips experts by name** (env-gated) so no byte of the
  expert collection is ever copied or repacked at load.
- **One gate (`KIMI_STREAM_EXPERTS`) controls both halves** — streamed
  execution and expert virtualization must move together; there is no valid
  mixed mode.
- **The no_alloc fit probe is untouched** — it must keep reporting full model
  sizes (its `memory_breakdown` path also asserts one buffer per context).

## Next Phase

- Close acceptance 2: reduce streamed-runtime residency. Measured targets:
  steady-state decode RSS below conventional (~4.5 GB), peak RSS during
  prefill below ~8 GB. Likely levers (Phase 6-adjacent): size the per-step
  scheduler pools to the decode graph rather than the prefill graph; reuse
  per-layer expert buffers instead of alloc/free churn (malloc retains ~1.2 GB
  of freed space); consider bounded staging buffers for pread.
- Once acceptance 2 closes, Phase 4 is PASS, Phase 5 becomes authoritative
  (re-validated here under the virtualized loader), and Phase 6 can start as
  a pure performance phase (repacked-expert cache).

## Reproduction

```bash
# streamed 4D captures (traces + peak RSS)
tools/phase04d_run_streamed.sh benchmarks/results/phase-04d/stream-10 benchmarks/prompts/phase-04-ref.md 10 1 naive
tools/phase04d_run_streamed.sh benchmarks/results/phase-04d/stream-64 benchmarks/prompts/phase-04-ref.md 64 1 naive
tools/phase04d_run_streamed.sh benchmarks/results/phase-04d/stream-alt-32 benchmarks/prompts/phase-03-reasoning-pumps.md 32 1 naive

# oracle comparisons (conventional references at 60dc29e64)
python3 tools/phase04_compare.py benchmarks/results/phase-04d/baseline-conv/act.bin benchmarks/results/phase-04d/baseline-conv/moe.csv \
    benchmarks/results/phase-04d/stream-10/act.bin benchmarks/results/phase-04d/stream-10/moe.csv benchmarks/results/phase-04d/stream-10/retr.csv
python3 tools/phase04_compare.py benchmarks/results/traces/phase-05-conv-long/act.bin benchmarks/results/traces/phase-05-conv-long/moe.csv \
    benchmarks/results/phase-04d/stream-64/act.bin benchmarks/results/phase-04d/stream-64/moe.csv benchmarks/results/phase-04d/stream-64/retr.csv
python3 tools/phase04_compare.py benchmarks/results/traces/phase-05-alt-conv/act.bin benchmarks/results/traces/phase-05-alt-conv/moe.csv \
    benchmarks/results/phase-04d/stream-alt-32/act.bin benchmarks/results/phase-04d/stream-alt-32/moe.csv benchmarks/results/phase-04d/stream-alt-32/retr.csv

# buft identity + virtualization evidence
KIMI_STREAM_EXPERTS=naive KIMI_STREAM_DEBUG=1 ... llama-cli ... 2>&1 | grep -E "alloc \(buft=|virtualized"

# clean RSS (no-mmap) with per-buffer allocation sizes
KIMI_STREAM_EXPERTS=naive KIMI_STREAM_DEBUG=1 /usr/bin/time -l llama-cli -m <gguf> -ngl 0 --no-mmap -p "$(cat benchmarks/prompts/phase-04-ref.md)" -n 10 --temp 0 --seed 1 --no-display-prompt --no-conversation --single-turn < /dev/null
```

Environment: Apple M5, 24 GB unified memory, macOS 26.5.2; llama.cpp at
`60dc29e64` + Phase 4D changes (commit below); model
`moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`.
