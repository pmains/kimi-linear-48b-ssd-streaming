# Stage 6A.3 - Canonical Resolved Execution Context

## Status

BLOCKED

## Objective

Replace the duplicated prefill resolution path with a reusable context derived
from the successful ordinary-agent preparation path.

## What Changed

- Added a new prefill bridge and revised `prefillWithStableBootstrapForAgent(...)`
  so it no longer resolves a model/auth path internally.
- Added a resolved-context return shape to the simple-completion helper so the
  prefill bridge can accept already-prepared values.
- Kept the prefill completion budget at `maxTokens: 1`.
- Added/updated unit coverage to prove the prefill bridge does not re-enter the
  independent auth/model resolver from the prefill call itself.

## Validation

- `./node_modules/.bin/vitest run src/agents/simple-completion-runtime.test.ts`
  - PASS

## Live Probe

Attempted a live smoke probe through the new prefill seam.

Result:

- the ordinary simple-completion resolver still hits legacy auth-profile
  migration on this workspace when invoked directly
- that confirms the remaining missing seam is not just a prefill wrapper issue;
  the canonical ordinary-turn context still needs to expose the resolved auth
  material/fingerprint from the real turn-prep boundary

## Blocker

The current resolved-context extraction is still anchored to the wrong source
for live Caveman use. The ordinary turn already resolves agent/session/model
state, but the reusable context we need has to be threaded from that boundary
with its auth material intact.

Until that ordinary-turn context is exposed, the live acceptance gate remains
blocked.

## Next Step

Derive a canonical turn-prep context from the ordinary agent path and feed
prefill from that context, rather than from the simple-completion helper.

