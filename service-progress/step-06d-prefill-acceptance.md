# Stage 6D — Prefill Acceptance (cold restart → explicit prefill → reuse)

## Status

**PASS (6D rerun, 2026-08-17)** — see the timeline below. The original 6D
FAIL is preserved as authoritative history; each remediation is recorded as a
separate stage. The final 6D rerun passed all 10 acceptance criteria
(cacheRead = 20,473, evaluated suffix 735 tokens, ordinary turn 46 s).

Timeline:

1. **6D (initial) — FAIL** — prefill half PASSED; reuse half FAILED with the
exact mechanism identified (surface divergence: 52 vs 41 tools, 147 diff
regions, session/runtime metadata block in the prefix).
2. **6D.1 — PASS (deterministic invariant)** — Option B remediation:
canonical agent-stable bootstrap builder shared by prefill and ordinary
sessions; session-varying material moved below the cache boundary; invariant
test proves `prefix_N(cli_prefill) == prefix_N(session_A) == prefix_N(session_B)`
(hermetic). Stage 6D itself remained FAIL pending a live rerun.
3. **6D rerun #1 (2026-08-17, revision `96e31106a1b`) — FAIL** — new
mechanism: process-local ACP/ambient state leaked into the stable surface
(acp-router skill, sessions_spawn tool metadata, ambient slack channel).
Server reused nothing: f_sim 0.414, full 21,205-token eval, cacheRead 0.
4. **6D.2 — PASS (remediation)** — canonicalized the three leaks; see
`service-progress/step-06d2-acp-determinism-fix.md`.
5. **6D rerun #2 (2026-08-17) — PASS** — full acceptance green;
`cacheRead = 20,473`, evaluated suffix 735 tokens, 46 s ordinary turn.

Original 6D report (historical FAIL) follows below, unchanged except for this
status header.

## Objective

Prove the committed `/prefill` path end to end, per SERVICE-ROADMAP stage 6D:

1. Restart the server so the starting state is genuinely cold.
2. Run `openclaw prefill caveman kimi-linear-48b`.
3. Verify the result becomes `READY`.
4. Record the server PID.
5. Create a brand-new Caveman session.
6. Send one tiny ordinary request.

PASS requires: prefill completes without creating a normal conversational
turn; status becomes `READY`; the recorded server PID matches the live
inference-server PID; the new Caveman session selects the warm prefix by
LCP/cache reuse; the ordinary request evaluates only a small suffix;
`cacheRead > 0`; latency materially below the cold baseline; no fallback; no
abort; no restart.

### Acceptance-signal hierarchy (operator decision 2026-08-16)

- **Primary signal: `cacheRead > 0` observed in the ordinary turn's usage**
  (trajectory `cacheRead` / `cached_tokens` / `prompt_tokens_details`).
- **Corroboration: server-side evaluated-suffix < 1000 tokens** (prompt-eval
  line). If trajectory instrumentation fails to expose `cacheRead` and only
  the heuristic passes, the cacheRead criterion is recorded **PARTIAL**, not
  silently treated as equivalent.
- **Informational: latency < 300 s vs the cold baseline.** A latency miss is
  recorded but does not by itself fail cache correctness when PID continuity,
  `cacheRead`, and suffix evaluation already prove reuse.

## Environment

- Host: macOS 26.5.2 (arm64), 24 GB unified memory
- openclaw-src revision: `a2b7c96075a` (6B seam completion; dev tree clean —
  the 6A.4 boundary-trace instrumentation is stashed during the run)
- Agent: `caveman`; model: `kimi-linear-48b`
- Dev config: `dev-openclaw/config/openclaw.json`; state:
  `dev-openclaw/state/` (warm-state registry under `warm-state/registry.json`)
- Live llama-server: frozen bundle `runtime/live`, port 18080, ctx 32768,
  expert cache 4096 MiB zerocopy (per `tools/serve_kimi_local.sh` defaults)
- Orchestration: `openclaw-src/scripts/dev/stage6d-acceptance.ts` (new;
  modeled on the 6A.6 harness; orchestration only — no runtime changes)

## Run Record (2026-08-16 20:26 MST)

- Registry before: READY entry from the 6C prefill (serverPid 64991) —
  expected to invalidate READY→COLD on the PID change.
- Cold restart: old PID 64991 stopped; fresh server PID **80177** healthy on
  :18080 (health wait OK).
- Prefill: `node --import tsx prefill-cli-live.tmp.ts prefill caveman
  kimi-linear-48b --json` launched detached; registry polling in progress
  (expected READY ~20:50 MST, ~25,600-token bootstrap at ~21 t/s).
- Ordinary turn: dispatched after READY on a brand-new session
  `agent:caveman:stage6d-<ts>` through the real agent path
  (`sessions_send`-style gateway dispatch, `deliver: false`).
- Evidence captured: registry entry (status/serverPid/cachedTokens/warmedAt),
  run trajectory events (usage: cacheRead / cached_tokens), llama-server log
  prompt-eval line for the ordinary turn, elapsed seconds.

