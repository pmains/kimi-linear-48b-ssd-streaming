# Stage 6A.6 Preflight (2026-08-16) — SQLite live-event monitor

## Status

PREFLIGHT PASS

## Changes

- `openclaw-src/scripts/dev/stage6a6-acceptance-harness.ts`: the live-run
  marker monitor now reads the authoritative SQLite `trajectory_runtime_events`
  table in the per-agent state database
  (`<OPENCLAW_STATE_DIR>/agents/<agentId>/agent/openclaw-agent.sqlite`) via
  `node:sqlite` (read-only, matched on `run_id`). `session.started` /
  `prompt.submitted` rows for the accepted runId are the live marker;
  `trace.artifacts` / `session.ended` rows are the terminal state. The former
  `.trajectory.jsonl` scan remains only as a fallback.
- Dev gateway config `dev-openclaw/config/openclaw.json` already carries
  `"diagnostics": { "enabled": false }` (validated JSON; supported top-level
  `DiagnosticsConfig.enabled` key in openclaw-src). The dev gateway boots with
  this config; that is the supported dev-gateway gate for stuck-session
  recovery. Provider `timeoutSeconds` was already removed previously.
- The dev gateway must be started with the same gateway auth token the harness
  borrows (`OPENCLAW_GATEWAY_TOKEN` from `~/.openclaw/openclaw.json`); without
  it the gateway generates a fresh runtime token per startup and the harness is
  rejected with `AUTH_TOKEN_MISMATCH`.

## Evidence (all four preflight conditions)

1. **dev gateway**: listening on 127.0.0.1:18790 with the diagnostics-disabled
   dev config (gateway log: `http server listening (17 plugins ...)`).
2. **accepted runId**: `eb4e791b-8762-4014-88ec-94b74471b138` accepted at
   2026-08-16T12:35:59-07:00 (`[agent/embedded] embedded run auto-compaction
   start: runId=...`).
3. **llama-server receives request**: gateway POST
   `http://127.0.0.1:18080/v1/chat/completions` -> 200 text/event-stream at
   12:36:49; server log shows task 0 `prompt processing, n_tokens = 281,
   progress = 0.99` then generation (`n_gen = 100..132`).
4. **SQLite observes live runtime events**: `trajectory_runtime_events` in
   `dev-openclaw/state/agents/caveman/agent/openclaw-agent.sqlite` has 7 rows
   for the accepted runId — `session.started`, `trace.metadata`,
   `context.compiled`, `prompt.submitted` at 19:35:59Z (live, before
   completion), then `model.completed`, `trace.artifacts`, `session.ended` at
   19:37:54Z.

## Termination

Smoke run terminated after the four conditions were observed. Everything is
stopped: llama-server (stopped via `tools/serve_kimi_local.sh stop`), dev
gateway (exited), harness (exited). Ports 18080 and 18790 free.

## Problems

- The run's provider stream was interrupted at 12:37:49 when the dev gateway
  received an external SIGTERM (`[gateway] received SIGTERM; shutting down`),
  so the in-flight run surfaced `Connection error` / `surface_error`. All four
  conditions were observed before that interruption; the preflight is an
  integration check, not an endurance experiment, so this does not block PASS.
- After the SIGTERM the gateway's `localService` auto-spawn attempts crashed
  with `Library not loaded: @rpath/libllama-server-impl.dylib` (frozen runtime
  bundle spawned from gateway cwd). Not part of the preflight path; not
  investigated further per directive.
- The harness client exited without writing its own results.json when the
  gateway shut down mid-run; conditions were verified directly from the gateway
  log, server log, and SQLite.

## Reproduction

```bash
# 1. dev gateway (token must match the harness's borrowed auth)
cd openclaw-src
OPENCLAW_HOME=$PWD/../dev-openclaw/home \
OPENCLAW_STATE_DIR=$PWD/../dev-openclaw/state \
OPENCLAW_CONFIG_PATH=$PWD/../dev-openclaw/config/openclaw.json \
OPENCLAW_GATEWAY_PORT=18790 OPENCLAW_SERVICE_KIND=gateway \
OPENCLAW_GATEWAY_TOKEN="<token from ~/.openclaw/openclaw.json gateway.auth.token>" \
node openclaw.mjs gateway --port 18790

# 2. preflight harness (cheapest smoke; bounded below the former ~6-min watchdog)
cd openclaw-src
OPENCLAW_HOME=$PWD/../dev-openclaw/home \
OPENCLAW_GATEWAY_URL=http://127.0.0.1:18790 \
pnpm exec tsx scripts/dev/stage6a6-acceptance-harness.ts \
  --state-dir $PWD/../dev-openclaw/state/stage6a6-preflight \
  --stop-after-live-marker --live-marker-timeout-seconds 360 \
  --health-timeout-seconds 300

# 3. verify SQLite live events
sqlite3 dev-openclaw/state/agents/caveman/agent/openclaw-agent.sqlite \
  "SELECT seq, json_extract(event_json,'$.type'), run_id FROM trajectory_runtime_events WHERE run_id='<runId>' ORDER BY seq;"

# 4. stop
tools/serve_kimi_local.sh stop   # (harness leaves llama-server detached)
```

## Next Phase

Run the full Stage 6A.6 acceptance (canonical invariant-prefix prefill,
then a brand-new ordinary Caveman session on the same fresh llama-server PID)
now that the live-run monitor reads the authoritative SQLite event stream. The
monitor gap is closed; the acceptance gate is the cache-reuse criteria in
SERVICE-ROADMAP.md §6A.6.
