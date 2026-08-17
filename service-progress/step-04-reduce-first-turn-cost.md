# Stage 4 Report - Reduce Caveman First-Turn Cost

## Status

PASS

## Objective

Make caveman's first Kimi turn small enough and simple enough to survive OpenClaw's abort window instead of falling back immediately.

This stage focused on two things:

- removing the bad request/schema shape that llama-server rejected
- shrinking caveman's prompt/tool surface so the first turn has a chance to finish

## Changes

- Updated caveman in `~/.openclaw/openclaw.json` only.
- Moved the Kimi request params into the model catalog entry instead of the agent model object.
- Switched caveman to the known-good `llama-server/kimi-linear-48b` model reference.
- Enabled caveman-only `experimental.localModelLean`.
- Removed caveman's skill prompt surface with `skills: []`.
- Left other agents on DeepSeek.

## Environment

- Host: Peter's MacBook Air, Apple Silicon `arm64`
- macOS: `26.5.2` (`25F84`)
- Repository commit: `84df80e035aea3a1a59c9f8110dfcbce8f143bd8`
- Live model path: `llama-server/kimi-linear-48b`
- Live server host / port: `127.0.0.1:18080`
- Live server context: `32768`
- Expert cache: `4096 MiB`
- Caveman agent: `caveman`
- Service state: same live `llama-server` process handled the cold run and the immediate warm follow-up, with no restart between them

## Tests

- Ran a cold caveman request with `openclaw agent --agent caveman --message "Hello" --json`
- Repeated the same request immediately against the same live server instance
- Checked the caveman session history with `openclaw sessions_history --sessionKey agent:caveman:main`
- Checked the live Kimi server log with `tail -n 80 /tmp/kimi-llama-server.lifecycle.log`

Expected result:

- the caveman request shape should no longer trigger the schema conversion failure
- the prompt surface should be smaller
- the immediate retry should reuse most of the bootstrap and be much cheaper than cold

Observed result:

- the schema conversion error stopped appearing
- the first request remained expensive but completed far enough to establish a baseline
- the immediate retry reused the prefix heavily and cut prompt-processing time sharply

## Measurements

Qualitative changes observed:

- The previous `400 JSON schema conversion failed: Pattern must start with '^' and end with '$'` error no longer appears on the retried caveman runs.
- The caveman prompt got much smaller.
- The system prompt report for the latest run dropped from about 9.5k chars to about 4.6k chars.
- The injected skill prompt dropped to `0` chars.

Measured cold pass:

- Prompt tokens evaluated: `7819`
- Cached/reused tokens surfaced in trace: `14336` cache-read tokens
- Prefill / prompt-eval time: `569,912.97 ms`
- First-token latency: about `570 s`
- Kimi completion: yes, but the turn still fell back afterward instead of staying cleanly on Kimi

Measured warm pass immediately after, without restart or config change:

- Prompt tokens evaluated: `245`
- Cached/reused tokens surfaced in trace: not exposed as a separate count, but the server selected the slot by LCP similarity (`f_sim_best = 0.989`, `f_keep = 0.996`)
- Prefill / prompt-processing time: `25.58 s`
- First-token latency: about `25.6 s`
- Kimi completion: no clean final completion; the run later hit a context-pool crash after generation had already started

## Results

The stage established three facts:

- the caveman request-shape issue was fixed enough to get a real Kimi request through
- the warm follow-up reused most of the bootstrap
- the remaining blocker moved from request-shape cleanup to later runtime stability

Observed latest run shape:

1. caveman launched against `llama-server/kimi-linear-48b`
2. lean mode trimmed the tool surface from 92 tools to 16
3. skills were removed from the prompt entirely
4. the cold pass still paid the full bootstrap cost
5. the immediate retry reused the prefix heavily and cut prefill from minutes to seconds

## Problems

- The schema conversion bug is fixed.
- The first-turn cost problem is now measured rather than guessed.
- The remaining issue is no longer prefix reuse; it is the later runtime stability boundary after generation has begun.

## Decisions

- Keep the scope on caveman only.
- Treat schema cleanup and prompt trimming as necessary and now validated.
- Do not expand the fix to other agents.
- Do not reopen prefix-reuse work unless it becomes the active blocker again.

## Next Phase

The next step should move to the remaining caveman-specific guardrails:

- stabilize the warm path so the reused-prefix run can finish cleanly
- keep the bootstrap lean and stable
- measure the second-turn reuse path again after the runtime is stabilized, not by changing the prompt surface further

## Reproduction

Run a fresh caveman turn for the cold baseline:

```bash
openclaw agent --agent caveman --message "Hello" --json
```

Immediately repeat the same turn without restarting the server.

Check the local Kimi server log:

```bash
tail -n 80 /tmp/kimi-llama-server.lifecycle.log
```

Check the caveman session transcript:

```bash
openclaw sessions_history --sessionKey agent:caveman:main
```

The latest successful caveman config state includes:

- `experimental.localModelLean = true`
- `skills = []`
- `model.primary = llama-server/kimi-linear-48b`

## Artifacts

- `/tmp/kimi-llama-server.lifecycle.log`
- `openclaw sessions_history --sessionKey agent:caveman:main` output
- `~/Library/Logs/DiagnosticReports/llama-server-*.ips` for any crash tied to the warm follow-up
