# Phase 11 E1 Report — Metal Staging for Streamed Experts

## Status

PASS — with two documented limitations (see Problems).

The architectural question E1 was designed to answer is resolved:
the streamed Metal failure was a **fixable boundary bug**, not a
fundamental incompatibility of two-phase streamed execution with Metal.
With the env-gated fix (`KIMI_STREAM_METAL_STAGE=1`), streamed MXFP4
executes on Metal end-to-end: 64 tokens generated, clean exit, phase07
invariants PASS.

## Objective

E1's single architectural question (from `progress/phase-11-kickoff.md`):

> Is the Metal streaming failure a fixable tensor-staging boundary bug
> (streamed expert tensors never allocated on the Metal backend), or a
> fundamental incompatibility of the two-phase streamed execution with
> Metal?

Acceptance (unchanged from the kickoff):
1. No abort; process exits cleanly; generated text present.
2. phase07 per-step invariants PASS on the run.
3. Record decode tok/s and resident MB vs the frozen CPU run (64-token
   smoke, n=1 — descriptive, not a gate).

## Changes

- `llama.cpp/src/llama-expert-stream-exec.cpp` — **fix #1** (env-gated
  `KIMI_STREAM_METAL_STAGE=1`, default off = frozen path): guard the
  routing-ids readback. On the last MoE layer during prefill (before any
  output token is selected) the topk ids tensor is 0-element; the
  readback then calls `ggml_backend_tensor_get_async` with a NULL host
  destination, which the Metal backend rejects
  (`newBufferWithBytesNoCopy:NULL` -> `GGML_ASSERT(buf_dst)` at
  ggml-metal-context.m:359). The CPU backend treats a 0-byte get as a
  no-op, which is why the frozen CPU-only K1 runs never fired this.
- `llama.cpp/src/llama-context.cpp` — **fix #2** (env-gated the same
  way): free the streamed path's persistent activation/logits buffers
  (`stream_persist_`, allocated by `stream_init_persist` on the compute
  backend) in `~llama_context`. They were never released, so the Metal
  device teardown still held registered rsets and asserted
  (`ggml_metal_rsets_free`: `[rsets->data count] == 0`,
  ggml-metal-device.m:657). On the frozen CPU path this was a silent
  teardown leak; freeing it is behavior-neutral there.
- `llama.cpp/ggml/src/ggml-metal/ggml-metal-context.m` — temporary
  env-gated instrumentation added and then reverted (used only to name
  the NULL tensor: `ffn_moe_topk-26 (copy)`).
- No changes to model math, quantization, or the frozen K1 baseline.

## Results

Ground-truth reproduction with the current frozen tree first, then the
fix, then acceptance:

| run | config | result |
|---|---|---|
| pre-fix reproduction | streamed MXFP4, `-ngl 999`, zerocopy 4096 MiB, 64 tok | SIGABRT at first prefill: `GGML_ASSERT(buf_dst)`, ggml-metal-context.m:359 |
| instrumented reproduction | same + KIMI_STREAM_DEBUG | crash tensor named: `ffn_moe_topk-26 (copy)`, data=NULL, size=0 |
| plain-Metal control | qwen3-8b, `-ngl 999`, 8 tok | EXIT=0, no asserts (rules out a generic teardown bug) |
| **E1 Metal acceptance** | streamed MXFP4, `-ngl 999`, zerocopy 4096 MiB, 64 tok, `KIMI_STREAM_METAL_STAGE=1` | **EXIT=0**, no asserts, text generated, phase07 invariants **PASS (0 violations)** |
| CPU reference | streamed MXFP4, `-ngl 0`, same streamer config, 64 tok | EXIT=0, invariants PASS |

E1 Metal acceptance numbers (64-token smoke, n=1, descriptive):

- Prompt: 708.7 t/s | Generation: 24.3 t/s (llama-cli report).
- Maximum resident set size: 10.9 GB (within the 24 GB unified budget;
  the earlier non-streamed full-offload OOM is avoided because streamed
  expert memory is bounded).
- phase07 (streamer-internal): decode steady hit_rate 1.000 (zero-copy),
  SSD 0.00 MB/token decode steady, decode steady phys ≈ 2.05 GB
  (streamer measurement; total process RSS includes Metal).
