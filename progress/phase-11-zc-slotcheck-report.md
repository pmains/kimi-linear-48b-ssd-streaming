# Phase 11 — E2 Zerocopy Expert-Slot Integrity Check

## Status

**CLASS A — failure found, localized, NOT fixed.** The streamed Metal
path consumes the **wrong expert** for every routed position when
trace capture is disabled (the frozen E4 configuration): the router's
expert-id readback is an **unsynchronized async get**, so on Metal all
routed ids arrive as **0**. Every zerocopy slot then faithfully holds
and serves **expert 0's bytes**, and the graph computes expert 0 for
all 8 expert positions → garbage MoE output → **this is the E4 ×218k
PPL root cause**. The slot layer itself is internally consistent
(checksums all match, 0 failures in 183,936 occurrence checks) — the
defect is upstream at the **ids readback boundary**, not in the slot
machinery, not in any kernel, not in the arithmetic. Do NOT fix yet
per directive. Stopped for review.

## Objective

Determine whether the streamed Metal path ever consumes the wrong
expert bytes from a zerocopy/cache slot, by verifying the invariant
requested expert E → correct GGUF tensor/offset → correct bytes →
correct cache/zerocopy slot S → correct Metal address consumed for E,
across a representative streamed Metal run covering multiple layers,
experts, hits, misses, slot reuse, and eviction/replacement — with
cheap deterministic checksums, env-gated/default-off instrumentation.
Classify: A (wrong bytes → stop, report, don't fix), B (cleared to
coverage), or C (cannot establish reliably).

## Method

### Instrumentation (fork, env-gated `KIMI_DX_ZC_VERIFY`, default off)

- `llama-expert-stream.h/.cpp`: per-(layer, kind, slot) registry of the
  expert + FNV-1a64 checksum of the bytes placed in each persistent
  zerocopy slot; placement recording in `zc_place` Phase D (both E2
  direct and temp paths) and `zc_warm_tail`; per-occurrence consumption
  verification at the end of every `zc_place` (read back the actual
  `slot_ids` the graph will consume; verify slot_ids == assigned slot,
  registry expert == requested expert, current slot checksum == placed
  checksum; fresh-pread cross-check per (layer, kind, expert)); hard
  stop on first failure; teardown SUMMARY + per-layer DIAG.
- Driver `tools/phase11_zc_slotcheck_run.sh`: frozen streamed Metal
  config (llama-cli, phase-03-coding-lru.md, -n 64, seed 7, naive
  stream, zerocopy cache, Metal stage + E2 direct place, -ngl 999) +
  `KIMI_DX_ZC_VERIFY=1`. Runs at 4096 MiB and 1024 MiB (eviction-
  forcing; cap ≈ 10 slots/layer).

### Runs (all EXIT=0, 0 slot-verification failures)

| arm | occurrences checked | hits | misses | slot reuses | pread cross-checks |
|---|---:|---:|---:|---:|---:|
| 4096 MiB (trace off) | 183,936 | 182,712 | 1,224 | 0 | 78 |
| 1024 MiB (trace off) | 183,936 | 182,712 | 1,224 | 0 | 78 |
| 4096 MiB (trace ON, A/B) | 44,136 | 25,971 | 18,165 | 17,076 | 10,836 |

## Findings

### 1. Slot layer is internally consistent (as instrumented)

Every occurrence check passed: `slot_ids` readback matched the
assigned persistent slot, the registry expert matched the requested
expert, and the slot's current FNV-1a64 matched the checksum recorded
at placement — 183,936/183,936 checks, 0 failures, both trace-off
arms. The zerocopy machinery faithfully stores and serves exactly the
bytes it was asked for, across hits, misses, and (trace-on run) 17,076
slot reuses / evictions.

### 2. BUT the requested expert id is 0 for every position on trace-off Metal

The ROUTE diag (added to print the deduped routed ids the zc path
receives) shows, for the trace-off run, every layer: `unique=[0]`
(n_slots=1). FNV-1a64 verification: the DIAG's stored checksum for
`il=1 kind=0` equals the GGUF slice of **expert 0 exactly**
(`5ad8978eb300356f`). So the zc path received expert id **0** for
every routed position, loaded expert 0's bytes, and the graph consumed
them.

### 3. Trace-on A/B (same binary, same config, only `KIMI_TRACE_ACT=1`)

Real routing: `unique=[25,78,17,136,64,46,30,221]`,
`[151,226,14,74,129,109,65,76]`, `[9,126,167,237,17,35,111,153]` …;
18,165 misses and 17,076 slot reuses exercised (unlike the trace-off
runs where `n_slots=1` collapses the cache). Generated text is a
coherent "Concurrent LRU Cache" Rust implementation; the trace-off run
generates garbage (`后国的_值=…`, plus a peg-native format error).

### 4. Root cause: unsynchronized async ids readback on Metal

`llama-expert-stream-exec.cpp:589`:

    ggml_backend_tensor_get_async(backend, ids_out, ids_host.data(), 0, n_ids*4)

