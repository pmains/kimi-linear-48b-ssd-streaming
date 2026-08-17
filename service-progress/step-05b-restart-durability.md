# Stage 5B Report - Restart Durability

## Status

UNSUPPORTED

## Objective

Test whether Caveman bootstrap state can be saved with llama.cpp's native slot API, survive a `llama-server` restart, and be restored into a new request that remains materially warm.

## Environment

- Live server PID before restart: `16372`
- Live server PID after restart: `16721`
- Service wrapper:
  - `tools/serve_kimi_local.launchd.sh`
  - `tools/serve_kimi_local.sh`
- Persistent slot-save path:
  - `runtime/state/slot-cache`
- Model:
  - `kimi-linear-48b`
- Server context:
  - `--ctx-size 32768`
  - `--parallel 1`

## Changes

- Added `--slot-save-path` to the local service wrappers.
- Created the persistent slot cache directory under `runtime/state/slot-cache`.
- Kept the change local to the service wrapper; no OpenClaw code was modified.

## Tests

### 1. Cold Caveman bootstrap

- Command: `openclaw agent --agent caveman --session-key stage5b-bootstrap-20260815T132750 --message Hello --json`
- Expected: establish a warm bootstrap on the current server.
- Observed: completed successfully.
- Status: PASS

### 2. Native slot save

- Endpoint: `POST /slots/0?action=save`
- Request body: `{"filename":"stage5b-slot-20260815T132750.bin"}`
- Expected: save the current slot cache to the persistent slot directory.
- Observed: succeeded with `n_saved=3306`, `n_written=71605268`, `save_ms=42.555`.
- Status: PASS

### 3. Server restart

- Action: `launchctl kickstart -k gui/501/com.openclaw.kimi-llama-server`
- Expected: server PID changes.
- Observed: PID changed from `16372` to `16721`.
- Status: PASS

### 4. Native slot restore

- Endpoint: `POST /slots/0?action=restore`
- Request body: `{"filename":"stage5b-slot-20260815T132750.bin"}`
- Expected: restore the saved slot into the restarted server.
- Observed: succeeded with `n_restored=3306`, `n_read=71605268`, `restore_ms=35.027`.
- Status: PASS

### 5. Post-restart reuse probe

- Command: `openclaw agent --agent caveman --session-key stage5b-reuse-20260815T132750 --message ping --json`
- Expected: materially warm reuse, similar to Stage 5A warm follow-up.
- Observed: request completed, but it evaluated essentially the full prompt again.
- Status: FAIL

## Measurements

### Cold baseline

- `runId`: `44c0899f-d65a-4f22-bea6-6d71ffa4c2aa`
- `sessionId`: `c4c7b578-17a7-4729-afd0-a8f0b115e228`
- `durationMs`: `235184`
- `promptTokens`: `3260`
- `usage.input`: `3260`
- `usage.output`: `47`
- `usage.total`: `3307`
- `lastCallUsage.cacheRead`: `0`

### Saved slot

- `slot_id`: `0`
- `n_saved`: `3306`
- `n_written`: `71605268`
- saved file: `runtime/state/slot-cache/stage5b-slot-20260815T132750.bin`
- file size: `71605268` bytes

### Restored slot

- `n_restored`: `3306`
- `n_read`: `71605268`

### Post-restart reuse probe

- `runId`: `4492da23-561e-48b7-90bc-b095bf215c8e`
- `sessionId`: `111afa3c-9f27-4e56-9bc4-66d14b1e2490`
- `durationMs`: `238582`
- `promptTokens`: `3263`
- `usage.input`: `3263`
- `usage.output`: `34`
- `usage.total`: `3297`
- `lastCallUsage.cacheRead`: `0`

### Server timing evidence

- Reuse probe prompt eval time: `223577.21 ms / 3263 tokens`
- Reuse probe eval time: `14126.94 ms / 34 tokens`
- Reuse probe total time: `237704.15 ms / 3297 tokens`
- LCP reuse after the post-restart request was not materially warm; the request behaved like a fresh cold prefill.

## Results

- The service wrapper now exposes a persistent slot-save directory and llama.cpp can save and restore a slot file across restart.
- The save and restore endpoints worked mechanically.
- The restored post-restart request did not reuse the bootstrap materially.
- The measured prompt work after restore was effectively the same as the cold bootstrap, with `cacheRead=0`.

## Problems

- Native slot persistence did not reproduce reusable Caveman bootstrap state across restart.
- The restored request still processed roughly the full prompt instead of a small suffix.
- This appears to be a durability boundary in the slot/KV save-restore path for the current Caveman bootstrap shape, not an OpenClaw issue.

## Decisions

- Keep the persistence mechanism native to llama.cpp slot save/restore.
- Do not introduce a Caveman-specific persistence layer.
- Treat restart durability as unsupported for now until the boundary is better understood.

## Next Phase

- Do not start Stage 5C yet.
- Document the restart boundary separately and only proceed if a new native slot/KV approach is identified or the current one is narrowed.

## Reproduction

```bash
launchctl kickstart -k gui/$(id -u)/com.openclaw.kimi-llama-server
openclaw agent --agent caveman --session-key stage5b-bootstrap-20260815T132750 --message Hello --json
curl -s http://127.0.0.1:18080/slots
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"filename":"stage5b-slot-20260815T132750.bin"}' \
  'http://127.0.0.1:18080/slots/0?action=save'
launchctl kickstart -k gui/$(id -u)/com.openclaw.kimi-llama-server
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"filename":"stage5b-slot-20260815T132750.bin"}' \
  'http://127.0.0.1:18080/slots/0?action=restore'
openclaw agent --agent caveman --session-key stage5b-reuse-20260815T132750 --message ping --json
```

## Artifacts

- [`/tmp/stage5b-detached-20260815T132750/report.json`](file:///tmp/stage5b-detached-20260815T132750/report.json)
- [`/tmp/stage5b-detached-20260815T132750/cold.stdout.json`](file:///tmp/stage5b-detached-20260815T132750/cold.stdout.json)
- [`/tmp/stage5b-detached-20260815T132750/reuse.stdout.json`](file:///tmp/stage5b-detached-20260815T132750/reuse.stdout.json)
- [`/tmp/kimi-llama-server.launchd.log`](file:///tmp/kimi-llama-server.launchd.log)
- [`/Users/pmains/Code/openclaw/kimi/runtime/state/slot-cache/stage5b-slot-20260815T132750.bin`](/Users/pmains/Code/openclaw/kimi/runtime/state/slot-cache/stage5b-slot-20260815T132750.bin)