- First fix-only smoke (before fix #2): 583.3/23.7 t/s, text generated,
  but teardown rsets assert — confirming fix #1 unblocked execution and
  fix #2 was required for a clean exit.

CPU reference numbers (same prompt/seed/config, `-ngl 0`):

- Prompt: 18.5 t/s | Generation: 6.4 t/s; max RSS 10.9 GB.
- Descriptive comparison on this smoke: Metal decode ≈ 3.8x the CPU
  decode (24.3 vs 6.4 t/s), prompt ≈ 38x (708.7 vs 18.5 t/s). This is
  n=1 and NOT a gate; no performance claim beyond this run.

## Problems

1. **Generated text differs between backends and is garbled on Metal.**
   The CPU reference produces a coherent LRU-cache response; the Metal
   acceptance run produces non-coherent output (llama-cli also reports
   its own `peg-native format` output check failure). Execution,
   invariants, and exit are clean, so E1's acceptance is met — but
   Metal-path output quality is NOT established. Candidate causes to
   test in E2/E4: MXFP4-on-Metal numerical differences in expert
   matmul/attention, routing divergence on the Metal path, or a
   logits/readback discrepancy. This is a quality gate for E4, not an
   E1 criterion.
2. **Trace capture aborts on Metal at load.** A Metal run with
   `KIMI_TRACE_ACT=1`/`KIMI_TRACE_MOE=1` (the phase07 capture env used
   by the frozen CPU harness) dies during load with SIGTRAP
   (EXIT=133), before any stream lines, with empty stats.csv. The
   stats/retr/mem/cache_layers capture files work on Metal; the
   act/moe trace capture does not. Remaining boundary, evidence for
   E1b.
3. **Kickoff's staging hypothesis is refuted by measurement.** The
   kickoff claimed loaded expert tensors are staged in CPU buffers
   ("model tensor buft, llama-expert-stream.cpp:403-410"). The fresh
   reproduction shows `[load] ... alloc (buft=MTL0)` — loaded experts
   and zero-copy layer buffers ARE Metal-allocated — and layers 1-25
   streamed compute succeeds on Metal. The actual first blocker was the
   0-size ids readback (problem above). The staging story was an
   inference from the retained crash log, not a measurement.

## Decisions

- Keep both fixes env-gated (`KIMI_STREAM_METAL_STAGE=1`), default off
  = the frozen path. Rollback = unset the env var / revert the commit.
- Fix #1 guard is placed at the readback site (skip the get when
  `n_ids == 0`) rather than changing the Metal backend: smallest change,
  CPU behavior byte-identical (a 0-byte get was already a no-op).
- Fix #2 (teardown free) is required for a clean Metal exit and is
  behavior-neutral on CPU; it is gated the same way for consistency and
  rollback simplicity.
- One commit per experiment (fork + report), frozen tree `caea707b7`
  remains the rollback anchor.

## Next Phase

- E1b candidate: Metal + trace capture (`KIMI_TRACE_ACT`/`KIMI_TRACE_MOE`)
  load-time SIGTRAP — needed before the frozen phase07 capture env can
  be used for Metal runs.
- E2 candidate: attack the measured repack bottleneck (239 ms/step of
  the 412 ms Q4_K_M decode step, 58%; the MXFP4 advantage is 94% repack)
  — storage-side pre-repacked layout vs NEON/SIMD repack vs MXFP4-style
  block layout, gated by `repack_us` collapse with invariants intact.
- Quality gate before any E4-style claim: diagnose the Metal-vs-CPU
  output difference (Problem 1) — PPL comparison on Metal vs the frozen
  K1 band (≤ +1.4% vs Q4_K_M).

## Reproduction

Reproduce the pre-fix crash (frozen behavior, no env gate):

    env KIMI_STREAM_EXPERTS=naive KIMI_EXPERT_CACHE_MODE=zerocopy \
      KIMI_EXPERT_CACHE_MB=4096 llama.cpp/build-metal/bin/llama-cli \
      -m models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
      -ngl 999 -c 4096 -p "$(cat benchmarks/prompts/phase-03-coding-lru.md)" \
      -n 64 --temp 0 --seed 7 --no-display-prompt --no-conversation --single-turn \
      < /dev/null
      # -> SIGABRT, GGML_ASSERT(buf_dst), ggml-metal-context.m:359

E1 Metal acceptance (env-gated fix on):

    env KIMI_STREAM_METAL_STAGE=1 KIMI_STREAM_EXPERTS=naive \
      KIMI_EXPERT_CACHE_MODE=zerocopy KIMI_EXPERT_CACHE_MB=4096 \
      KIMI_STREAM_STATS_FILE=e1/stats.csv KIMI_STREAM_RETR_FILE=e1/retr.csv \
      KIMI_STREAM_MEM_FILE=e1/mem.csv KIMI_STREAM_CACHE_LAYERS_FILE=e1/cache_layers.csv \
      llama.cpp/build-metal/bin/llama-cli -m <MXFP4 gguf> -ngl 999 -c 4096 \
      -p "$(cat benchmarks/prompts/phase-03-coding-lru.md)" -n 64 --temp 0 --seed 7 \
      --no-display-prompt --no-conversation --single-turn < /dev/null
      # -> EXIT=0, 64 tokens, invariants PASS via:
      python3 tools/phase07_summarize.py e1

CPU reference (same, `-ngl 0`, no env gate): 18.5/6.4 t/s, invariants
PASS — retained at `benchmarks/results/phase-k1/e1-cpu-ref/`.
