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

## Clean-tree baseline (2026-08-17, pre-6E)

Per operator directive, 6E runs only from a clean, recorded revision.

- Workspace repo HEAD: `e90860b` (`chore: track service-progress reports...`);
  service-hardening committed separately as `6a9f239`; scratch probes stashed
  (one embedded a dev gateway token — never committed).
- openclaw-src HEAD: `18ffc771c54` — contains the 6D.2 canonicalization
  (`d8acd4a5f43`), the 6E status STALE-reconciliation (`f3cd48c5e8f`), and the
  COLD-restart prefill fix (`18ffc771c54`).
- llama.cpp / live-runtime: `0a6b2df63` (runtime/live/COMMIT, stage-6a6
  temp-context pool recycle fix).
- Pre-6E registry snapshot preserved:
  `dev-openclaw/state/warm-state/preserved/registry-2026-08-17T10-04-40-pre-6E.json`
  (sha256 `0090d71f17aab6f408c83fb7b9e91b3ca76476509fa74c41cbfe97f4f847051c`);
  contains the incidental restart invalidation: PIDs 64991, 80177 (6D
  acceptance server), 23163, 31088 all reconciled COLD; one orphaned
  PREFILLING (pid None).
- Live server at 6E start: PID 32953, `runtime/live/bin/llama-server` with
  `--slot-save-path runtime/state/slot-cache` (new serve script flag set).
