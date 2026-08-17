# Stage 5C Report - Runtime Stability

## Status

PASS

## Objective

Demonstrate that the patched local Kimi service can survive the Stage 5 runtime path on one `llama-server` PID:

`cold bootstrap -> generation -> new-session warm reuse -> generation`

without allocation abort, watchdog abort, provider timeout, fallback model, or server restart.

## Changes

- No new code or configuration changes were made for this test.
- The test used the already-patched service and the existing OpenClaw/Caveman request path.

## Results

- Live `llama-server` PID before, during, and after the test: `16721`
- Cold bootstrap session:
  - session key: `agent:caveman:stage5c-bootstrap-20260815t000000`
  - session id: `44e5e232-60ea-4852-a547-08c49f300313`
  - provider: `llama-server`
  - model: `kimi-linear-48b`
  - status: completed
  - aborted: `false`
  - input tokens: `510`
  - cache read: `2747`
  - duration: `55448 ms`
  - server timing: prompt eval `33838.53 ms / 510 tokens`

- Warm reuse session:
  - session key: `agent:caveman:stage5c-reuse-20260815t000000`
  - session id: `d18730c7-ef35-4424-afec-9250b0127958`
  - provider: `llama-server`
  - model: `kimi-linear-48b`
  - status: completed
  - aborted: `false`
  - input tokens: `510`
  - cache read: `2747`
  - duration: `38796 ms`
  - server timing: prompt eval `33838.53 ms / 510 tokens`

- Warm reuse was materially prefix-shaped:
  - second request reused cached prefix material instead of re-evaluating a full bootstrap-sized prompt
  - `fallbackUsed: false`
  - `winnerProvider: llama-server`
  - `winnerModel: kimi-linear-48b`

## Problems

- The server log still emits `llama_expert_streamer: failed to allocate loaded ids buffers` warnings during requests, but they did not produce a request abort in this run.

## Decisions

- Treat Stage 5 as complete after this successful same-PID cold bootstrap plus warm reuse sequence.
- Keep Stage 6 separate; do not fold context-window expansion into Stage 5.

## Next Phase

- Stage 6 may begin from this stable warm-service baseline.

## Reproduction

```bash
tools/serve_kimi_local.sh status
openclaw agent --agent caveman --session-key stage5c-bootstrap-20260815t000000 --message Hello --json
tools/serve_kimi_local.sh status
openclaw agent --agent caveman --session-key stage5c-reuse-20260815t000000 --message ping --json
tools/serve_kimi_local.sh status
tail -n 120 /tmp/kimi-llama-server.launchd.log
openclaw sessions --agent caveman --json --limit 8
```

## Artifacts

- `/Users/pmains/.openclaw/agents/caveman/sessions/44e5e232-60ea-4852-a547-08c49f300313.jsonl`
- `/Users/pmains/.openclaw/agents/caveman/sessions/d18730c7-ef35-4424-afec-9250b0127958.jsonl`
- `/tmp/kimi-llama-server.launchd.log`
