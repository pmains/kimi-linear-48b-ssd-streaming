# Stage 6D.2 — ACP/Ambient Determinism Fix (6D rerun remediation)

## Status

**PASS (6D rerun, 2026-08-17)** — the full 6D acceptance now passes end to end:
cold llama-server restart -> CLI prefill -> READY on the live PID -> brand-new
Caveman session through the real ordinary-agent path -> `cacheRead = 20,473`
(96.5% of the 21,209-token prompt), evaluated suffix **735 tokens**, ordinary
turn **46 s** (vs ~22 min cold). All 10 acceptance criteria green.

This stage records the remediation for the 6D rerun's first FAIL (see
`step-06d-prefill-acceptance.md` for the preserved history: initial 6D FAIL ->
6D.1 PASS -> 6D rerun FAIL -> 6D.2 PASS).

## Objective

The 6D rerun (committed revision `96e31106a1b`) failed with a NEW mechanism:
the deterministic invariant (6D.1) passed, the bootstrap surfaces still
diverged in production. The CLI prefill and the ordinary gateway session
produced byte-different stable prefixes, so llama-server could not reuse the
warmed KV (`f_sim_best = 0.414`, full 21,205-token eval, ~22 min, cacheRead 0).

Root cause: **process-local runtime state leaked into the agent-stable
surface** in three places, each sufficient to break server-side prefix reuse:

