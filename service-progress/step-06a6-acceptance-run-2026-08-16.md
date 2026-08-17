# Stage 6A.6 Acceptance Run — 2026-08-16

## Status

FAIL (incomplete cache-acceptance observation)

## Objective

Single full Stage 6A.6 acceptance experiment as documented: fresh
`llama-server` PID -> cold canonical prefill turn -> ordinary Caveman turn ->
measure cache reuse (`cacheRead > 0` on the ordinary turn, llama-server
telemetry corroboration, prompt-evaluation time materially lower than the
uncached ~27k-token baseline).

## Setup (reproduced exactly as documented)

- Dev gateway: `openclaw-src` build on 127.0.0.1:18790 with
  `OPENCLAW_CONFIG_PATH=dev-openclaw/config/openclaw.json` (contains
  `diagnostics.enabled=false`), `OPENCLAW_STATE_DIR=dev-openclaw/state`,
  `OPENCLAW_HOME=dev-openclaw/home`, gateway auth token matching the harness's
  borrowed token.
- Harness: `pnpm exec tsx scripts/dev/stage6a6-acceptance-harness.ts` (defaults:
  prefill "Reply with the single word ok.", followup "Reply with the single
  word yes.", target session `agent:caveman:main`, no client timeout, SQLite
  `trajectory_runtime_events` live-run monitor).
- Server: started fresh by the harness via `tools/serve_kimi_local.sh` (PID
  58186, port 18080, ctx 32768, expert cache 4096 MiB zerocopy), healthy before
  dispatch.
- Run window: 2026-08-16 19:51Z–20:11Z (12:51–13:11 MST).
- Artifacts: `dev-openclaw/state/stage6a6-acceptance/2026-08-16T19-48-29-562Z/`
  (manifest.json, results.json, summary.md), `/tmp/kimi-llama-server.log`,
  `/tmp/kimi-dev-gateway.log`, SQLite
  `dev-openclaw/state/agents/caveman/agent/openclaw-agent.sqlite`.

## Results

### Prefill turn (runId `eac84423-7b31-498c-b7ae-84629dfb2a00`)

- Dispatched 19:51:03Z, accepted (`status: accepted`), auto-compaction
  occurred (`compactionCount: 1`).
- Model call 1 (server task 0): compacted prompt of 331 tokens,
  `prompt eval time = 19540.66 ms / 331 tokens (16.94 t/s)`; 175 tokens
  generated at 3.65 t/s; `graphs reused = 0`.
- Model call 2 (server task 177): prompt of 22,241 tokens,
  `prompt eval time = 1088.14 s / 22241 tokens (20.44 t/s)`; **no
  `cached_tokens` in any progress line -> cold evaluation**.
- Run errored at 20:10:31Z: `LLM request timed out. rawError=terminated`
  (gateway), because llama-server crashed (below). Final trajectory:
  `finalStatus: error`, `stopReason: error`, `assistantTexts: []`.

### llama-server crash (the immediate blocker)

Immediately after task 177's 22,241-token prompt eval completed (progress
1.00), before/at generation:

```
ggml_new_object: not enough space in the context's memory pool (needed 1049168, available 1048944)
/Users/pmains/Code/openclaw/kimi/llama.cpp/ggml/src/ggml.c:1813: GGML_ASSERT(obj_new) failed
```
-> SIGABRT (backtrace: `ggml_new_object` -> `ggml_abort` ->
`server_context_impl::update_slots` -> `server_context_impl::decode`).

The ggml context compute pool ran short by **224 bytes** for the post-eval
graph at 22,241 prompt tokens. The repeated
`llama_expert_streamer: failed to allocate loaded ids buffers (il=26 ...)`
warnings precede this; the expert-streamer buffer accounting is the suspect
but llama.cpp is NOT patched per the roadmap STOP boundary.

### Ordinary followup turn (runId `856bb37c-b04c-4484-be54-2db34fd20d1f`)

- Dispatched 20:10:45Z (immediately after the prefill errored), accepted.
- Errored 20:10:48Z (3 s): no live server. The gateway's `localService`
  auto-spawn of the frozen bundle
  (`runtime/live/bin/llama-server`) failed repeatedly:
  `dyld: Library not loaded: @rpath/libllama-server-impl.dylib` (SIGABRT).
- The followup **never reached a live model call**: no usage report, so
  `cacheRead` is unmeasurable. Acceptance criterion 1 cannot be evaluated.

### Monitor (SQLite `trajectory_runtime_events`)

- Followup: live marker FOUND via the new SQLite monitor
  (`state: found`, seq 70, `session.started`, `waitedMs: 3204`).
