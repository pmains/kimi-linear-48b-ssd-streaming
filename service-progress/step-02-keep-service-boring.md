# Stage 2 Report - Keep the Kimi Service Boring

## Status

PASS

## Objective

Make the local Kimi service behave like boring infrastructure:

- explicit defaults
- launchd-managed
- loopback-only
- single-instance
- predictable logs

## What Changed

- Updated `tools/serve_kimi_local.sh` to use explicit environment-driven defaults.
- Updated `tools/serve_kimi_local.launchd.sh` to:
  - read the same service knobs from the environment
  - refuse duplicate starts when an existing server is already running
  - write a pidfile for cleaner status handling
  - preserve lifecycle logging on exit
- Updated `tools/com.openclaw.kimi-llama-server.plist` with explicit environment variables:
  - `KIMI_BIN`
  - `KIMI_CACHE_MB`
  - `KIMI_CTX`
  - `KIMI_HOST`
  - `KIMI_PORT`
  - `KIMI_STREAM_EXPERTS`
  - `KIMI_EXPERT_CACHE_MODE`
- Reloaded the LaunchAgent so the live service picked up the new config.

## Results

Verified live service state:

- `launchctl print` shows the LaunchAgent loaded with explicit Kimi env vars.
- `curl http://127.0.0.1:18080/health` returns `{"status":"ok"}`.
- `curl http://127.0.0.1:18080/v1/models` returns the Kimi model as expected.
- The live wrapper log shows the current start using the explicit defaults.

## Notes

This stage is about reliability and repeatability, not performance tuning.
The service now has one obvious home and one obvious configuration path.

## Next

Proceed to Stage 3: measure the real caveman failure boundary.