1. `src/skills/loading/plugin-skills.ts` — the acpx plugin's skills (the
   `acp-router` skill) were gated on `isAcpRuntimeSpawnAvailable(...)`, which
   consults the **process-local ACP backend registry**. The gateway process
   registers the acpx backend ("embedded acpx runtime backend registered
   lazily"); the CLI prefill process does not. Result: `acp-router` present in
   the ordinary session's `<available_skills>`, absent in the prefill's
   (first system-prompt divergence at char 5,770, ~742 chars).
2. `src/agents/tools/sessions-spawn-tool.ts` — the tool's ADVERTISED surface
   (displaySummary/description/parameters) was built from the same
   process-local check. The wire prompt renders the tools JSON first (the
   Kimi chat template emits `<|im_system|>tool_declare<|im_middle|>{tools}`),
   so the divergence landed at wire-token ~8,779 inside `sessions_spawn`'s
   definition — the FIRST wire divergence (the server's LCP stops there).
3. `src/cli/prefill-cli.runtime.ts` / config resolution — the CLI config load
   auto-enabled ambient channel plugins with the default `ambientEnvTriggers:
   "allow"` policy, while the gateway defaults to `"suppress"` (run.ts:699,
   no `--ambient-channels`). The shell's `SLACK_APP_TOKEN`/`SLACK_BOT_TOKEN`
   therefore activated the slack plugin in the prefill process only; the
   `slack` skill appeared in the prefill's `<available_skills>` and was absent
   in the ordinary session's (second divergence, char ~10,736).

The 6D.1 report had flagged the residual risk in its Problems section
("resolvePluginSkillDirs still consults process-level ACP availability for
ACPX skill exposure; no ACPX skills exist in the dev surface today") — the
"no ACPX skills in the dev surface" assumption was wrong: `acp-router` is an
ACPX-classified plugin skill in the dev surface, and it broke the invariant.

## Changes

- `src/skills/loading/plugin-skills.ts` — acpx skill exposure gated on config
  policy only (`config.acp.enabled !== false`), mirroring
  `resolveCanonicalAcpSpawnAvailable`; the process-local backend check no
  longer influences the stable surface. Actual ACP enforcement is unchanged
  (call-site).
- `src/agents/tools/sessions-spawn-tool.ts` — split the tool's ADVERTISED
  surface (`acpAdvertised` from `resolveCanonicalAcpSpawnAvailable`) from the
  execute-time enforcement (`acpAvailable` from `isAcpRuntimeSpawnAvailable`,
  kept process-local). The advertised schema/description render inside the
  agent-stable bootstrap and must be deterministic across processes.
- `src/cli/command-config-resolution.ts` + `src/cli/capability-cli/shared.ts`
  — threaded an optional `ambientEnvTriggers` through
  `resolveCommandConfigWithSecrets` / `resolveLocalCapabilityRuntimeConfig`
  (default unchanged: "allow").
- `src/cli/prefill-cli.runtime.ts` — the prefill resolves its config with
  `ambientEnvTriggers: "suppress"`, matching the gateway's default ambient
  policy, so ambient-only channel plugins (slack) do not enter the prefill
  surface.
- Tests updated to the canonical semantics: `plugin-skills.test.ts`
  (acpx-skill exposure is config-policy-only; backend registration changes no
  longer alter the memo) and `sessions-spawn-tool.test.ts` (advertisement by
  config policy regardless of backend presence/health; execute-time rejection
  tests unchanged and passing).
- `scripts/dev/stage6d-acceptance.ts` — corrected dev-gateway dispatch path
  (in-process `OPENCLAW_GATEWAY_URL=http://127.0.0.1:18790` set after config
  load, per the 6D finding), registry entry selection by expected
  (agent, model, fingerprint) instead of position, non-fatal
  `session_status` pre-call (a brand-new session key is expected to be
  unknown; the dispatch creates the session), and a cheap dev-gateway health
  preflight before the ~20-minute prefill commitment.

## Results (acceptance run 2026-08-17 16:08–16:29 UTC)

- Cold restart: 23163 -> fresh PID **31088** healthy on :18080.
- CLI prefill (committed seam + 6D.2 fixes): **READY** in 1,110 s, prompt
  20,989 tokens @ ~22 t/s, registry entry
  `caveman/kimi-linear-48b/ffd6319a76d8...`, serverPid 31088 matches live.
- Ordinary turn (brand-new session `agent:caveman:stage6d-...`, dispatched
  through the dev gateway): runId `35cd9e5b-c13e-430c-b3a4-9a27c1d32ecf`.
- Server: slot selected by LCP similarity **f_sim_best = 0.982**
  (was 0.414), f_keep 0.992; prompt eval **735 tokens in 44.4 s**;
  `cacheRead = 20,473` (96.5% of 21,209 input tokens); output 2 tokens;
  elapsed **46 s** total vs ~22 min cold baseline.
- Verdict: all 10 criteria PASS (prefill without conversational turn; READY;
  recorded PID matches live; cacheRead > 0; small evaluated suffix < 1000;
  latency materially below cold; no fallback; no abort; no restart).
- Registry selection anchored by expected (agent, model, fingerprint):
  `ffd6319a76d8f0ace69c55d16ab2592b8d17d0dd4fa33f55ae3cf456a9919c59`.

## Problems / notes

- The 6D rerun's first attempt cost a full cold prefill + a full 21,205-token
  eval (~22 min) before the mechanism was identified; the diagnostic path
  after that (trajectory limit/redaction bumps + dead-provider dispatch)
  was temporary and fully reverted.
- `resolvePluginSkillDirs`'s memo identity still tracks `acpRuntimeAvailable`
  (now config-derived); harmless, retained for minimal diff.
- Other process-local inputs in the stable surface remain a review item: the
  6D.1 report's Problems section also noted `resolvePluginSkillDirs` and
  plugin-registry state. The rerun's diff (prefix byte-identical at 30,931
  chars) validates the current set for this dev configuration.
- The warmed prefix now represents the gateway-default surface (ambient
  channels suppressed). A future operator running the gateway with
  `--ambient-channels` would need a matching prefill option (out of scope).

## Next Phase

Stage 6E (invalidation and restart behavior) may now proceed per
`service-progress/step-06e-prefill-invalidation.md`, starting from
`READY(PID 31088, fingerprint ffd6319a76d8...)`.

## Reproduction

```bash
cd openclaw-src
# The acceptance (cold restart -> prefill -> fresh session -> cacheRead proof):
node --import tsx scripts/dev/stage6d-acceptance.ts
# Deterministic surface invariant (no live Kimi):
node scripts/run-vitest.mjs run --config test/vitest/vitest.agents.config.ts \
  src/agents/stable-bootstrap-invariant.test.ts
# Regression suites:
node scripts/run-vitest.mjs run --config vitest.config.ts \
  src/skills/loading/plugin-skills.test.ts src/agents/tools/sessions-spawn-tool.test.ts \
  src/cli/prefill-cli.runtime.test.ts
pnpm tsgo:core
```
