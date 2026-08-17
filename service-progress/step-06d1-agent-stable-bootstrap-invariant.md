# Stage 6D.1 — Agent-Stable Bootstrap Invariant

## Status

**PASS (deterministic invariant)** — the canonical agent-stable bootstrap
prefix is now byte-identical across (A) the CLI prefill surface, (B) a fresh
Caveman session, and (C) a different fresh Caveman session, proven by a cheap
deterministic test that does not require a live Kimi run.

Stage 6D itself remains **FAIL** (unchanged, per operator direction). This
report records the Option B remediation as a separate stage. 6E has **not**
started.

## Objective

Make the Kimi stable bootstrap genuinely agent-stable so one explicit prefill
can be reused by arbitrary future sessions of that agent, per Option B of the
6D findings:

1. Define an explicit agent-stable prefix boundary.
2. Make CLI prefill and ordinary agent execution consume the SAME canonical
   agent-stable bootstrap builder.
3. Fix the effective-tool mismatch (41 tools for Caveman after owner-only
   `tools.deny`, not 52).
4. Move session-varying material out of the cached agent-stable prefix
   (Runtime block, output directives, silent-reply contract), preserving it
   after the boundary.
5. Make the warm-state fingerprint identify the canonical agent-stable
   prefix, not session-specific state.

The 6D acceptance criteria were NOT weakened or re-scoped. Before any live
Kimi run, a deterministic invariant test must prove

    prefix_N(cli_prefill) == prefix_N(session_A) == prefix_N(session_B)

## Architecture / Refactor

### 1. Canonical agent-stable context resolver (new)

`src/agents/embedded-agent-runner/run/agent-stable-bootstrap-context.ts`

`resolveAgentStableBootstrapContext({cfg, agentId, agentDir, workspaceDir,
provider, modelId, model, thinkLevel, admittedRunContext})` returns the
canonical `StableBootstrapAttemptContext`. It pins every session-shaped input
to its canonical fresh-session value:

| input | canonical value | why |
|---|---|---|
| `sessionKey` | `agent:<id>:stable-bootstrap` | plain agent key: primary, full prompt mode, `openclaw_main` surface, not subagent/ACP/cron |
| `sessionId` / `runId` | `stable-bootstrap` | deterministic, session-independent |
| `trigger` | `"user"` | fresh interactive session → full bootstrap routing (BOOTSTRAP.md + "Bootstrap Pending") |
| `senderIsOwner` | `false` | owner-only `tools.deny` applies → the canonical 41-tool surface |
| `sourceReplyDeliveryMode` | `"message_tool_only"` | the 6D acceptance dispatch surface |
| `messageChannel` | `"webchat"` | internal-message channel; the `message` tool's schema/description is channel-dependent ("Supports actions: send.") |
| `isCanonicalWorkspace` | `true` | canonical workspace bootstrap semantics |

The module also provides:

- `resolveCanonicalAcpSpawnAvailable({config, sandboxed})` — ACP spawn
  availability for the stable prefix is resolved from **config policy only**
  (`acp.enabled !== false`, minus sandbox). The 6D evidence showed the
  ordinary (gateway) process resolved ACP available while the CLI prefill
  process did not, because ACP backends are registered per-process by plugins
  (`registerAcpRuntimeBackend`) and health is process-local. ACP workflow
  hints and ACP-variant tool summaries render inside the stable prefix, so
  the value must be deterministic across processes. Actual ACP spawn
  enforcement is unchanged and remains at the tool call site.
- `resolveStableBootstrapFingerprintSystemPrompt(providerReadySystemPrompt)`
  — the fingerprint input: the system prompt up to the cache boundary.

### 2. One canonical builder for prefill and ordinary execution

- `src/cli/prefill-cli.runtime.ts` — `buildPrefillContext` now calls
  `resolveAgentStableBootstrapContext` (the old code built a synthetic
  `explicit:` session with random UUIDs and no trigger/sender policy). It
  also calls `pinConfigDir(process.env)` before config load, mirroring the
  gateway pre-bootstrap, so plugin-skills resolve from the same config root
  the gateway uses (6D finding: `~/.openclaw/plugin-skills` vs dev state's
  `plugin-skills`).
- `src/agents/embedded-agent-runner/run/attempt-stable-bootstrap-prefill.ts` —
  the shared prepare chain was extracted into
  `prepareEmbeddedAttemptStableBootstrapForContext({prepared, context})`
  (the same chain the ordinary runner consumes: setup → skills → tool base →
  bundle tools → tool catalog → bootstrap → system prompt → stable
  bootstrap). `prepareEmbeddedAttemptStableBootstrapForPrefill` canonicalizes
  the resolved context and delegates to it. The ordinary runner
  (`runEmbeddedAttempt` in `attempt.ts`) continues to use the same chain with
  its real session context.
