# Stage 6A.6 Blocker Fix: Grow-Only Temp-Context Pool Underallocation

## Status

PASS (defect characterized, fixed, unit-tested, and large-prompt regression
passed; Stage 6A.6 rerun result appended below)

## Objective

Reproduce and characterize the `ggml_new_object` compute-pool shortfall
around large-prompt decode/update_slots, determine why the expert-streaming
path underallocated the pool by 224 bytes, and fix only that defect —
without changing caching, KDA/KV behavior, prefix construction, OpenClaw
dispatch, or Stage 6A.6 acceptance semantics.

## Characterization (evidence-backed)

### Crash signature (Stage 6A.6 acceptance run, 2026-08-16, PID 58186)

```
ggml_new_object: not enough space in the context's memory pool (needed 1049168, available 1048944)
ggml/src/ggml.c:1813: GGML_ASSERT(obj_new) failed
-> SIGABRT: ggml_new_tensor_3d -> llama_expert_streamer::cache_ensure_temp
   -> zc_place -> load_layer -> process_ubatch_streamed -> decode ->
   server_context_impl::update_slots
```

Backtrace confirmed against the frozen live bundle by dylib UUID
(`runtime/live/bin/libllama.0.dylib` = BE77382B-..., matching the .ips
`llama-server-2026-08-16-131033.ips`).

### Arithmetic

- `struct ggml_object` = 32 B; this fork's `struct ggml_tensor` = 336 B;
  a no_alloc tensor object costs 32 + 336 = **368 B** in the pool.
- The crashing pool was sized `1 MiB + 368` (1,048,944 B) — the previous
  "1 MiB + one measured tensor" sizing, which the frozen bundle already
  contained (confirmed by disassembly: 2 MiB probe ctx, `ggml_used_mem`
  delta, `add x8, x8, #0x100, lsl #12`).
- Pool capacity: 1,048,944 / 368 = **2850 tensor objects**. At the crash,
  `cur_end` = 1,048,800 = 2850 x 368; the 2851st tensor needed 368 B with
  144 B free -> **short by exactly 224 B**.
- The plain 1 MiB pool (phase-07 live binary) showed the same defect one
  object earlier: `needed 1048800, available 1048576` (2849 objects, 2850th
  failed) — twice in the launchd lifecycle log (both SIGABRT, exit 134).

### Root cause

The streamer's repack temp context (`cache_ensure_temp`) is a **grow-only**
no_alloc ggml context: ggml never reclaims tensor objects, so every
type/shape transition permanently consumes a 368 B slot. The down expert
tensor alternates Q6_K/Q4_K **14x per layer pass** (blk.1-26 GGUF types:
Q6 at {1,2,3,6,9,12,15,18,21,23,24,25,26}, Q4 elsewhere), so each step
creates ~14-15 new objects whenever the layer has cache misses. A diverse
prompt misses every step, so the object count is **unbounded over the
process lifetime** (~15/step):

- acceptance run: 176 steps (task 0: 1 prefill + 175 decode) + ~14 steps of
  the 22,241-token eval -> ~2,850 objects -> crash at the 2851st.
- instrumented dev run confirmed the mechanism: up/gate (uniform Q4_K
  2304x1024) recreate exactly once each; down recreates on every Q6<->Q4
  transition; per-step counts 14-17 on miss-y steps and 0 on all-hit steps.
- synthetic garbage prompts do NOT reproduce (OOD tokens route to a small
  expert subset -> cache hits -> ~0 objects/step); real in-distribution
  text (Caveman context, code) does.

Any fixed pool size is therefore wrong by construction: sizing was the
defect, not the specific 224 B delta. The "+1 tensor" sizing merely moved
the crash from object 2850 to 2851.

### Fix (minimal, self-contained in the temp-context path)

