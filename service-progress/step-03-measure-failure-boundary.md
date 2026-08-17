# Stage 3 Report - Measure the Caveman Failure Boundary

## Status

PARTIAL

## Objective

Measure where the `caveman` path is failing so the next fix targets the real blocker instead of guessing.

This stage focused on two questions:

- Is the local Kimi service still too slow to survive OpenClaw's watchdog window?
- Is the current caveman request shape failing earlier than that?

## Changes

- No runtime code changed in this stage.
- Collected caveman session history and transcript evidence.
- Collected current local Kimi server log evidence.

## Results

The caveman session `agent:caveman:main` failed on a trivial `Hello` turn before Kimi produced any content.

Observed sequence:

1. caveman started with provider `llama-server` and model `kimi-linear-48b`
2. the local Kimi turn failed immediately
3. OpenClaw fell back to `gpt-5.4-mini`, which answered `Hello.`

Exact failure recorded in the session transcript:

- `400 JSON schema conversion failed:`
- `Pattern must start with '^' and end with '$'`

That means the current caveman failure boundary is not just slow prefill. The local Kimi path is also rejecting the request payload shape before a useful reply can be emitted.

The server log still shows the earlier long-prefill problem as a separate issue:

- prompt processing progressed slowly on large tasks
- several tasks were cancelled before completion
- one long run reached very high prompt progress before being stopped
- the expert-streamer emitted repeated `failed to allocate loaded ids buffers` warnings during those runs

So the evidence now points to two distinct blockers:

- immediate request/schema failure on caveman's `Hello` run
- earlier slow-prefill / watchdog pressure during larger requests

## Problems

- The caveman request path is not yet stable enough to rely on Kimi as primary.
- The error surfaced before any useful generation, so tuning cache size alone is not sufficient.
- The slow-prefill logs remain relevant, but they are no longer the only blocker.

## Decisions

- Keep the scope on `caveman` only.
- Treat the request-shape/schema failure as the immediate next thing to fix or bypass.
- Keep the older prefill timing issue in view, but do not widen the rollout until caveman can complete a basic turn cleanly.

## Next Phase

The next step should inspect the caveman request path that is producing the bad schema conversion input for `llama-server`.

Likely targets:

- the caveman model wrapper / request adapter
- tool schema generation for the Kimi provider path
- any OpenClaw-specific JSON schema or pattern serialization that differs from other providers

## Reproduction

Current caveman failure can be reproduced with:

```bash
sed -n '1,120p' /Users/pmains/.openclaw/agents/caveman/sessions/bdf44695-49bf-4d89-910a-022904026f42.jsonl
tail -n 120 /tmp/kimi-llama-server.launchd.log
```

Useful session inspection commands:

```bash
openclaw sessions_list --agentId caveman
openclaw sessions_history --sessionKey agent:caveman:main
```

The relevant live server state for this measurement is:

- host: `127.0.0.1`
- port: `18080`
- context: `32768`
- expert cache: `4096 MiB`

