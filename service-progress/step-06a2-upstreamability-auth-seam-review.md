# Stage 6A.2 - Upstreamability and Auth-Seam Review

## Status

BLOCKED

## Objective

Review the current source-level prefill implementation as a candidate upstream
OpenClaw contribution and determine whether it uses the canonical ordinary-turn
resolution/auth seam.

## Findings

- The ordinary turn already resolves the execution environment before bootstrap
  prep begins:
  - `prepareAgentCommandExecution(...)` resolves agent/session/workspace/config
    state and produces `PreparedAgentCommandExecution`.
  - `prepareEmbeddedSessionState(...)` resolves run context and persists the
    non-model session state before provider/model selection.
  - `resolveEmbeddedModelSelection(...)` resolves provider/model/auth for the
    turn.
  - `runEmbeddedAgentAttempt(...)` passes that resolved state into
    `runEmbeddedAttempt(...)`.
  - `prepareEmbeddedAttemptSetup(...)` then assumes provider metadata is already
    ready and begins bootstrap prep.
- The new `prefillWithStableBootstrapForAgent(...)` helper still re-enters
  `resolveSimpleCompletionSelectionForAgent(...)` and
  `prepareSimpleCompletionModel(...)`, which in turn can trigger legacy
  auth-profile discovery and migration checks.
- That means prefill is currently using a second resolution pipeline instead of
  consuming the ordinary turn's already-resolved execution context.

## Classification

`PREFILL_BUG`

Root cause: the helper is calling the wrong source-level seam for auth/model
preparation. The underlying missing ergonomic seam is a reusable resolved
execution context, but the immediate bug is that prefill ignores the one already
assembled by the ordinary turn path.

## Minimal Correct Seam

- Keep ordinary agent resolution as the source of truth.
- Thread the resolved environment into prefill instead of re-resolving it from
  `cfg`/`agentId`/`modelRef`.
- Reuse the canonical stable bootstrap directly from the resolved execution
  context.
- Call the ordinary provider path with `maxTokens: 1` and discard output.

If a reusable type is introduced, it should be derived from the ordinary-turn
boundary and contain only:

- `cfg`
- `agentId`
- `agentDir`
- `workspaceDir`
- resolved `provider` / `modelId`
- resolved `model`
- resolved auth material and fingerprint
- stable bootstrap and bootstrap fingerprint

## Security Notes

- Prefill should not independently discover, migrate, or reinterpret
  credentials.
- Re-running auth discovery from a utility helper expands the credential
  surface and can surface migration failures unrelated to the actual warm-state
  experiment.

## Next Gate

The live acceptance gate can proceed only after prefill is wired through the
canonical resolved execution context.