- `src/agents/embedded-agent-runner/run/attempt-system-prompt-prepare.ts` —
  the shared `acpEnabled` flag now uses `resolveCanonicalAcpSpawnAvailable`
  (config-policy-only), so the CLI process and the gateway render identical
  ACP hints in the stable prefix.

### 3. Effective tool surface (41 tools for Caveman)

The canonical context sets `senderIsOwner: false`, so the owner-only deny
(`GATEWAY_OWNER_ONLY_CORE_TOOLS`, applied in `agent-tools.ts` when
`senderIsOwner === false`) removes exactly the 11 tools the 6D finding
documented: automations, conversations_list, conversations_send,
conversations_turn, gateway, mobile_ui, nodes, openclaw, portal, sessions,
terminal. Verified against the real dev config: the canonical prefill surface
now produces **41 tools** (probe log: "tool policy removed 11 tool(s) via
gateway sender owner-only tools.deny: …"), matching the ordinary Caveman
acceptance surface.

Known scope boundary: the `message` tool's schema/description is
channel-dependent by design (per-channel action metadata). The canonical
surface pins `messageChannel: "webchat"` (the acceptance channel). Sessions
on other channels resolve a different message-tool surface and will not
reuse this prefill (honest miss, cacheRead = 0). Making the message tool
channel-independent is a follow-up option if multi-channel reuse is needed.

### 4. Session-varying material moved below the boundary

`src/agents/system-prompt.ts`:

- `## Assistant Output Directives` moved from the stable prefix to the
  volatile suffix (its wording depends on `sourceReplyDeliveryMode`, a
  session/turn property).
- `## Silent Replies` moved from the stable prefix to the volatile suffix
  (its presence depends on `silentReplyPromptMode`).
- `sourceMessageToolOnly` / `silentReplyPromptMode` removed from the
  stable-prefix cache key.

Everything before the boundary is now a deterministic function of
agent-stable inputs. Everything session- or turn-specific (temporal context,
dynamic project context, approval/owner identity, channel guidance,
messaging, output directives, silent-reply contract, watched sessions,
heartbeat, `## Runtime` block incl. `Runtime: agent=… | session=<key> | …`,
reasoning line) lives after the boundary and is preserved — not deleted.

### 5. Fingerprint semantics

`src/agents/simple-completion-runtime.ts` — the warm-state bootstrap
fingerprint is now computed over the **canonical agent-stable prefix**
(`resolveStableBootstrapFingerprintSystemPrompt`: system prompt up to the
cache boundary) plus the effective tool catalog, instead of the full
provider-ready system prompt (which contained the session Runtime block).

- A different session key does **not** change the fingerprint.
- A change to a genuinely stable bootstrap input (system instructions,
  effective tool surface, skills/config, workspace bootstrap content) does
  change it → READY becomes STALE, per the existing registry rules.
- The registry key stays `(agent_id, model_id, bootstrap_fingerprint)`; only
  the fingerprint's input surface changed.

## Exact stable-prefix boundary N

N is the cache boundary inside the provider-ready system prompt
(`SYSTEM_PROMPT_CACHE_BOUNDARY`, `\n<!-- OPENCLAW_CACHE_BOUNDARY -->\n`,
stripped before the wire request). The prefill warms exactly the stable
prefix (plus its own volatile tail); every ordinary session shares that
prefix byte-for-byte and evaluates only its own volatile suffix + history +
user message.

Measured against the real dev config (`dev-openclaw/config/openclaw.json`,
agent caveman, model kimi-linear-48b):

- system prompt: 34,154 chars
- **stable prefix (N): 30,462 chars** (~25k tokens at ~1.2 chars/token,
  consistent with the 25,396-token prefill observed at 6D)
- volatile suffix: 3,656 chars
- effective tools: **41**
- canonical fingerprint: `78a37466565564c58cc6e4df318bde703bc5e3a4330ade9610a4d7b0f0ad4c9f`

## Invariant-test evidence

`src/agents/stable-bootstrap-invariant.test.ts` (deterministic; no live Kimi;
runs in ~30 s) constructs the three request surfaces through the REAL
bootstrap chain:

- A. CLI prefill surface — `prepareEmbeddedAttemptStableBootstrapForPrefill`
  with the canonical context (the exact CLI path).
- B. Fresh Caveman session A — the shared chain with a fresh session key
  (`agent:caveman:session-a-<uuid>`), trigger user, senderIsOwner false,
  message_tool_only, channel webchat (the acceptance dispatch shape).
- C. Fresh Caveman session B — same, different session key.

Assertions (all pass):

1. `prefix_N(cli_prefill) == prefix_N(session_A) == prefix_N(session_B)`
   byte-identical; N (boundary char count) identical.
2. Effective tool catalogs identical; owner-only tools absent (policy
   applied); sessions may differ AFTER N — their volatile suffixes differ
   and embed their own `session=` identity.
3. Fingerprint (stable prefix + tools) identical across A/B/C — changing
   only the session key does not change the fingerprint.
4. Changing a genuine bootstrap input changes the fingerprint: an owner
   surface (`senderIsOwner: true`) re-adds owner-only tools, changes the
   stable prefix and the fingerprint; a workspace whose bootstrap content
   differs (AGENTS.md present) changes the stable prefix and the fingerprint.
5. The test fails if session identity, tool-policy differences, tool
   metadata, Skills/Gateway sections, or other session-specific material
   leaks into the invariant prefix (this is the negative direction of
   assertions 1–4; the earlier 6D divergence would fail it: 52 vs 41 tools,
   ACP hints, BOOTSTRAP.md routing, output-directive variants).

## Regression / typecheck results

- `pnpm tsgo:core` — **PASS** (exit 0) at commit `96e31106a1b`.
- Deterministic invariant test — **3/3 PASS** (agents shard config).
- Seam/registry/CLI suites — **102 PASS** (`simple-completion-runtime`,
  `warm-state-registry`, `attempt-stable-bootstrap`, `system-prompt-stability`,
  `prompt-composition`, `prefill-cli`, `prefill-cli.runtime`).
- Tool-policy/prompt-construction/embedded-runner suites — **228 PASS**
  (`attempt-system-prompt`, `attempt-setup`, `attempt-tool-policy`,
  `attempt-prompt-tool-policy`, `tool-policy-pipeline`, `agent-tools.policy`,
  `conversation-tool-policy-pipeline`, `message-tool.internal-source-reply`,
  `prompt-surface`, `prompt-cache-observability`, `prompt-cache-retention`).
- Agents shard (targeted) — **29 PASS** (`simple-completion-runtime`,
  `attempt-stable-bootstrap-run`).
- `tsgo:core:test` — fails on **pre-existing** errors in
  `simple-completion-runtime.test.ts` (lines 1142–1204), verified present at
  the pre-6D.1 HEAD `a2b7c96075a` on a clean tree; unrelated to this stage.
  The new invariant test typechecks cleanly after the fix.

## Problems / notes

- The reconstruction artifact `prefill-surface-reconstruction.json` set env
  after imports, so its skills/section divergences were partly artifacts; the
  tool-catalog divergence (52 vs 41), ACP hints, bootstrap routing, and
  Runtime block were real. All are addressed above.
- `resolvePluginSkillDirs` still consults process-level ACP availability for
  ACPX skill exposure; no ACPX skills exist in the dev surface today, so it
  has no current impact. Noted as a residual env-dependent input.
- Owner sessions (senderIsOwner true → 52 tools) and non-webchat channels
  (message-tool variants) are out of scope for this shared prefill: they
  compute the same canonical fingerprint but their actual surface differs →
  honest miss (cacheRead = 0), never a false READY.

## Next Phase

The 6D rerun (cold restart → CLI prefill → brand-new Caveman session →
cacheRead > 0) is now structurally ready: prefill surface == ordinary
surface up to N, 41 tools on both sides, fingerprint session-independent.

Do NOT run the ~22-minute Kimi prefill yet — the operator explicitly
proceeds only after reviewing this report and confirming the rerun.

After a successful 6D rerun, Stage 6E (invalidation and restart behavior)
can begin. Not started here.

## Reproduction

```bash
cd openclaw-src
# Deterministic invariant test (no live Kimi):
node scripts/run-vitest.mjs run --config test/vitest/vitest.agents.config.ts \
  src/agents/stable-bootstrap-invariant.test.ts

# Regression suites:
node scripts/run-vitest.mjs run --config vitest.config.ts \
  src/agents/simple-completion-runtime.test.ts src/agents/warm-state-registry.test.ts \
  src/agents/embedded-agent-runner/run/attempt-stable-bootstrap.test.ts \
  src/agents/system-prompt-stability.test.ts src/agents/prompt-composition.test.ts \
  src/cli/prefill-cli.test.ts src/cli/prefill-cli.runtime.test.ts
node scripts/run-vitest.mjs run --config vitest.config.ts \
  src/agents/embedded-agent-runner/run/attempt-system-prompt.test.ts \
  src/agents/embedded-agent-runner/run/attempt-setup.test.ts \
  src/agents/tool-policy-pipeline.test.ts src/agents/agent-tools.policy.test.ts \
  src/agents/conversation-tool-policy-pipeline.test.ts src/agents/prompt-surface.test.ts

# Typecheck:
pnpm tsgo:core

# Dev-config surface probe (41 tools, boundary N, fingerprint):
OPENCLAW_CONFIG_PATH=../dev-openclaw/config/openclaw.json \
OPENCLAW_STATE_DIR=../dev-openclaw/state \
node --import tsx scripts/dev/reconstruct-prefill-surface.tmp.ts
```