There is **no synchronize after this get_async** in the default path.
The only syncs are inside `llm_trace_moe_dump` / `llm_trace_act_dump`
(`llama-graph.cpp:67,106,187,224` — the E1b fix), and those functions
**return immediately when trace envs are unset**. So:

- trace OFF (E4, this check): the async Metal blit of the routed ids
  is still pending when `load_layer` consumes `ids_host` → ids read as
  zeros → expert 0 for every routed position → garbage logits.
- trace ON (localize/substitute/mxfp4 runs): the dump loops'
  `ggml_backend_synchronize` incidentally flushes the pending ids
  readback → real routing (expert 50 etc.) → all arithmetic probes
  were valid and the arithmetic is genuinely sound.
- CPU: `get_async` falls back to a synchronous copy (E1b note) → CPU
  always routes correctly → E4 CPU reference PPL 6.76.

This is the **E4 ×218k PPL explanation**: E4's Metal arm ran trace-off
(its driver sets only `KIMI_STREAM_METAL_STAGE` + `KIMI_STREAM_E2_
DIRECT_PLACE`), so it consumed expert 0 for all 8 expert positions in
every layer — a degenerate MoE that produces garbage logits. The
"Metal numerical defect" framing was wrong in a second sense: the
kernels and arithmetic were fine; the default streamed-Metal path was
feeding the router's selection to the loader as zeros.

## Problems

- The trace-off runs' `n_slots=1` collapsed the cache (all positions →
  expert 0), so slot reuse/eviction could not be exercised there; the
  trace-on A/B arm provided that coverage (17,076 reuses).
- The 78 pread cross-checks in the trace-off runs all verified expert
  0's slice (self-consistent); the FNV check against the GGUF proved
  this was real zeros, not an instrumentation decode error.

## Decisions

- **Classification A**: any requested expert maps to the wrong bytes —
  specifically, the *requested* expert id is 0 for every routed
  position on the trace-off streamed Metal path. First failing event
  is the very first routed position of the run (every layer, exec 2
  prefill): expected = router's real top-1 (e.g., expert 50 at il=1);
  actual = 0; slot/cache state preceding = empty/armed; likely
  boundary = **ids readback in `llama-expert-stream-exec.cpp` (async
  get without sync when trace disabled)**.
- The zerocopy slot layer is exonerated to the coverage checked (its
  invariant holds for the ids it is given). The wrong-bytes hypothesis
  is confirmed, but the defect is at the request boundary, not the slot
  boundary.
- E4 root cause identified: unsynchronized async ids readback on the
  streamed Metal path when trace capture is off. All earlier arithmetic
  probes remain valid (they ran trace-on with correct routing).
- **Do NOT fix yet** (per directive): the minimal fix would be a
  `ggml_backend_synchronize` after the ids get_async (mirroring the
  E1b fix), but no source change was made to the production path.

## Next Phase

1. Review: confirm the fix direction (add backend synchronize after
   the routed-ids get_async in `llama-expert-stream-exec.cpp`, or use
   the synchronous `ggml_backend_tensor_get`), then re-run E4 with
   trace off — expected Metal PPL → ≈ CPU reference.
2. Re-run this slot-integrity check after the fix with trace off to
   confirm real routing (non-zero unique) and the full
   hit/miss/reuse/eviction coverage.
3. E3 (compute optimization) remains not begun.

## Reproduction

    # fork: build with the env-gated instrumentation
    cd llama.cpp && cmake --build build-metal -j10

    # trace-off arms (E4 configuration; shows the defect: unique=[0])
    tools/phase11_zc_slotcheck_run.sh 4096
    tools/phase11_zc_slotcheck_run.sh 1024
    # -> benchmarks/results/phase-11/zc-slotcheck/verify-{4096,1024}/run.log

    # trace-on A/B arm (real routing; coherent text)
    env KIMI_TRACE_ACT=1 KIMI_TRACE_ACT_FILE=$PWD/benchmarks/results/phase-11/zc-slotcheck/verify-4096-traceon/act.bin \
      KIMI_STREAM_EXPERTS=naive KIMI_EXPERT_CACHE_MODE=zerocopy KIMI_EXPERT_CACHE_MB=4096 \
      KIMI_STREAM_METAL_STAGE=1 KIMI_STREAM_E2_DIRECT_PLACE=1 KIMI_DX_ZC_VERIFY=1 \
      llama.cpp/build-metal/bin/llama-cli -m models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
      -c 4096 -f benchmarks/prompts/phase-03-coding-lru.md -n 64 --temp 0 --seed 7 \
      --no-display-prompt --no-conversation --single-turn -ngl 999 < /dev/null \
      > benchmarks/results/phase-11/zc-slotcheck/verify-4096-traceon/run.log 2>&1

    # decisive evidence lines
    grep "ROUTE" benchmarks/results/phase-11/zc-slotcheck/verify-4096/run.log        # unique=[0]
    grep "ROUTE" benchmarks/results/phase-11/zc-slotcheck/verify-4096-traceon/run.log  # unique=[25,78,…]