## Artifacts

- `dev-openclaw/state/stage6d-acceptance/2026-08-17T03-26-05-078Z/`
  - `manifest.json` — revision, session keys, message, timeouts
  - `run.log` — chronological run transcript
  - `prefill.stdout.jsonl` / `prefill.stderr.log` — CLI prefill output
  - `results.json` — registry, ordinary-turn usage evidence, verdict
  - `summary.md` — human-readable verdict
- Console tail: `/tmp/kimi-stage6d.out`

## Results

### Prefill half — PASS

- Cold restart: old PID 64991 → fresh PID **80177** (healthy on :18080).
- CLI prefill (`prefill caveman kimi-linear-48b --json`, committed tree
  `a2b7c96075a`): **READY** in 1,352 s, prompt 25,396 tokens @ ~21 t/s,
  output 1 token, registry entry `caveman␟kimi-linear-48b␟0976a1a4…`,
  serverPid 80177 matches the live server. No conversational turn created.

### Reuse half — FAIL

- Ordinary turn (fresh session `agent:caveman:stage6d-…`, dispatched through
the dev gateway): server ran a **full prompt eval (~20 min) — cacheRead = 0**.
- Root cause: the ordinary turn's request surface diverges from the CLI
  prefill's in three classes, each sufficient to break server-side prefix
  reuse (details + diff evidence in `findings-surface-divergence.md`,
  `ordinary-turn-request-body.json`, `prefill-surface-reconstruction.json`):
  1. Tool catalog: 52 (CLI) vs 41 (agent runtime) — the gateway's owner-only
     `tools.deny` strips 11 tools from agent runs; the CLI ignores it.
  2. Tool metadata + system sections: 147 diff regions between system
     prompts (descriptions, ## Skills, Gateway instructions).
  3. Session/runtime metadata block: the ordinary system prompt embeds the
     session key + runtime descriptor (`Runtime: agent=caveman | session=… |
     channel=webchat | capabilities=…`) — only the agent runtime can produce
     it; a CLI prefill cannot reproduce a future session's block.
- The warm-state registry behaved correctly: prefill fingerprint
  `0976a1a4`; the ordinary surface computes a different fingerprint;
  `resolveWarmState` found no match (honest miss, no false positive).
- The 6A.6 within-session proof remains valid (cacheRead=22,410): reuse
  works when the prefill is a turn in the same session.

### Verdict list (per acceptance criteria)

| criterion | result |
|---|---|
| prefill without conversational turn | PASS (CLI path, 1 output token) |
| status READY | PASS (registry `READY`, 0976a1a4) |
| recorded PID matches live server | PASS (80177) |
| warm-prefix selection (cacheRead > 0) | **FAIL** (0; full eval) |
| small evaluated suffix (< 1000) | FAIL (full 25k eval) |
| latency below cold baseline | FAIL (full eval ~20 min) |
| no fallback / no abort / no restart | PASS (none observed) |

Overall: **FAIL** — the cross-session model (CLI prefill outside any
session, then a brand-new session reuses) is incompatible with the runtime
embedding session-specific metadata in the system prompt. Design decision
required (see Next Phase).

## Problems

- Fresh-session ordinary turn did a full eval (no reuse) — mechanism fully
documented in `findings-surface-divergence.md`.
- Harness dispatch hangs when targeting the real gateway (18789): it does
  not know agent `caveman`. The dev gateway (18790, dev config + dev state)
  must be running, and `OPENCLAW_GATEWAY_URL` must be set in-process AFTER
  config load (`getRuntimeConfig` appears to clobber the env). Probe with
  in-process env accepts in ~150 ms.
- No `/abort` endpoint on this llama-server build (404) — a misbehaving
  request occupies the single slot until it completes.
- The warm KV was overwritten by the failed probe eval; any re-run requires
  a fresh cold prefill (~22 min).

## Decisions

- Re-stashed the 6A.4 boundary-trace instrumentation after the diagnosis
  (kept out of the committed tree, per operator direction).
- Registry/fingerprint machinery validated as honest: it never claimed a
  match that didn't exist.

## Next Phase

6D's cross-session reuse needs a design decision:

A. **Session-scoped prefill** (6A.6 model): the prefill runs as a special
   turn in the target session; later turns in THAT session reuse. CLI
   prefill would materialize a real session.
B. **Make the bootstrap agent-stable**: remove session/runtime metadata from
   the system prompt (or move it after the user message) + align tool
   policy and tool metadata between CLI and agent runtime. Then the CLI
   prefill can match any session.
C. **Accept FAIL with the 6A.6 within-session proof** as the reuse evidence
   and re-scope 6D/6E around session-scoped prefill.

Any re-run costs a fresh cold prefill (~22 min).

## Reproduction

```bash
cd openclaw-src
node --import tsx scripts/dev/stage6d-acceptance.ts
```