In `cache_ensure_temp`, when the pool cannot fit another tensor object,
free the per-kind backend buffers, drop the stale tensor objects
(`ggml_reset`), and continue from an empty pool. Temps are transient repack
staging; nothing outside `cache_ensure_temp` references them between calls,
so the reset is invisible to every caller. No caching, KDA/KV, prefix, or
placement semantics change. The pool size is unchanged (`1 MiB + one
measured tensor`); it now only needs to cover the objects created between
two recycling points instead of the process lifetime.

## Changes

- `src/llama-expert-stream.cpp`: recycle-on-overflow in `cache_ensure_temp`;
  env-gated recreate logging now includes pool used/size.
- `src/llama-expert-stream.h`: document that the temp pool is a bounded
  cache with recycling.
- `tests/test-expert-stream-temp-ctx.cpp`: added recycled-pool regression
  probes (see Results).
- `tools/server/server.cpp` + `src/llama-expert-stream.cpp` BUG-001
  instrumentation (pre-existing uncommitted; committed together — same
  stability/temp-context investigation lineage).
- llama.cpp commit: `0a6b2df63`.

## Results

### Unit test (`test-expert-stream-temp-ctx`)

- Reproduces the exact 1 MiB boundary abort: `needed 1048800, available
  1048576` (the launchd crash signature).
- Reproduces the exact acceptance abort: `needed 1049168, available
  1048944` (the Stage 6A.6 crash signature).
- Recycled pool survives 2850x4+100 objects (4+ full pool lifecycles);
  the non-recycled control still aborts -> the probe would catch a
  non-recycled implementation.
- exit 0.

### Large-prompt regression (dev server, fixed binary, same PID)

Config identical to live (`KIMI_STREAM_EXPERTS=naive`,
`KIMI_EXPERT_CACHE_MB=4096`, `KIMI_EXPERT_CACHE_MODE=zerocopy`, ctx 32768),
real C++ code prompt, 200 generated tokens/request, two requests:

- 5,401 temp objects created (old binary aborts at 2,850).
- 1 recycle fired exactly at the boundary:
  `[temp] recreate kind=2 type=q4_K->q6_K ... temp_objects=2850
   pool_used=1048800/1048944` -> `[temp] pool exhausted; recycled`.
- Zero aborts, zero "not enough space" warnings, both requests HTTP 200.

## Problems

- The first synthetic regression prompt (repeated random words) routed to a
  small expert subset and produced ~0 objects/step after warming — not a
  faithful reproduction; real in-distribution text was required.
- The exact step count at the acceptance crash (~190) is inferred from the
  object budget (2850/15); the server log's ubatch boundaries and the
  launchd instance's earlier tasks are consistent with it, but the launchd
  instance's full task list was truncated by the lifecycle wrapper's
  40-line tail. This does not affect the fix's validity (unbounded growth
  is established directly by the instrumented recreate counts).

## Decisions

- Recycle-on-overflow instead of enlarging the pool: enlargement cannot
  work because the growth is unbounded (~15 objects/step, process lifetime).
- Keep the pool at 1 MiB + one measured tensor: with recycling it is
  sufficient; changing the size adds no safety.
- Keep the reset inside `cache_ensure_temp` (self-contained) rather than
  at step boundaries in the exec loop: smaller surface, no behavior change
  for callers, no interaction with the exec-side step machinery.
- Do not touch the `failed to allocate loaded ids buffers (il=26 ...
  n_tokens=0)` warning: it is the documented benign pre-output-selection
  path (0-size ids tensors), a separate BUG-001 follow-up, not this defect.

## Next Phase

- Stage 6A.6 rerun result (below) determines whether the acceptance
  criteria are met (`cacheRead > 0` on the ordinary followup turn,
  telemetry corroboration, materially lower prompt-eval time vs the
  uncached ~27k baseline).
- After a PASS: promote the fix to the live runtime
  (`tools/freeze_live_runtime.sh` + `tools/serve_kimi_local.sh restart`),
  which also closes the live server's own recurring SIGABRTs (two observed
  in `/tmp/kimi-llama-server.lifecycle.log`).

