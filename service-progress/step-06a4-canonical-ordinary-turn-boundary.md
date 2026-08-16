# Stage 6A.4 - Canonical Ordinary-Turn Boundary

## Status

PARTIAL

## Objective

Cut prefill over to the canonical ordinary-turn boundary so the stable-bootstrap path no longer invents its own full embedded attempt shape.

## Changes

- Added a narrow `StableBootstrapAttemptContext` type for the stable-bootstrap prep chain.
- Updated stable-bootstrap prep helpers to consume that narrower resolved context instead of a fake `EmbeddedRunAttemptParams` shell.
- Changed `prefillWithStableBootstrapForAgent(...)` to take an explicit resolved context alongside the prepared model/auth result.
- Kept the provider invocation budget at `maxTokens: 1`.

## Results

- `./node_modules/.bin/vitest run src/agents/simple-completion-runtime.test.ts` - PASS
- `./node_modules/.bin/vitest run src/agents/embedded-agent-runner/run/attempt-stable-bootstrap-run.test.ts src/agents/embedded-agent-runner/run/attempt-stable-bootstrap.test.ts` - PASS
- Live acceptance probe against fresh `runtime/live/bin/llama-server` reached the server at PID `51981`, but the ordinary Caveman path did not complete cleanly because the current OpenClaw config/state was rejected by the source CLI before a full end-to-end proof could be recorded.
- The source prefill probe also failed before llama-server evaluation when auth/profile loading hit `AUTH_PROFILE_MIGRATION_REQUIRED` for `~/.openclaw/agents/main/agent/openclaw-agent.sqlite`.
- The isolated `dev-openclaw` environment is now the canonical Stage 6A.4 harness.
- In that environment, the non-conversational prefill link completed successfully against the fresh live server PID `75519` and returned a one-token warm call from the canonical Caveman selection path.
- The isolated Caveman config now explicitly disables the memory plugin slot:
  - `plugins.slots.memory = "none"`
- After that config change and a fresh server restart, the warm prefill still completed successfully against fresh PID `77135`.
- The subsequent brand-new ordinary Caveman session entered the model path cleanly and no longer emitted the earlier OpenAI-backed memory-sync/auth warning.
- The new session was persisted in the isolated agent DB as `agent:caveman:stage6a4-devopenclaw-20260815t223000` with session id `3b60f031-9fc0-4ce4-b035-d2cae6d4b303`.
- That session reached prompt submission and model selection, but it still has not produced a terminal completion envelope.
- After adding temporary Stage 6A.4 boundary tracing and rerunning the isolated ordinary Caveman turn, the run reached the `openai-completions` transport and received `200 text/event-stream` from llama-server.
- The settled-path boundary is now narrower than `waitForPendingEvents`: `runEmbeddedAttemptSettledPhase` passed the pending-event join and entered `settleEmbeddedAttemptStream`.
- Inside the completions transport, the stream iterator exhausted without observing `data: [DONE]` and without a provider `finish_reason` on the wedged turn (`sawStreamDONE=no`, `sawStopFinishReason=no`).
- The first missing terminal signal on the stalled turn is therefore the provider SSE terminalization, not the embedded subscription drain.
- The exact JSON body sent by the isolated Caveman ordinary turn was captured to
  `dev-openclaw/state/stage6a4-request-body.json` (`sha256=34839c6e4d22c445d313fbd1b7f62c643de995fd2aa5c5474ccdd30900be083f`).
  Its shape is:
  - `model=kimi-linear-48b`
  - `messages=5` (`system` + `user` x4)
  - `stream=true`
  - `stream_options.include_usage=true`
  - `tool_choice=auto`
  - `tools=53`
  - `max_completion_tokens=1`
- Replaying that exact body directly against the same llama-server PID `77135`
  did terminate normally when observed over a longer 300s window:
  - the first SSE chunk arrived immediately at request start and contained only
    the assistant role delta;
  - the server log showed prompt processing advancing through 2,048, 2,633, and
    3,145 prompt tokens before prompt evaluation completed;
  - the first actual generated content token (`ok`) appeared at about
    `+211.372s`, followed immediately by `finish_reason:"length"`, usage, and
    `data: [DONE]`.
- That means the identical Caveman request payload does eventually complete on
  the direct HTTP path; the earlier 90-second observations were under-observed
  rather than evidence of a true nonterminating stream.
- The 90-second reduction matrix is therefore only a short-window heuristic:
  - `del(.stream_options.include_usage)` produced an early streamed chunk, but it
    still needs the same longer observation window before being judged complete.
  - `del(.tool_choice)`, `del(.tools)`, and `.max_completion_tokens=8` did not
    produce enough evidence inside 90s to distinguish slow prompt evaluation
    from a real stall.

## Problems

