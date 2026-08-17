# Stage 6E — Prefill Invalidation (live)

## Status

NOT STARTED — protocol defined 2026-08-16 (after 6D acceptance; 6E runs only
once 6D has a verdict). Purpose-built observability (registry, CLI status,
progress reporting, acceptance artifacts) replaces the 6A.4 boundary-trace
instrumentation for this stage; the stash is preserved as a fallback only.

## Objective

Prove the two invalidation dimensions live, independently, using the real
system's events (not unit tests):

- **Semantic identity changed** → bootstrap mutation flips READY to STALE.
- **Physical cache owner disappeared** → server restart flips READY to COLD.

Plus recovery: a manual prefill returns the state to READY in both cases.

## Protocol (narrow, per operator decision 2026-08-16)

Starting point: `READY(PID 80177, fingerprint A)` (the 6D acceptance server).

1. **Mutation → STALE**
   - Mutate a fingerprinted bootstrap input (one of: provider-ready system
     prompt content, effective tool catalog) for `caveman`/`kimi-linear-48b`.
   - `openclaw prefill status` must reconcile the entry to **STALE** (new
     fingerprint, old entry marked stale).
2. **Recovery via intended bootstrap → READY**
   - Restore the intended bootstrap B (the one Caveman actually uses) and run
     `openclaw prefill caveman kimi-linear-48b`.
   - Registry must reach **READY(fingerprint B, PID 80177)** — same server,
     new fingerprint.
3. **Restart → COLD**
   - Restart llama-server (new PID).
   - `openclaw prefill status` must reconcile the entry to **COLD** with the
     new PID (regardless of whether any slot-cache files still exist).
4. **Recovery → READY**
   - Re-run `openclaw prefill caveman kimi-linear-48b`.
   - Registry must reach **READY(new PID)**.

5. **Restore**: leave the bootstrap configuration exactly as it was before the
   experiment (Caveman must not be left modified).

PASS = each transition observed live via `prefill status` / registry, in
order, with recovery to READY, and the original configuration restored.

## Evidence

_(pending)_

## Result

_(pending)_

## Blockers / Follow-up

- 6E waits on the 6D verdict.
- The stashed 6A.4 boundary-trace instrumentation is preserved through 6D/6E
  as a diagnostic fallback only. If Stage 6 passes without needing it, review
  afterward: extract any generally useful observability into a clean commit or
  discard — do not preserve debugging instrumentation merely because it once
  proved useful (operator decision 2026-08-16).
