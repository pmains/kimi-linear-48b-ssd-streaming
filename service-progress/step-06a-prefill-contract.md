# Stage 6A — Prefill Contract

## Status

BLOCKED

## Hypothesis / Requirement

Canonical `prefill(agent_id, model_id)` should reuse the same agent/model/bootstrap
resolution and canonical bootstrap construction as an ordinary agent turn, but
skip conversational side effects while leaving warm provider state resident.

## Implementation

Inspected the public OpenClaw surface and the Codex app-server turn pipeline.
The stable/public context-engine API exists for bootstrap/assembly work
(`bootstrapHarnessContextEngine`, `resolveBootstrapContextForRun`, and the
plugin SDK context-engine hooks), but it does not provide the full
agent/model/bootstrap + provider-invocation seam required for a canonical
prefill.

The actual ordinary-turn path still lives inside the app-server attempt runner
and thread lifecycle internals (`runCodexAppServerAttempt`,
`startOrResumeThread`, `buildTurnStartParams`, and the prompt/projection helpers
they call). Those are generated/internal bundle symbols, not a source-level API
this repository should depend on directly.

Conclusion: OpenClaw does **not** currently expose a stable source-level seam
for `prefill(agent_id, model_id)` that both:

- reuses the canonical ordinary-turn bootstrap builder, and
- suppresses only the conversational/session side effects.

Smallest source-level OpenClaw refactor required:

1. Extract the shared canonical turn bootstrap into a source module that both
   the ordinary agent turn path and prefill can call.
2. Expose a stable `prefill(agent_id, model_id)`-style entry point on top of
   that shared bootstrap.
3. Split lifecycle effects from bootstrap construction so prefill can skip
   session/turn creation, visibility, and watchdog behavior while preserving the
   same resolved prompt and provider request.
4. Keep the first implementation model-agnostic; Caveman/Kimi/llama-server stay
   as validation targets, not part of the abstraction.

## Evidence

Public OpenClaw docs and types show:

- context-engine lifecycle support (`bootstrapHarnessContextEngine`,
  `resolveBootstrapContextForRun`, `buildBootstrapContextForFiles`)
- Codex app-server turn semantics for `thread/start`, `thread/resume`, and
  `turn/start`

But no public API that combines:

- canonical agent/model resolution,
- canonical bootstrap/system-prompt construction,
- provider invocation,
- and suppressed conversational side effects.

The codex app-server docs describe the current ordinary-turn projection model
and explicitly note that the built-in harness applies context-engine assembly
by projecting it into developer instructions and the current turn prompt. That
is useful for understanding the normal path, but it is not yet a reusable
prefill seam.

## Result

OpenClaw currently lacks the reusable source-level seam Stage 6A needs.
Repo-local implementation should pause until OpenClaw exports a shared
bootstrap/prefill helper from source rather than forcing the Kimi repository to
depend on generated bundle internals.

## Blockers / Follow-up

- Need OpenClaw source refactor before any repo-side prefill implementation.
- The refactor should be the smallest extraction from the normal turn path that
  preserves canonical bootstrap construction for both ordinary turns and
  prefill.
