# Stage 6E — Prefill Invalidation (live)

## Status

**PASS (2026-08-17)** — full live sequence observed end to end via the real
system's events. Operator refinement applied: because the original bootstrap is
restored, the recovered fingerprint returns to **fp A** (identity, not
chronology).

Final observed transition chain (driver log + registry + `prefill status`):

    READY(fp A, PID A=32953)
    → mutate acp.enabled=false (hides acp-router skill → tool catalog changes)
    → `prefill status` reconciles READY → STALE (same PID)
    → restore bootstrap byte-identical
    → prefill → READY(fp A, PID 32953)   [fingerprint returned to A]
    → controlled restart → PID B=35859
    → `prefill status` reconciles READY → COLD (new PID)
    → cold prefill on PID B
    → READY(fp A, PID 35859)
    → final config byte-identical to preserved original

Three orchestration/harness failures occurred during the run; all three were
driver defects, none implementation failures (documented below). The state
machine under test behaved correctly at every transition.

Purpose-built observability (registry, CLI status, progress reporting,
acceptance artifacts) replaced the 6A.4 boundary-trace instrumentation for
this stage; the stash remains preserved as a fallback only.

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

Acceptance run dir:
`dev-openclaw/state/stage6e-acceptance/2026-08-17T17-30-37Z/`
(`driver.log`, `progress.json`, `registry.phase{0,1,2,3,5,6}.json`,
`status.{mutated,restarted}.out.json`, `prefill.{establish,restore,final}.out.jsonl`).

Final registry entry (`caveman`/`kimi-linear-48b`):

    bootstrapFingerprint: ffd6319a76d8f0ace69c55d16ab2592b8d17d0dd4fa33f55ae3cf456a9919c59  (fp A)
    status: READY
    serverPid: 35859

Live server pidfile `/tmp/kimi-llama-server.pid` = 35859 (alive).

Observed transition timeline (driver log, local time):

    10:30:38  prefill[establish] launched
    10:32:53  READY on pid 32953 (fp=ffd6319a…) [establish]
    10:32:53  config mutated: acp.enabled=false (sha 9a46a292…)
    10:34:57  status[mutated] → entry STALE pid=32953 → PASS (bootstrap invalidation)
    10:34:57  config restored byte-identical (sha 7fe3eb62…); prefill[restore] launched
    10:37:12  READY on pid 32953 (fp=ffd6319a…) [restore] → fingerprint returned to A
    10:37:18  controlled restart: PID A 32953 → PID B 35859 → PASS
    11:00:27  status[restarted] → entry COLD pid=32953 → PASS (process invalidation)
    11:00:27  prefill[final] launched (cold prefill on PID B)
    11:18:46  READY on pid 35859 (fp=ffd6319a…) [final] → fingerprint A on PID B
    final     config byte-identical to preserved original

Final cold prefill (phase 6, genuinely cold on the fresh PID):

    llama-server: prompt eval time = 974919.13 ms / 20989 tokens (21.53 tok/s)
    CLI:          status READY, serverPid 35859, promptTokens 20989,
                  evaluatedTokens 20989, cacheReadTokens 0, outputTokens 1,
                  totalTokens 20990, elapsedMs 1089767

## Result

**PASS** — every transition observed live via `prefill status` / the registry,
in order, with recovery to READY, and the original configuration restored
byte-identical (sha256 `7fe3eb62d0e31bdc7f8b9e039bb0b7cc3f60dd0aa7b13c918f1809654f1ddedd`;
`acp` absent — no experimental mutation remains).

Three properties proven independently:

1. **Bootstrap invalidation** — `READY(fp A, PID A) → STALE` when a genuinely
   stable input changed (`acp.enabled=false` removed the acp-router skill from
   the effective tool catalog).
2. **Determinism/recovery** — restoring the original bootstrap produced the
   *same* fp A (`ffd6319a…`), then `READY(fp A, PID A)`.
3. **Process invalidation** — killing PID A produced `COLD` on the new PID,
   then recovery as `READY(fp A, PID B=35859)`.

Stage 6 as a whole therefore passes: explicit prefill of the exact bootstrap
outside a normal turn, fingerprint-tracked warm state, observable CLI
status, live PID invalidation, and subsequent-session reuse (6D:
`cacheRead=20473`, 735 evaluated tokens).

## Harness failures during the run (driver defects only)

All three failures below were defects in `tools/stage6e_acceptance.sh`; the
state machine under test behaved correctly at every transition.

1. **First driver run (PID 34743)** — `log()` used `tee`, so inside
   `$(wait_for_ready …)` the command substitution captured the log line plus
   the fingerprint; the exact-match lookup returned `MISSING` even though the
   registry/`status.mutated.out.json` already showed the entry reconciled to
   STALE. Fixed: log → stderr.
2. **Second driver run (PID 35271)** — phases 1–4 passed; crashed at phase 5
   with `$3: unbound variable` (`progress()` read three args while phases 5/6
   call it with two). Fixed: `progress()` arity + `STAGE6E_RESUME_FROM`.
3. **Resume attempts** — first resume loaded empty facts because the buggy
   `progress()` had persisted keys under wrong names (and a relative
   `STAGE6E_OUT` broke the redirect); second resume extracted `FP_A="="` due to
   an awk field-index bug. Fixed: resume derives facts from ground truth
   (driver log, pidfile, config backup).

## Blockers / Follow-up

- 6E waited on the 6D verdict; 6D is PASS, so 6E ran and passed.
- The stashed 6A.4 boundary-trace instrumentation was preserved through 6D/6E
  as a diagnostic fallback only. It was not needed; review afterward: extract
  any generally useful observability into a clean commit or discard — do not
  preserve debugging instrumentation merely because it once proved useful
  (operator decision 2026-08-16).

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
