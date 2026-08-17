# Stage 6A.1 Report - Canonical Bootstrap Seam

## Status

PARTIAL

## Objective

Extract the canonical Codex app-server bootstrap assembly into a shared source-level helper so a future `prefill(agent_id, model_id)` entry point can reuse the exact same agent/model/bootstrap resolution as an ordinary turn, then stop before conversational lifecycle side effects.

## Environment

- Workspace: `/Users/pmains/Code/openclaw/kimi`
- Source tree: `openclaw-src/`
- Validation shell: Node 26.0.0
- Repo test runner dependencies: not installed in this shell (`tsx` was unavailable)

## Changes

- `openclaw-src/extensions/codex/src/app-server/run-attempt-bootstrap.ts`
  - Added `prepareCodexAttemptBootstrap(...)`, a shared helper that composes the canonical connection, runtime, tools, context, and prompt preparation steps without starting a turn.
- `openclaw-src/extensions/codex/src/app-server/run-attempt.ts`
  - Switched the ordinary app-server turn path to use `prepareCodexAttemptBootstrap(...)` for the bootstrap assembly portion.
  - Kept the turn lifecycle, routing, and finalization behavior unchanged.
- `openclaw-src/extensions/codex/src/app-server/run-attempt-bootstrap.test.ts`
  - Added a focused test that asserts the helper preserves the canonical connection -> runtime -> tools -> context -> prompt sequence.

## Tests

- Attempted: `node scripts/run-vitest.mjs run --config test/vitest/vitest.unit.config.ts extensions/codex/src/app-server/run-attempt-bootstrap.test.ts`
  - Expected: run the new helper test.
  - Observed: failed before test execution because `tsx` is not installed in this workspace.
- `git diff --check`
  - Expected: no patch formatting issues.
  - Observed: passed.

## Measurements

- No runtime measurements yet.
- No inference-side behavior was changed.
- The new helper is a structural seam only.

## Results

- The ordinary Codex app-server run path now goes through a shared bootstrap helper.
- The helper is narrow enough for a later prefill command to stop after canonical bootstrap construction and skip turn creation / lifecycle effects.
- The refactor did not change the turn runner’s external behavior on paper.

## Problems

- The repo’s normal Vitest runner could not be exercised here because the workspace is not bootstrapped and `tsx` is missing.
- That means the new test is added but not yet executed in this shell.

## Decisions

- Keep the seam at the bootstrap boundary for now:
  - connection resolution
  - runtime preparation
  - tool preparation
  - context assembly
  - prompt assembly
- Do not introduce the prefill registry or CLI yet; those belong to the next Stage 6A substeps.

## Next Steps

- Add the prefill warm-state registry and status tracking.
- Expose a CLI/observability surface once the state model exists.
- Run the new helper test in a bootstrapped workspace and then exercise the broader Codex app-server test slice.

## Reproduction

```bash
cd /Users/pmains/Code/openclaw/kimi/openclaw-src
node scripts/run-vitest.mjs run --config test/vitest/vitest.unit.config.ts \
  extensions/codex/src/app-server/run-attempt-bootstrap.test.ts
```

## Artifacts

- `/Users/pmains/Code/openclaw/kimi/openclaw-src/extensions/codex/src/app-server/run-attempt-bootstrap.ts`
- `/Users/pmains/Code/openclaw/kimi/openclaw-src/extensions/codex/src/app-server/run-attempt.ts`
- `/Users/pmains/Code/openclaw/kimi/openclaw-src/extensions/codex/src/app-server/run-attempt-bootstrap.test.ts`