- The live Caveman acceptance sequence is blocked by pre-existing workspace state/config issues, not by a llama-server failure: the source CLI reports the home config as invalid and the state DB as requiring migration, while the direct prefill probe hits auth-profile migration before it can reach provider evaluation.
- The direct prefill harness still needs a canonical auth-bearing resolved context that bypasses independent auth-profile discovery; the attempted harness path re-entered legacy auth/profile loading instead of starting from an already-resolved execution context.
- Stage 6A.4 acceptance must now use the isolated `dev-openclaw` environment that already passed the ordinary Caveman control turn:
  - `OPENCLAW_HOME=/Users/pmains/Code/openclaw/kimi/dev-openclaw/home`
  - `OPENCLAW_STATE_DIR=/Users/pmains/Code/openclaw/kimi/dev-openclaw/state`
  - `OPENCLAW_CONFIG_PATH=/Users/pmains/Code/openclaw/kimi/dev-openclaw/config/openclaw.json`
- The older `/tmp/openclaw-stage6a4-*` harness is retired for further acceptance probes.
- Memory sync is optional ancillary behavior for this Caveman path; it is not required for the canonical ordinary turn, and disabling the memory slot removed the OpenAI-backed auth warning.
- The isolated-environment ordinary Caveman acceptance chain still has not
  reached the final normal-response envelope in the conversational path.
- The direct-wire comparison no longer indicates a hard nontermination in the
  payload itself; the long-window replay shows the request spends a long time in
  prompt evaluation before completing normally.
- The 90-second reduction search was under-observing prompt evaluation, so it is
  not a reliable failure boundary yet.

## Decisions

- Kept `maxTokens: 1` rather than introducing a provider-level prefill flag.
- Chose a narrower stable-bootstrap context instead of a second full embedded-run attempt object.

## Next Phase

- Resolve the auth/config-state seam so the live same-PID Caveman acceptance sequence can run and record prefix reuse, cache read, and generation success.

## Reproduction

- `cd openclaw-src && ./node_modules/.bin/vitest run src/agents/simple-completion-runtime.test.ts`
- `cd openclaw-src && ./node_modules/.bin/vitest run src/agents/embedded-agent-runner/run/attempt-stable-bootstrap-run.test.ts src/agents/embedded-agent-runner/run/attempt-stable-bootstrap.test.ts`
- `cd openclaw-src && OPENCLAW_HOME=/Users/pmains/Code/openclaw/kimi/dev-openclaw/home OPENCLAW_STATE_DIR=/Users/pmains/Code/openclaw/kimi/dev-openclaw/state OPENCLAW_CONFIG_PATH=/Users/pmains/Code/openclaw/kimi/dev-openclaw/config/openclaw.json node --import tsx src/entry.ts agent --local --agent caveman --json --message 'Reply with the single word ok.'`
- Temporary boundary trace file:
  - `/Users/pmains/Code/openclaw/kimi/dev-openclaw/state/stage6a4-boundary.log`
- Exact Caveman request body capture:
  - `/Users/pmains/Code/openclaw/kimi/dev-openclaw/state/stage6a4-request-body.json`
- Long-window exact-body replay:
  - `/Users/pmains/Code/openclaw/kimi/dev-openclaw/state/stage6a4-reduction/baseline_300s.body.txt`
  - `/Users/pmains/Code/openclaw/kimi/dev-openclaw/state/stage6a4-reduction/baseline_300s.meta.json`
- Direct minimal llama-server comparison:
  - `curl -sS -N --http1.1 -D - -H 'Content-Type: application/json' -H 'Authorization: Bearer llama-cpp-local' http://127.0.0.1:18080/v1/chat/completions -d '{"model":"kimi-linear-48b","messages":[{"role":"user","content":"Reply with the single word ok."}],"stream":true,"max_tokens":1}'`
- Exact-body direct replay:
  - `curl -sS -N --http1.1 --max-time 300 -D - -H 'Content-Type: application/json' -H 'Authorization: Bearer llama-cpp-local' http://127.0.0.1:18080/v1/chat/completions --data-binary @dev-openclaw/state/stage6a4-request-body.json`
- Single-variable reduction matrix:
  - `dev-openclaw/state/stage6a4-reduction/results.tsv`
  - `dev-openclaw/state/stage6a4-reduction/no_include_usage_90s.body.txt`
  - `dev-openclaw/state/stage6a4-reduction/no_include_usage_90s.meta.txt`
- The `/tmp/openclaw-stage6a4-*` harness is no longer a valid reproduction path.
- Warm prefill reproduction in the isolated environment:
  - `cd openclaw-src && OPENCLAW_HOME=/Users/pmains/Code/openclaw/kimi/dev-openclaw/home OPENCLAW_STATE_DIR=/Users/pmains/Code/openclaw/kimi/dev-openclaw/state OPENCLAW_CONFIG_PATH=/Users/pmains/Code/openclaw/kimi/dev-openclaw/config/openclaw.json node --import tsx --input-type=module ...`
- Isolated dev config change for memory sync:
  - add `plugins.slots.memory = "none"` to `dev-openclaw/config/openclaw.json`