- Prefill: marker `pending` during its 120 s window even though the rows
  exist. Explanation: the trajectory runtime store flushes to SQLite at run
  finalization (`src/agents/embedded-agent-runner/run/attempt-finalize.ts`
  calls `trajectoryRecorder.flush()`); `created_at` stores the EVENT
  timestamp, not the insert time. The prefill's rows (event times 19:51:03Z)
  were inserted at finalize (20:10:31Z), after the marker window. The SQLite
  monitor is therefore a durable run-observability source (rows appear at
  finalize), not a mid-run progress source for long-running model calls.
  This also corrects the earlier preflight note: the preflight run's rows
  became visible at finalization, not "while live".

## Acceptance criteria verdict

| Criterion | Result |
| --- | --- |
| `cacheRead > 0` on the ordinary turn | NOT MEASURABLE (followup never reached a model; prefill's own large eval showed 0 cached tokens) |
| llama-server telemetry corroborates prefix reuse | NO (no `cached_tokens` in any task; both large evals cold) |
| Ordinary prompt-eval materially lower than uncached ~27k baseline | NO (followup never evaluated; prefill task 177: 22,241 tok / 1088.14 s cold) |

Per the roadmap: the run produced terminal evidence of failure, so this is an
incomplete cache-acceptance observation. STOP boundary honored — no
llama.cpp patch, no further runtime investigation this run.

## Problems

1. ggml compute-pool shortfall (224 B) after a 22,241-token prompt eval ->
   `GGML_ASSERT(obj_new)` -> SIGABRT in the modified expert-streaming runtime.
   This is the new boundary: the expert-streamer buffer accounting under large
   prompt sizes (the preflight/smoke runs only exercised ~300-token prompts).
2. Dev gateway `localService` auto-spawn of the frozen runtime bundle fails on
   `@rpath/libllama-server-impl.dylib`; the gateway cannot self-restart the
   model server. The acceptance harness starts the server itself, so this only
   mattered after the crash.
3. Agent-dispatch path auto-compacts the first turn (`compactionCount: 1`),
   so the original "canonical invariant-prefix prefill" (~27k full
   serialization) no longer occurs as the first model call; the prefill's
   large cold eval was its second model call.
4. SQLite live-marker semantics: rows are visible at run finalization (flush
   cadence), so "live" marker == finalize-time marker for long runs.

## Decisions

- Record the acceptance attempt as FAIL / incomplete cache-acceptance
  observation with full terminal evidence (gateway log, server log, SQLite,
  harness results.json).
- Do not patch llama.cpp (roadmap STOP point). The next boundary is the
  expert-streamer compute-buffer accounting crash at large prompt sizes,
  followed by the prefix-reuse characterization once a large cold eval can
  complete without crashing.

## Next Phase

- Reproduce/characterize the 224-byte ggml compute-pool shortfall (22,241-token
  prompt + generation graph) in the expert-streaming runtime before any further
  cache-acceptance measurement; a warm-turn measurement is impossible while the
  server aborts after large evals.
- Then rerun Stage 6A.6 acceptance on a fresh PID per the roadmap criteria.

## Reproduction

```bash
cd openclaw-src
# dev gateway (token = gateway.auth.token from ~/.openclaw/openclaw.json)
OPENCLAW_HOME=$PWD/../dev-openclaw/home \
OPENCLAW_STATE_DIR=$PWD/../dev-openclaw/state \
OPENCLAW_CONFIG_PATH=$PWD/../dev-openclaw/config/openclaw.json \
OPENCLAW_GATEWAY_PORT=18790 OPENCLAW_SERVICE_KIND=gateway \
OPENCLAW_GATEWAY_TOKEN="<token>" node openclaw.mjs gateway --port 18790

# acceptance harness (defaults = documented reproduction)
OPENCLAW_HOME=$PWD/../dev-openclaw/home \
OPENCLAW_GATEWAY_URL=http://127.0.0.1:18790 \
pnpm exec tsx scripts/dev/stage6a6-acceptance-harness.ts

# evidence
sqlite3 ../dev-openclaw/state/agents/caveman/agent/openclaw-agent.sqlite \
  "SELECT run_id, json_extract(event_json,'$.type'), datetime(created_at/1000,'unixepoch') FROM trajectory_runtime_events WHERE run_id IN ('eac84423-7b31-498c-b7ae-84629dfb2a00','856bb37c-b04c-4484-be54-2db34fd20d1f');"
tail -40 /tmp/kimi-llama-server.log   # ggml crash evidence
```
