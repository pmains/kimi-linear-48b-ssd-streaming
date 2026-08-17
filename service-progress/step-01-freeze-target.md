# Stage 1 Report - Freeze the Target

## Status

PASS

## Objective

Lock the work to one target only: `caveman` using local Kimi Linear 48B.
Do not broaden scope to other agents or other model rollouts.

## What Changed

- Rewrote `SERVICE-ROADMAP.md` into a caveman-only execution plan.
- Kept the Kimi service work scoped to the local Kimi path for `caveman`.
- Left all other OpenClaw agents on their existing DeepSeek defaults.

## Result

The target is now explicit and narrow:

- `caveman` is the only agent in scope.
- Local Kimi Linear 48B is the only model path under test.
- DeepSeek remains the fallback only for `caveman`.
- No other agent configs are being touched.

## Notes

This stage is about discipline, not performance.
The point is to stop the project from turning into a fleet-wide model migration.

## Next

Proceed to Stage 2: make the Kimi service boring and infrastructure-like.