## Reproduction

```bash
# unit test
cd llama.cpp && cmake --build build-metal --target test-expert-stream-temp-ctx -j8 \
  && ./build-metal/bin/test-expert-stream-temp-ctx; echo $?

# large-prompt regression (real code prompt, 2 requests on one PID,
# crosses the old 2850-object boundary; expect "pool exhausted; recycled"
# and zero aborts)
KIMI_STREAM_DEBUG=1 KIMI_STREAM_EXPERTS=naive KIMI_EXPERT_CACHE_MB=4096 \
KIMI_EXPERT_CACHE_MODE=zerocopy \
llama.cpp/build-metal/bin/llama-server -m models/kimi-linear/...-Q4_K_M.gguf \
  -ngl 0 --no-mmap --ctx-size 32768 --host 127.0.0.1 --port 18081 --parallel 1
# then: two POST /v1/chat/completions, ~200 max_tokens each, real code prompt
grep -c "\[temp\] recreate" /tmp/kimi-llama-server.log   # > 2850
grep -c "pool exhausted; recycled" /tmp/kimi-llama-server.log  # >= 1
grep -c "GGML_ASSERT" /tmp/kimi-llama-server.log          # 0
```

## Stage 6A.6 Rerun (2026-08-16, fixed binary 0a6b2df63, fresh PID 63686)

Executed with the documented harness (`stage6a6-acceptance-harness.ts`, dev
config, `KIMI_BIN=llama.cpp/build-metal/bin/llama-server`, port 18080, ctx
32768, zerocopy cache 4096 MiB). Records:
`dev-openclaw/state/stage6a6-acceptance/2026-08-16T21-41-41-660Z/`.

### Results

- **Prefill turn** (runId `cfe69649-...`): 22,409-token cold eval
  `prompt eval time = 1348704.81 ms / 22409 tokens (16.62 t/s)` — the exact
  scenario that SIGABRT'd the old binary right after progress 1.00 —
  **completed with zero aborts** and generated "ok".
- **Ordinary followup turn** (runId `48aae132-...`): usage
  `input: 86, output: 2, cacheRead: 22410` — 22,410 tokens served from the
  prefix/KV cache; server-side eval was 86 tokens / 14,456.88 ms vs the
  prefill's 22,409 tokens / 1,348,704.81 ms (~93x lower, and ~130x lower
  than the ~27k uncached 1,882,854 ms baseline).
- Zero `ggml_new_object` warnings, zero `GGML_ASSERT` aborts, zero recycles
  needed (this run stayed below the 2,850-object boundary: ~46 steps x ~15
  objects; the boundary crossing is covered by the two-request regression
  above).

### Acceptance criteria

| Criterion | Result |
| --- | --- |
| `cacheRead > 0` on the ordinary turn | PASS — 22,410 |
| llama-server telemetry corroborates prefix reuse | PASS — followup eval 86 tokens vs 22,409 cold |
| Ordinary prompt-eval materially lower than uncached ~27k baseline | PASS — 14.5 s vs 1,348.7 s (same-PID) / 1,882.9 s (baseline) |

### Promotion

- `tools/freeze_live_runtime.sh` -> `runtime/live/COMMIT` =
  `0a6b2df63 stage-6a6: fix grow-only temp-context pool underallocation
  (recycle on overflow)`; frozen `libllama` contains the recycle path
  (string check).
- Live server restarted (`tools/serve_kimi_local.sh start`, PID 64991);
  direct completion smoke (HTTP 200, "ok") and
  `openclaw infer model run --model kimi-local/kimi-linear-48b` both pass.
- This also closes the live server's own recurring SIGABRTs from this
  defect (two observed in `/tmp/kimi-llama-server.lifecycle.log`, exit 134,
  both `cache_ensure_temp` at the 2,849/2,850-object boundary).
