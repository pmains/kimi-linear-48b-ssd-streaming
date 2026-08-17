# Step 6C — Prefill CLI and Observability

## Status

PASS (CLI surface, registry status, and progress observability) with one
explicit boundary: the full end-to-end prefill run is in flight against the
live server during this report (see Reproduction). The CLI is implemented,
unit-tested (18 new tests), and its live status path is verified. The seam
changes required to make the CLI's prefill path actually executable were
found and fixed on the way (see Changes) — several were pre-existing gaps in
the uncommitted 6B follow-up work, which remains uncommitted and type-broken
(67 pre-existing tsgo errors, none in files this step owns).

## Follow-up (2026-08-16 19:35 MST)

- The CLI work landed as openclaw-src commit `cf4f6bde255` ("stage-6c:
  prefill CLI + warm-state status observability") — this report was written
  against the pre-commit tree (`c9c6ff96d93` + uncommitted WIP). All files
  listed in Changes are in the commit; test counts are unchanged.
- In-flight run snapshot at 19:35 MST: server task 13 at 6,144/25,600 tokens
  (progress 0.24, ~21.8 t/s) → expected READY ~19:50 MST.
- The 6B follow-up WIP (`attempt-*.ts` family, `stable-bootstrap-context.ts`)
  is NOT part of the 6C commit and remains uncommitted + type-broken (67
  tsgo errors) — commit it before treating 6D acceptance as authoritative.
- Removed the stale `NOT STARTED` placeholder
  `service-progress/step-06c-prefill-cli-observability.md`; this report
  supersedes it.
- SERVICE-ROADMAP.md updated in the same pass: 6B/6C status blocks corrected
  (heading level, two-store note), 6D status note added.

## Objective

Implement Stage 6C of SERVICE-ROADMAP.md:

    openclaw prefill <agent> [model] [--json]
    openclaw prefill status [agent] [--json]

with real-inference progress (not an elapsed-time animation), machine-readable
status, and no agent stuck-session watchdog on the prefill command.

## Changes

`openclaw-src` (dev tree; commit `c9c6ff96d93` + pre-existing uncommitted WIP):

| File | Change |
|---|---|
| `src/cli/prefill-cli.ts` (new) | Commander registration: `prefill <agent> [model]` and `prefill status [agent]`, `--json` on the parent command (Commander quirk: identically named parent/subcommand options are attributed to the parent — status reads `parent.opts().json ?? opts.json`). Errors via `runCommandWithRuntime` → stderr + exit 1. |
| `src/cli/prefill-cli.runtime.ts` (new) | Domain logic: `runPrefillCommand` (prepare → live-server check → stable-bootstrap seam with explicit `warmStateDir` → READY summary), `runPrefillStatusCommand` (registry probe + live PID), progress tracker polling llama-server's own `slot print_timing … progress =` lines from the server log tail (`/tmp/kimi-llama-server.log`, `KIMI_SERVER_LOG` override), and text/JSON renderers. |
| `src/cli/prefill-cli.test.ts` (new) | 7 tests: argv routing (agent+model, agent-only, status, status+agent), JSON/text rendering, error exit path, missing-argument error. |
| `src/cli/prefill-cli.runtime.test.ts` (new) | 11 tests: progress-line formatting, server-log progress parsing (newest line wins), status probe with READY→COLD invalidation persisted, agent filtering, missing-server reporting, run happy path with injected prepare/prefill seams (registry dir, session key, admission context asserted), cache-read accounting, and both failure paths. |
| `src/cli/program/register.subclis-core.ts` | Lazy registration entry for `prefill`. |
| `src/cli/program/subcli-descriptors.ts` | Descriptor: `prefill`, `hasSubcommands: true`. |
| `src/agents/warm-state-registry.ts` | Added `probeWarmStateRegistry`: whole-registry READY→COLD PID invalidation (persisted), used by `prefill status`; plus `WarmStateProbeTransition`/`WarmStateProbeResult` types. |
| `src/agents/simple-completion-runtime.ts` | Three pre-existing seam gaps fixed: (1) missing `resolveStateDir` import (committed 6B bug — the env-opt-in registry path would ReferenceError); (2) dead `begin` binding removed; (3) model discovery workspace fallback now binds to the explicit agent (`resolveAgentWorkspaceDir(cfg, agentId)`) instead of `resolveDefaultAgentId` — multi-agent configs previously threw `AGENT_SELECTION_REQUIRED`; (4) NEW: `stripModelRequestTimeout` — prefill completions drop `requestTimeoutMs` so interactive-turn provider timeouts (`models.providers.<id>.timeoutSeconds`, live config has 600 s) cannot abort a ~22-minute prefill. |
| `src/agents/simple-completion-runtime.test.ts` | +1 test: prefill strips the provider request timeout. 28/28 pass. |
| `src/agents/embedded-agent-runner/run/stable-bootstrap-context.ts` (untracked WIP) | Optional `admittedRunContext` field added (non-destructive). |
| `src/agents/embedded-agent-runner/run/attempt-stable-bootstrap-prefill.ts` (untracked WIP) | Threads `admittedRunContext` through `buildPrefillContext`. |

## Results

Tests (all run from `openclaw-src`):

- `prefill-cli.test.ts` + `prefill-cli.runtime.test.ts`: **18/18 pass** (cli shard).
- `simple-completion-runtime.test.ts`: **28/28 pass** (agents-core shard).
- `warm-state-registry.test.ts`: **17/17 pass** (unit-fast shard).
- CLI registration-adjacent suites: root-help, help, command-tree,
  root-command-descriptions (JSON-output governance), completion-command-tree,
  argv: **31/31 pass**.
- Full cli shard (4,739 tests): 8 failures — verified **pre-existing** (same 3
  files fail with this step's changes stashed: `logs-cli.runtime`,
  `update-cli`, `node-cli/daemon` — Linux systemd/update/linger tests on macOS).
- `tsgo` core: this step's files and the two seam files are clean; the 67
  remaining errors are the pre-existing uncommitted WIP set (`attempt-*.ts`,
  `stable-bootstrap-context.ts`) — unchanged in count.

Live verification (dev environment, live llama-server pid 64991):

- `prefill status` (text + JSON): registry path, live PID detection, and
  empty-entry rendering verified end-to-end.
- Stable-bootstrap prep through the real seam: **OK** — 52 tools, real
  caveman system prompt, real dev config.
- Full `prefill caveman kimi-linear-48b --json` launched detached (pid 77399,
  log `/tmp/kimi-prefill-cli.jsonl`); registry transitions and server task
  visibility confirmed once the request reaches llama-server.

## Problems

1. **Uncommitted 6B follow-up WIP blocks/blocked the seam.** The prefill seam
   (`prefillWithStableBootstrapForAgent`) had never run end-to-end: the WIP
   `attempt-tool-prepare.ts` reads `attempt.admittedRunContext.operationalRunInstance`,
   which the prefill context never provided → `Cannot read properties of
   undefined`. Fixed by threading an optional `admittedRunContext` (lifecycle
   correlation only — the type itself documents it is "never identity or
   authorization evidence") through the prefill context and constructing it in
   the CLI. The WIP tree (67 tsgo errors, `attempt-*.ts` family, untracked
   `stable-bootstrap-context.ts` / `attempt-stable-bootstrap-prefill.ts`) still
   needs its owner to finish and commit — do not mistake 6B as closed.
2. **Multi-agent `AGENT_SELECTION_REQUIRED`.** `resolveModelWorkspaceDir`
   falls back to the DEFAULT agent; caveman + main made every simple-completion
   preparation throw. Fixed in the seam (explicit-agent workspace binding).
3. **Provider timeout would abort prefills.** Live config `kimi-local`
   `timeoutSeconds: 600` flows into `model.requestTimeoutMs` via
   `applyConfiguredProviderOverrides`; a 22-minute prefill would be cut at
   10 min. The seam now strips the request timeout for prefill completions
   (roadmap: "a long prefill is legitimate work"). Documented in code.
4. **Dev tree cannot load the live config** (schema drift: `lastTouchedAt`,
   `agents.ownership`, `gateway.controlUi.allowInsecureAuth` rejected). The
   CLI in the dev tree must run against `dev-openclaw/config/openclaw.json`;
   production runs use the installed OpenClaw binary with the live config.
5. **Two registry stores.** The TS seam/CLI registry is
   `<stateDir>/warm-state/registry.json`; the earlier `tools/warm_state.py`
   manages `runtime/state/warm-state.json` with a different fingerprint input
   (captured 6A.4 body). The CLI and seam share the TS store; the Python tool
   is a separate management surface. Convergence is a follow-up decision.
6. **Commander parent/subcommand option shadowing.** `--json` declared on both
   the parent and `status` is attributed to the parent; status reads the
   inherited value. Covered by tests.
7. `[plugins] llama-cpp failed to load … unicorn-magic` warning on every dev
   run: the dev tree's `dist/extensions` are stale relative to its node_modules.
   Non-fatal (config-provided model resolution succeeds); noted for cleanup.

## Decisions

- CLI runs the prefill **in-process** via the simple-completion seam (local
  transport, like `infer model run --local`): no gateway RPC timeout, no agent
  stuck-session watchdog, no conversational turn created.
- Registry bookkeeping is **always enabled for the CLI** (explicit
  `warmStateDir`); the env opt-in (`OPENCLAW_WARM_STATE_REGISTRY=1`) remains
  for other callers.
- Progress uses **llama-server's own logged progress fraction**
  (`slot print_timing … n_tokens = N, progress = P`), the same authoritative
  signal the 6A.6 harness parses; total/ETA are derived (N/P), never invented.
  Progress renders only on a TTY and never to stdout (JSON-safe).
- `prefill status` applies the registry's READY→COLD PID rule across all
  entries via the new `probeWarmStateRegistry` (persisted). Fingerprint
  staleness cannot be evaluated without preparing the bootstrap, so status
  surfaces recorded statuses; the prefill run applies the full rules.
- A killed CLI leaves a dangling `PREFILLING` entry; re-running
  `openclaw prefill <agent>` is the recovery path (`beginPrefill` allows
  PREFILLING). Documented, not auto-healed.
- The prefill context carries an agent-scoped session key
  (`agent:<agent>:explicit:prefill-<uuid>`) and a synthetic
  `admittedRunContext` — both are run-correlation, not authorization evidence.

## Next Step

- Stage 6D acceptance is now runnable: restart the server for a genuinely cold
  start, run `openclaw prefill caveman kimi-linear-48b`, verify READY +
  matching PID, then a brand-new Caveman session must report `cacheRead > 0`
  with a small evaluated suffix. The in-flight run from this step already
  leaves the server warm for that test.
- Before 6D: decide whether to finish/commit the 6B WIP (recommended) and
  whether the live OpenClaw config should drop `kimi-local.timeoutSeconds`
  (now redundant for prefills since the seam strips it).

## Reproduction

Tests:

```bash
cd openclaw-src
node scripts/run-vitest.mjs run --config test/vitest/vitest.cli.config.ts \
  src/cli/prefill-cli.test.ts src/cli/prefill-cli.runtime.test.ts
node scripts/run-vitest.mjs run --config test/vitest/vitest.agents-core.config.ts \
  src/agents/simple-completion-runtime.test.ts
node scripts/run-vitest.mjs run src/agents/warm-state-registry.test.ts
```

Live prefill (detached; dev env; requires live server on 18080):

```bash
cd openclaw-src
cat > prefill-cli-live.tmp.ts <<'EOF'
import { Command } from "commander";
import { registerPrefillCli } from "./src/cli/prefill-cli.js";
const program = new Command();
registerPrefillCli(program);
await program.parseAsync(process.argv.slice(2), { from: "user" });
EOF
OPENCLAW_HOME=../dev-openclaw/home OPENCLAW_STATE_DIR=../dev-openclaw/state \
OPENCLAW_CONFIG_PATH=../dev-openclaw/config/openclaw.json \
nohup node --import tsx prefill-cli-live.tmp.ts prefill caveman kimi-linear-48b --json \
  > /tmp/kimi-prefill-cli.jsonl 2> /tmp/kimi-prefill-cli.err &
```

Status (safe, read-only):

```bash
OPENCLAW_HOME=../dev-openclaw/home OPENCLAW_STATE_DIR=../dev-openclaw/state \
OPENCLAW_CONFIG_PATH=../dev-openclaw/config/openclaw.json \
node --import tsx prefill-cli-live.tmp.ts prefill status --json
```

In-flight run evidence: `/tmp/kimi-prefill-cli.jsonl`,
`dev-openclaw/state/warm-state/registry.json`,
`/tmp/kimi-llama-server.log` (new task lines), `/tmp/kimi-prefill-cli.err`.
