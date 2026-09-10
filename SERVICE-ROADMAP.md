# SERVICE-ROADMAP.md

This document is now a single-purpose plan for one problem:

> Prove whether `caveman`'s Kimi bootstrap state survives session boundaries, define explicit prefill and warm-state tracking for that bootstrap, isolate the separate post-prefill failure, and then expand the usable context window to a measured target.

Do not use this file to plan broader model rollouts. Do not touch other agents unless this plan succeeds and the user explicitly asks for expansion.

---

## Scope

- In scope:
  - `caveman` only
  - local Kimi service lifecycle
  - caveman bootstrap scope and durability
  - cross-session state reuse for caveman
  - explicit prefill / prewarm for caveman bootstraps
  - warm-state registry and invalidation for caveman/model/bootstrap combinations
  - post-prefill failure on caveman, but only after reuse is characterized
  - context-window expansion for caveman using the verified native/reference mechanism, but only after the narrow reuse/prefill path is understood
- Out of scope:
  - all other agents
  - Qwen service work
  - model math changes
  - unmeasured context-window expansion work
  - cache-policy redesign
  - timeout tuning
  - schema cleanup
  - tool-count changes
  - inference optimization phases in `ROADMAP.md`

---

## Current Diagnosis

We have already proven that:

- Kimi Linear 48B can run locally on this machine.
- The server itself stays healthy long enough to serve real requests.
- The cold Caveman path is expensive, but the same `llama-server` process can reuse the existing bootstrap state.
- The warm same-process retry is dramatically cheaper than the cold pass.
- Stage 5 showed that the same live `llama-server` process can support a warm same-PID Caveman reuse path, while restart-durable slot restore is unsupported in this configuration.
- The next unknown is the explicit prefill contract: can OpenClaw intentionally warm the exact Caveman bootstrap outside a conversational turn, track it by fingerprint and PID, and prove that later ordinary sessions reuse it?
- Stage 5C demonstrated runtime stability through generation and subsequent warm reuse on the same server. The known `failed to allocate loaded ids buffers` warning remains nonfatal unless later testing demonstrates otherwise.

The practical issue is now state scope, not raw model correctness:

    caveman bootstrap state
      + same-process reuse
      + explicit prefill/warm-state tracking
      + restart invalidation boundary
      = stable provisioning boundary to isolate

The post-prefill failure is a separate issue and must not be mixed into the reuse experiment.

Once the reuse/prefill boundary is known, the next narrow question is:

    how far can caveman's usable context window be expanded
      + with the verified native/reference mechanism
      + without breaking the agent path
      + within the available memory budget

---

## Success Criteria

`caveman` is considered fixed when all of the following are true:

1. A new Caveman session against the same live `llama-server` reuses only a small suffix of the bootstrap.
2. The explicit prefill contract exists, identifies warm state by `(agent_id, model_id, bootstrap_fingerprint)`, and a later ordinary Caveman session demonstrably reuses that prefetched state.
3. The warm-state registry correctly tracks COLD, PREFILLING, READY, STALE, and FAILED across bootstrap changes and `llama-server` PID changes.
4. The measured prompt-processing time for reused bootstrap stays dramatically below the cold baseline.
5. The reuse and prefill results are reproducible without shell state or one-off manual setup.
6. The completed Stage 5C runtime-stability characterization is documented separately, and the known nonfatal warning is not mistaken for an unresolved failure.
7. The usable context window has been expanded to a measured target, with `128k` treated as the first major milestone and `256k` only as an aspirational upper target if it proves practical. — **MET at 128K (2026-08-17); 256K not yet attempted.**

---

## Plan

After each step in the plan, put a markdown report in service-progress/

### 1. Freeze the target

Keep `caveman` pointed at Kimi Linear only.

- Primary model: `kimi-local/kimi-linear-48b`
- Fallbacks: DeepSeek and GPT-5.4 mini remain available only if Kimi fails
- No other agent config should change

### 2. Keep the Kimi service boring

The local Kimi server should behave like infrastructure, not a helper script.

Required properties:

- auto-start on login
- loopback-only by default
- no duplicate server instances
- explicit logs for stdout, stderr, exit, and lifecycle events
- manual start/stop remains available for debugging
- validated 4 GB expert cache stays the default

This is about reliability, not speed tricks.

### 3. Measure the real failure boundary

Before changing behavior, capture the caveman request shape and timing.

Measure:

- prompt token count
- prompt-eval duration
- time to first token
- total wall time
- whether the request aborts before first output
- the corresponding `llama-server` log tail
- the OpenClaw abort flags for the request

The goal here is to prove exactly where caveman dies, not to guess.

### 4. Test cross-session bootstrap reuse

The main hypothesis is simple:

> Caveman's stable bootstrap should remain mostly reusable when a brand-new Caveman session is created against the same live `llama-server` process.

Test exactly one thing:

- keep the current `llama-server` process alive
- create a new Caveman session
- send a small request
- measure evaluated prompt tokens, slot/LCP reuse, prompt-processing time, and first-token latency

PASS condition:

- the new session still evaluates only a small suffix of the bootstrap
- the server reuses the existing slot or prefix state
- the measurement is materially cheaper than the cold baseline

If this fails, stop here and document the scope boundary. Do not branch into schema, timeout, or tool-count work unless one directly blocks the measurement.

### 5. Stage 5: separate the durability and failure questions

Stage 5 is split into three explicit substeps so each hypothesis stays narrow.

#### 5A. Measure session-scoped reuse

The next hypothesis is:

> A brand-new Caveman session against the same live `llama-server` process should reuse the stable bootstrap state.

Test exactly one thing:

- keep the current `llama-server` process alive
- create a new Caveman session
- send a small request
- measure evaluated prompt tokens, slot/LCP reuse, prompt-processing time, and first-token latency

PASS condition:

- the new session still evaluates only a small suffix of the bootstrap
- the server reuses the existing slot or prefix state
- the measurement is materially cheaper than the cold baseline

If this fails, stop here and document the scope boundary. Do not branch into schema, timeout, or tool-count work unless one directly blocks the measurement.

#### 5B. Measure restart durability

The next hypothesis is separate:

> `llama-server` should be able to save and restore the Caveman bootstrap state across a restart.

Test exactly one thing:

- persist or snapshot the state if the server supports it
- restart `llama-server`
- create another small Caveman request
- measure whether the prompt stays warm or reverts to the cold baseline

PASS condition:

- the restored session still reuses a small suffix
- restart does not force a full cold prefill

If this fails, document the boundary and stop. Do not mix in the post-prefill crash yet.

#### 5C. Validate post-prefill runtime stability - PASS

The same-PID runtime-stability sequence completed successfully:

- cold bootstrap
- generation
- new-session warm reuse
- generation

The known `failed to allocate loaded ids buffers` warning remained visible in logs, but it did not produce an abort, restart, timeout, or fallback during the completed test.

### 6. Explicit Agent/Model Prefill

Stage 6 is the explicit prefill and warm-state tracking phase. It is split into
small substeps so the contract, registry, interface, and proof stay separate.

#### 6A. Define the prefill contract

Establish one canonical operation:

    prefill(agent_id, model_id)

It must:

- resolve the agent exactly as a normal OpenClaw turn would
- resolve the specified model
- compile the same stable bootstrap prefix that a normal first turn would receive
- compute `bootstrap_fingerprint`
- send that bootstrap through the normal provider/inference path, but without creating a conversational user turn
- leave the resulting prefix/KV state resident in the live inference server
- record the server PID associated with that warm state

Critically, prefill is not successful merely because inference completed. It is
successful only if a subsequent ordinary agent session demonstrably reuses the
resulting prefix.

Current implementation note:

- The stable-bootstrap prep chain now accepts a narrower resolved execution
  context instead of inventing a fake full embedded attempt object.
- `prefillWithStableBootstrapForAgent(...)` now requires that explicit resolved
  context, so the prefill seam no longer derives its own bootstrap context.
- The canonical seam therefore remains:

      ordinary agent resolution -> resolved execution context -> {normal turn | prefill}

- Live Caveman acceptance is still pending because the current workspace
  config/state rejects the source CLI before a full end-to-end proof can be
  recorded, and the direct prefill harness still trips auth-profile migration
  before llama-server evaluation.

Current harness note:

- Use the isolated `dev-openclaw` environment for Stage 6A.4 acceptance.
- Canonical paths are:
  - `OPENCLAW_HOME=/Users/pmains/Code/openclaw/kimi/dev-openclaw/home`
  - `OPENCLAW_STATE_DIR=/Users/pmains/Code/openclaw/kimi/dev-openclaw/state`
  - `OPENCLAW_CONFIG_PATH=/Users/pmains/Code/openclaw/kimi/dev-openclaw/config/openclaw.json`
- Do not use the older `/tmp/openclaw-stage6a4-*` harness for further
  acceptance probes.
- Keep the Caveman identity/model pairing fixed at the known-good
  `caveman` / `llama-cpp/kimi-linear-48b` configuration.
- For the isolated dev harness, disable the memory plugin slot with
  `plugins.slots.memory = "none"` so Caveman does not enter the optional
  OpenAI-backed memory-sync path during acceptance probes.
- The current isolated ordinary Caveman probe now reaches the `openai-completions`
  transport and gets `200 text/event-stream`, but the stalled turn never
  observes `data: [DONE]` or a provider `finish_reason` before settlement
  interruption.
- The exact Caveman JSON body is now captured in
  `dev-openclaw/state/stage6a4-request-body.json` (sha256
  `34839c6e4d22c445d313fbd1b7f62c643de995fd2aa5c5474ccdd30900be083f`), and
  replaying that identical body directly against the same llama-server PID
  `77135` does terminate normally when observed over a longer 300s window:
  the first SSE chunk arrives immediately, prompt processing continues for
  several minutes, prompt evaluation completes after roughly `211s`, and the
  response then emits generated content, `finish_reason:"length"`, usage, and
  `data: [DONE]`.
- The 90-second reduction pass was under-observing prompt evaluation rather
  than proving a hard stall:
  - `stream_options.include_usage` removed: one streamed chunk arrived within
    90s, but that short window was still not long enough to distinguish slow
    prompt evaluation from completion.
  - `tool_choice` removed, `tools` removed, and `max_completion_tokens=8`: no
    response headers within the observation window, again too short to classify
    the request definitively.
- That means the exact request shape is slow, not broken: the current remaining
  confounder is the amount of prompt evaluation needed before generation starts,
  not a permanent SSE termination bug on the direct HTTP path.
- The canonical isolated acceptance sequence was then rerun against a fresh
  `llama-server` PID with a warm prefill immediately before the ordinary
  Caveman turn. After removing `models.providers.llama-cpp.timeoutSeconds`
  from the isolated config, the local-provider watchdog exemption applied and
  the ordinary turn ran to natural stream completion, but the agent still
  surfaced an `incomplete_turn` with `replayInvalid: true`.
- The warm prefill still reported `cacheRead: 0`, and the final ordinary turn
  likewise reported `cacheRead: 0`.
- Prefix characterization then stopped at the first token boundary:
  - warm prefill token count: `7,757`
  - ordinary request token count: `27,809`
  - longest common prefix: `1`
  - exact-prefix check: `false`
  - first divergence index: `1`
- The ordinary request serializes tool declarations immediately after
  `<|im_system|>`, while the warm prefill starts with the plain system prompt.
  That prompt-shape mismatch is the current boundary; do not inspect
  llama-server cache matching or llama.cpp KDA/KV reuse until the tokenized
  prefix is exact.
- Stage 6A.4 therefore remains `PARTIAL`, and the current result is
  diagnostic-complete rather than acceptance-complete.

#### 6A.5 Derive the invariant ordinary-request prefix

The next boundary is the canonical tokenized prefix of an ordinary Caveman
request.

Establish it empirically, then make the prefill path reproduce that exact
ordinary serialization prefix:

- capture at least two ordinary Caveman requests with different user messages
  and unique session keys, using the same agent/model/tool/runtime template
- compare the final token IDs that llama-server would actually evaluate
- identify the longest semantically safe invariant prefix
- construct the prefill through the same ordinary request serialization path,
  not a synthetic system-only shape
- assert that `prefill_tokens == ordinary_tokens[:len(prefill_tokens)]`

Do not inspect cache matching or llama.cpp KDA/KV reuse until the exact-prefix
proof is in place.

#### 6A.6 Invariant-prefix cache acceptance

Run the canonical invariant-prefix prefill followed by a brand-new ordinary
Caveman session against the same fresh `llama-server` PID, then determine
whether the ordinary turn reports substantive prefix reuse.

OpenClaw integration issue:

- `sessions_send(sessionKey=..., timeoutSeconds: 0)` is not the right harness
  surface for this acceptance step because it blocks in the admission path
  before returning a `runId`.
- The acceptance harness should submit the turn through the supported
  lower-level `agent` dispatch with `expectFinal: false`, capture the returned
  `runId`, and keep the existing durable completion monitor.
- This is a harness / OpenClaw routing issue, not a model, cache, or
  `llama-server` behavior change.

Current environment note:

- The Stage 6A.6 harness now forces the isolated dev config via
  `OPENCLAW_CONFIG_PATH=/Users/pmains/Code/openclaw/kimi/dev-openclaw/config/openclaw.json`
  and `OPENCLAW_STATE_DIR=/Users/pmains/Code/openclaw/kimi/dev-openclaw/state`.
- Borrowing only the gateway auth secret from `~/.openclaw/openclaw.json`
  remains acceptable for the isolated dev harness.
- Manual trajectory inspection has now proven durable live-run observability:
  the active session `.trajectory.jsonl` shows `session.started` and
  `prompt.submitted` for the dispatched run before `model.completed`.
- The automated harness monitor still returns `pending` for that same live run,
  so the live-marker polling path remains unresolved and should not be treated
  as evidence against cache reuse.
- The same-server canonical replay artifact already shows substantive prompt
  cache reuse on the ordinary Caveman prompt: `cached_tokens=24576`,
  `prompt_n=3149`, `prompt_ms=211371.516`, versus the uncached 27,809-token
  prefill at `prompt eval time = 1882854.65 ms`.
- The comparison trail for that signal is anchored by
  `dev-openclaw/state/stage6a4-reduction/baseline_300s.body.txt`,
  `dev-openclaw/state/stage6a4-reduction/results.tsv`, and
  `dev-openclaw/state/stage6a4-token-prefix-compare.json`.

Acceptance criteria:

- `cacheRead > 0` on the ordinary turn
- llama-server telemetry corroborates prefix reuse
- the ordinary prompt-evaluation time is materially lower than the uncached
  ~27k-token baseline

If the run cannot produce terminal evidence, stop at that boundary and record
the request as an incomplete cache-acceptance observation. Do not patch
`llama.cpp` yet. The next boundary after a failed acceptance run is the
llama-server / llama.cpp prefix-state reuse characterization, including any
hybrid-attention/KDA constraints.

Current state note:

- The cache-reuse signal itself is already established in the replay
  artifacts.
- The automated live-marker monitor gap is now closed: the Stage 6A.6
  harness monitor reads the authoritative SQLite `trajectory_runtime_events`
  table in the per-agent state database
  (`<OPENCLAW_STATE_DIR>/agents/<agentId>/agent/openclaw-agent.sqlite`) via
  `node:sqlite`, matching rows on the accepted `run_id`
  (`session.started`/`prompt.submitted` = live marker;
  `trace.artifacts`/`session.ended` = terminal). The `.trajectory.jsonl` scan
  remains only as a fallback.
- Dev-gateway gate: `dev-openclaw/config/openclaw.json` runs with
  `diagnostics.enabled=false` (supported `DiagnosticsConfig.enabled` gate for
  stuck-session recovery); provider `timeoutSeconds` was already removed.
  The dev gateway must be started with the same `OPENCLAW_GATEWAY_TOKEN` the
  harness borrows, otherwise the gateway's fresh per-startup runtime token
  rejects the harness with `AUTH_TOKEN_MISMATCH`.
- Stage 6A.6 preflight (2026-08-16) PASSED the integration check: dev gateway
  -> accepted runId (`eb4e791b-8762-4014-88ec-94b74471b138`) -> llama-server
  received the request (POST /v1/chat/completions, task 0 prompt processing
  + generation) -> SQLite observed live runtime events for that runId while
  the run was in flight. The smoke run was terminated after those four
  conditions were observed (below the former ~6-minute watchdog threshold).
  See `service-progress/step-06a6-sqlite-live-monitor-preflight.md` and
  `dev-openclaw/state/stage6a6-preflight/2026-08-16T19-33-25-138Z/preflight-evidence.json`.
- The Stage 6A.6 acceptance run itself was executed on 2026-08-16 (12:51–13:11
  MST, fresh llama-server PID 58186) and FAILED / is incomplete:
  - Prefill run `eac84423-...` auto-compacted (compactionCount 1); its second
    model call cold-evaluated a 22,241-token prompt (1088.14 s, no
    cached_tokens), then llama-server crashed with
    `ggml_new_object: not enough space in the context's memory pool (needed
    1049168, available 1048944)` -> `GGML_ASSERT(obj_new)` -> SIGABRT
    (compute pool short by 224 bytes for the post-eval graph).
  - Ordinary followup `856bb37c-...` errored in 3 s because no server was
    alive (gateway localService auto-spawn of the frozen bundle fails on
    `@rpath/libllama-server-impl.dylib`); it never reached a model call, so
    `cacheRead` is unmeasurable. Acceptance criteria not met.
  - The SQLite monitor itself is verified: the followup's live marker was
    found in `trajectory_runtime_events` (seq 70, waitedMs 3204). Note:
    SQLite rows flush at run finalization with `created_at` = event
    timestamp, so they become visible at finalize (the preflight's "while
    live" wording is corrected accordingly).
  - Records: `service-progress/step-06a6-acceptance-run-2026-08-16.md`,
    `dev-openclaw/state/stage6a6-acceptance/2026-08-16T19-48-29-562Z/`.
  - Next boundary: the expert-streamer/ggml compute-buffer accounting crash
    at large prompt sizes (llama.cpp NOT patched; roadmap STOP point).
- The Stage 6A.6 acceptance rerun was executed on 2026-08-16 (fixed binary
  `0a6b2df63`, fresh PID 63686) and PASSED:
  - Blocker fixed in llama.cpp `0a6b2df63`: the streamer's grow-only repack
    temp context accumulated ~15 tensor objects/step (down expert alternates
    Q6_K/Q4_K 14x per layer pass) and overflowed any fixed pool size; the
    crash was the 2851st object, 224 B short. Fix: recycle the pool on
    overflow (free per-kind buffers + `ggml_reset`). No caching, KDA/KV,
    prefix, dispatch, or acceptance-semantics changes. Characterization,
    unit test, and regression evidence:
    `progress/stage-06a6-temp-ctx-pool-fix-report.md`.
  - Prefill turn `cfe69649-...` cold-evaluated a 22,409-token prompt
    (1,348,704.81 ms / 16.62 t/s) and completed with zero aborts — the exact
    scenario that SIGABRT'd the previous run.
  - Ordinary followup `48aae132-...` reported `cacheRead=22410` (input 86,
    output 2): server-side eval 86 tokens / 14.46 s vs the prefill's 22,409
    tokens / 1,348.7 s (~93x lower; ~130x lower than the ~27k uncached
    baseline). All three acceptance criteria met.
  - Records: `dev-openclaw/state/stage6a6-acceptance/2026-08-16T21-41-41-660Z/`,
    `progress/stage-06a6-report.md` (PASS).
  - Fix promoted to live: `runtime/live/COMMIT` = `0a6b2df63`; live server
    restarted and verified via `openclaw infer model run --model
    kimi-local/kimi-linear-48b`. This also closes the live server's own
    recurring SIGABRTs from the same defect (BUG-001).

#### 6B. Implement the warm-state registry

#### Status (2026-08-16)

**PASS** — see `service-progress/step-06b-warm-state-registry.md`. Registry
implemented in `openclaw-src` (`c9c6ff96d93`):
`src/agents/warm-state-registry.ts` (fingerprint computation, live-PID
resolution, full state machine with READY→STALE on fingerprint change and
READY→COLD on PID change, versioned JSON persistence) wired into
`prefillWithStableBootstrapForAgent` (opt-in via `OPENCLAW_WARM_STATE_REGISTRY=1`
or explicit `warmStateDir`; inert otherwise). 17/17 new unit tests pass;
54/54 existing prefill-seam tests pass; `tsc` clean.

Caveat: the 6B follow-up seam WIP (`attempt-*.ts` family,
`stable-bootstrap-context.ts`) is now committed (`a2b7c96075a`, 2026-08-16),
so the repo is self-consistent: `cf4f6bde255` imports
`attempt-stable-bootstrap-prefill`/`stable-bootstrap-context`, which were
previously untracked. `tsgo:core` clean; 74/74 seam-affected unit tests pass.
The only remaining uncommitted dev-tree changes are the 6A.4 boundary-trace
instrumentation (14 files, diagnostic only) — not part of the seam.

OpenClaw needs to know what it believes is warm:

    (agent_id, model_id, bootstrap_fingerprint)
      -> status
      -> server_pid
      -> cached_tokens
      -> warmed_at

Define the state machine precisely:

    COLD
     ↓
    PREFILLING
     ↓
    READY

    READY -> STALE when the bootstrap fingerprint changes
    READY -> COLD when the inference-server PID changes
    PREFILLING -> FAILED
    FAILED -> PREFILLING on explicit retry

That PID rule is important: Stage 5B established that restart persistence is
unsupported in this configuration, so a server restart must invalidate the prior
READY state.

Current implementation note (2026-08-16, updated after 6C):

- The operative registry for the prefill path is the TypeScript
  implementation in `openclaw-src`: `src/agents/warm-state-registry.ts`
  (commits `c9c6ff96d93` + `cf4f6bde255`, which adds
  `probeWarmStateRegistry`), persisted as versioned JSON at
  `<stateDir>/warm-state/registry.json` (atomic tmp+rename writes;
  corrupted file -> empty registry).
- Registry key: `(agent_id, model_id, bootstrap_fingerprint)`; entry fields:
  `status`, `server_pid`, `cached_tokens`, `warmed_at`, `updated_at`.
- The bootstrap fingerprint is sha256 over the stable serialization (sorted
  keys, undefined dropped) of the provider-ready system prompt plus the
  effective tool catalog — not a captured request body. Any bootstrap change
  flips the fingerprint and marks the old READY entry STALE.
- `resolveWarmState` applies lazy invalidation on read, persisted:
  READY -> COLD when the recorded `server_pid` differs from the live
  llama-server PID (pidfile, default `/tmp/kimi-llama-server.pid`) or no
  server is running; READY -> STALE when the fingerprint differs;
  PREFILLING -> FAILED if the prefill target server died; FAILED -> PREFILLING
  on explicit retry.
- Recording is opt-in: `OPENCLAW_WARM_STATE_REGISTRY=1` or an explicit
  `warmStateDir`; the Stage 6C CLI always enables it. Bookkeeping errors are
  caught and surfaced, never allowed to fail the prefill.
- Separate management surface: `tools/warm_state.py` operates a different
  store (`runtime/state/warm-state.json`; fingerprint over the captured
  stage6a4 request body; bounded `history` field; `--registry` for alternate
  stores). The CLI and seam share the TS store; converging the two surfaces
  is an open follow-up (`service-progress/step-06c-cli-observability.md`,
  Problem 5).
- Verified on 2026-08-16: 17/17 unit tests exercise the full state machine
  (including READY->COLD on PID change and READY->STALE on fingerprint
  change) against the dev store and live PID 64991; live `prefill status`
  confirms READY->COLD invalidation against the real server.
- Status/observability surface is Stage 6C (`openclaw prefill status`), which
  applies the READY->COLD PID rule across all entries.
- Acceptance of the full prefill loop remains Stage 6D.

#### 6C. CLI and observability

#### Status (2026-08-16)

**PASS (surface) — e2e prefill run in flight.** See
`service-progress/step-06c-cli-observability.md`. Implemented in `openclaw-src`
(committed `cf4f6bde255`): `src/cli/prefill-cli.ts` + `prefill-cli.runtime.ts`
(command registration + domain logic), `probeWarmStateRegistry` in
`src/agents/warm-state-registry.ts`, and seam fixes required to make the
prefill path actually executable (explicit-agent workspace binding for model
discovery, request-timeout strip for prefill completions, `admittedRunContext`
threading through the prefill context). 18 new CLI tests + 28 seam tests +
17 registry tests pass. Live `prefill status` verified against the running
llama-server; the first real `prefill caveman kimi-linear-48b` was launched
detached and reached the server (registry PREFILLING, server task 13,
`timeoutMs=undefined`). Still in flight at last check (19:35 MST): task 13 at
6,144/25,600 tokens (progress 0.24, ~21.8 t/s), expected READY ~19:50.
The 6B follow-up seam WIP is committed (`a2b7c96075a`) and type-clean
(`tsgo:core` passes; 74/74 seam-affected tests pass) — 6D acceptance can now
run against the committed revision.

Implement:

    openclaw prefill caveman kimi-linear-48b
    openclaw prefill caveman
    openclaw prefill status
    openclaw prefill status caveman
    openclaw prefill caveman kimi-linear-48b --json

DONE: the command itself is not subject to the normal agent stuck-session
watchdog (in-process simple-completion seam; provider request timeouts are
stripped for prefill completions). DONE: progress comes from llama-server's
own logged prompt-progress fraction (`slot print_timing ... n_tokens = N,
progress = P`), rendered as `PREFILLING 7,782 / 10,240 tokens (76%) ·
15.8 tok/s · ETA 2m35s · PID 11752` on TTY stderr (no fabricated
percentages; JSON mode never mixes progress into stdout).

#### 6D. Prove that `/prefill` actually works

#### Status (2026-08-17, final: PASS)

**PASS (6D rerun #2, 2026-08-17)** — the full acceptance now passes end to end:
cold llama-server restart → CLI prefill → READY on the live PID → brand-new
Caveman session through the real ordinary-agent path → `cacheRead = 20,473`
(96.5% of the 21,209-token prompt), evaluated suffix **735 tokens**, ordinary
turn **46 s** vs ~22 min cold. All 10 criteria green. Evidence:
`dev-openclaw/state/stage6d-acceptance/2026-08-17T16-08-07-815Z/`.

History (preserved in `service-progress/step-06d-prefill-acceptance.md`):

1. **6D (initial) — FAIL** — prefill half PASSED (cold restart 64991 → 80177;
READY in 1,352 s; fingerprint `0976a1a4`); reuse half FAILED (full eval,
cacheRead 0). Root cause: surface divergence (52 vs 41 tools; 147 diff
regions; session/runtime metadata block in the prefix). Registry behaved
honestly (fingerprints differ → miss).
2. **6D.1 — PASS (deterministic invariant)** — Option B remediation:
canonical agent-stable bootstrap builder shared by prefill and ordinary
sessions; session-varying material below the cache boundary; invariant test
`prefix_N(cli_prefill) == prefix_N(session_A) == prefix_N(session_B)`
(hermetic). Commit `96e31106a1b`.
3. **6D rerun #1 (2026-08-17, `96e31106a1b`) — FAIL** — new mechanism:
process-local ACP/ambient state leaked into the stable surface
(`acp-router` skill gated on the per-process ACP backend registry;
`sessions_spawn` tool metadata from the same process-local check; ambient
`slack` channel auto-enabled in the CLI but suppressed in the gateway).
Server reused nothing: f_sim 0.414, full 21,205-token eval, cacheRead 0.
4. **6D.2 — PASS (remediation)** — canonicalized the three leaks to
config policy (acpx skills + sessions_spawn advertisement via
`resolveCanonicalAcpSpawnAvailable`; prefill config load with
`ambientEnvTriggers: "suppress"` matching the gateway default). Full detail:
`service-progress/step-06d2-acp-determinism-fix.md`.
5. **6D rerun #2 — PASS** — full acceptance green (above).

Also fixed in the harness: dev-gateway dispatch (in-process URL 18790 after
config load), registry selection by expected (agent, model, fingerprint)
rather than position, non-fatal `session_status` pre-call (a brand-new
session key is expected unknown; dispatch creates it), and a cheap dev-gateway
health preflight before the ~20-minute prefill.

#### 6D.1. Agent-stable bootstrap invariant (Option B remediation)

#### Status (2026-08-17)

**PASS (deterministic invariant)** — see below (unchanged from the original
record). The 6D rerun (#1) revealed that the hermetic invariant did not cover
real-config process-local inputs (ACP backend registration, ambient channel
policy); those were canonicalized in 6D.2 and the rerun then passed.

Acceptance experiment:

1. Restart the server so the starting state is genuinely cold.
2. Run `openclaw prefill caveman kimi-linear-48b`.
3. Verify the result becomes `READY`.
4. Record the server PID.
5. Create a brand-new Caveman session.
6. Send one tiny ordinary request.

PASS requires all of these:

- prefill completes without creating a normal conversational turn
- status becomes `READY`
- the recorded server PID matches the live inference-server PID
- the new Caveman session selects the warm prefix by LCP/cache reuse
- the ordinary request evaluates only a small suffix
- `cacheRead > 0`
- latency is materially below the cold baseline
- no fallback
- no abort
- no restart

That final verification matters. OpenClaw must not report `READY` while having
warmed something other than the exact bootstrap the agent will later use.

#### 6E. Invalidation and restart behavior

Test the two invalidation paths that matter:

- Bootstrap mutation: change something fingerprinted and confirm the old state becomes `STALE`.
- Server restart: PID changes and the old state becomes `COLD`, regardless of whether any slot file still exists.

Then a manual prefill must return the state to `READY`.

Concrete live protocol (operator decision 2026-08-16; full detail in
`service-progress/step-06e-prefill-invalidation.md`):

    READY(PID A, fingerprint A)
    → mutate a fingerprinted bootstrap input
    → `prefill status` reconciles to STALE
    → restore intended bootstrap B + prefill
    → READY(fingerprint B, PID A)
    → restart llama-server
    → `prefill status` reconciles to COLD (new PID)
    → re-prefill
    → READY(new PID)

Each transition must be observed live via `prefill status`/the registry (the
real system's events, not unit tests). Restore the original bootstrap
configuration at the end so the acceptance experiment does not leave Caveman
modified. The stashed 6A.4 boundary-trace instrumentation stays stashed
through 6D/6E as a diagnostic fallback; after Stage 6, review it — extract
generally useful pieces into a clean commit or discard.

Stage 6 is complete when OpenClaw can explicitly prefill the exact bootstrap
used by an agent/model pair outside a normal agent turn, accurately track that
warm state against its bootstrap fingerprint and inference-server lifetime,
expose observable CLI progress and status, and prove that a subsequent new
agent session actually reuses the prefilled state.

#### 6E status (2026-08-17)

**PASS** — full live sequence observed end to end via `prefill status` / the
registry (see `service-progress/step-06e-prefill-invalidation.md`). Operator
refinement applied: because the original bootstrap is restored, the recovered
fingerprint returns to fp A (identity, not chronology).

    READY(fp A, PID A=32953)
    → mutate acp.enabled=false → `prefill status` → STALE (same PID)
    → restore bootstrap byte-identical → prefill → READY(fp A, PID 32953)
    → controlled restart → PID B=35859
    → `prefill status` → COLD (new PID)
    → cold prefill (20,989 tokens, 974.9 s) → READY(fp A, PID 35859)
    → final config byte-identical (sha256 7fe3eb62…)

Three properties proven independently: bootstrap invalidation
(`READY → STALE` on a stable-input change), determinism/recovery (restored
bootstrap reproduced the same fp A), and process invalidation (`COLD` on PID
change, recovery as `READY(fp A, PID B)`). Three orchestration/harness
failures occurred during the run; all were driver defects in
`tools/stage6e_acceptance.sh`, none implementation failures (documented in the
6E report). Stage 6 as a whole: **PASS**.

### 7. Verify the native position mechanism

Before running any ladder, establish which positional behavior Kimi Linear actually uses in the reference implementation and in the current `llama.cpp` path.

The question is not "which knobs exist in the UI."
The question is "which knobs actually participate in Kimi Linear inference."

Concrete gate:

1. Inspect the reference Kimi Linear model/config path and the corresponding `llama.cpp` runtime path.
2. Determine the native/reference positional behavior.
3. Record which context-extension knobs actually affect this architecture.
4. Classify each candidate mechanism as one of:
   - `SUPPORTED`
   - `UNAVAILABLE`
   - `NOT APPLICABLE`
5. If the evidence says RoPE or YaRN are not part of the inference path, record them as `UNAVAILABLE` or `NOT APPLICABLE` and do not run peer experiments on them.
6. Treat the native/reference mechanism as the first and primary mechanism under test.

Required output:

- exact source of the reference behavior
- exact `llama.cpp`/`llama-server` path that implements it
- exact runtime knobs that are actually meaningful
- explicit note if `NoPE` is the reference/native behavior
- explicit note if `RoPE` or `YaRN` are unsupported or not relevant

Stop this gate before the ladder if the mechanism is not clearly identified. Do not guess.

#### 7 status (2026-08-17)

**COMPLETE — native mechanism verified: NoPE (no positional encoding).** Full
evidence and classification in `service-progress/step-07-position-mechanism.md`.

- Reference (`moonshotai/Kimi-Linear-48B-A3B-Instruct` `config.json` +
  `modeling_kimi.py`): `rope_theta=10000.0`, `rope_scaling=None`,
  `qk_rope_head_dim=64` / `qk_nope_head_dim=128`; the MLA forward splits
  `q_rot`/`k_rot` and re-concatenates them **without any rotary application**
  (no `apply_rotary_pos_emb`, no cos/sin tables, no rotary module anywhere in
  the file). KDA layers use causal conv1d + recurrent delta-net scan — position
  is implicit in causality/convolution/recurrence.
- llama.cpp (`0a6b2df63`): `llama_model_rope_type()` returns
  `LLAMA_ROPE_TYPE_NONE` for `LLM_ARCH_KIMI_LINEAR`; `src/models/kimi-linear.cpp`
  builds MLA Q/K by concatenating nope + "pe" slices with no `ggml_rope` call
  (code comments: "Kimi MLA does NOT apply RoPE"; "k_pe is used directly
  without RoPE"); KDA uses `causal_conv1d` + `ggml_kda_scan`. No rope freqs
  are built; KV-cache K-shift is gated off (`rope_type != NONE`).
- Classification: RoPE, RoPE linear scaling, YaRN — **NOT APPLICABLE**
  (no rope op exists to consume them; `rope_scaling=None` in reference).
  NoPE — **SUPPORTED (native)**. MLA/KDA position handling — **SUPPORTED**
  (matches reference). `--ctx-size` — participates (KV + recurrent state
  allocation). `--rope-scaling`/`--yarn-*`/`--rope-freq-base`/`--rope-freq-scale`
  — **NOT APPLICABLE** for this arch.
- Implication: the model's native context is 1,048,576 tokens
  (`model_max_length`; GGUF `context_length=1048576`). Any future
  context-window ladder is a **memory-budget** question, not a
  positional-extrapolation question. No ladder was defined or begun; that
  decision is deferred per the Stage 7 directive.

### 8. Verify usable context scaling under the native NoPE mechanism

(Renamed from "Expand the usable context window" per operator definition 2026-08-17.)

Stage 7 established the architectural reason, not just a knob result: Kimi Linear
is natively **NoPE**, and both execution paths (reference and llama.cpp) derive
ordering from causality (causal mask in the 7 MLA layers; causal conv1d +
recurrent delta-net scan in the 20 KDA layers) rather than explicit positional
transforms. That removes an entire class of context-extension variables. Stage 8
is therefore a **capacity, correctness, and memory-behavior validation problem**,
not a positional-extrapolation problem.

Acceptance question:

> How far can the current Kimi Linear implementation scale toward its declared
> 1,048,576-token native context while preserving correctness, stable memory
> behavior, cache semantics, and acceptable service operation?

Ladder: increasing contexts with **no positional knob changes** (no RoPE, no
YaRN, no frequency overrides — none are applicable per Stage 7).

    current validated context (32K) → 64K → 128K → 256K → 512K → 1,048,576

Each rung is a **diagnostic checkpoint, not a mandatory target**. If resource
growth reveals a hard architectural or implementation limit, stop and
characterize it before proceeding.

Four independent gates per rung:

1. **Allocation / startup** — the requested context provisions successfully.
2. **Prompt ingestion / prefill correctness** — long prompts prefill correctly.
3. **Post-prefill generation correctness** — generation after long-context
   prefill is correct.
4. **State/cache behavior after long-context use** — KV cache, KDA recurrent
   state, expert cache, and slot behavior remain stable.

Memory must be recorded **separately** at each rung where observable:

- KV cache (MLA layers)
- recurrent/KDA state (KDA layers)
- model / expert cache
- total process RSS

Three failure classes — never collapsed into a single FAIL:

- **Allocation failure** — the memory model or implementation cannot provision
  the requested context.
- **Correctness failure** — it provisions, but long-context semantics break.
- **Performance failure** — correctness holds, but latency or memory pressure
  makes that rung operationally unusable.

Scaling law: measure the actual memory curve rather than assuming it. With only
7 MLA layers carrying conventional attention-state costs and 20 KDA layers using
recurrent state, the observed slope may differ substantially from what a standard
transformer context calculator predicts. Stage 8 establishes that curve.

Protocol (per rung):

1. Capture a baseline run at the current known-good configuration first.
2. Use the native NoPE configuration and one exact runtime knob set (`--ctx-size`
   only; no positional knobs).
3. Run the fixed rung ladder above with the same caveman request shape at every
   rung; keep the rest of the agent configuration unchanged.
4. Apply the four gates and classify any failure per the three classes above.
5. Record memory separately (KV / KDA state / expert cache / total RSS).
6. Stop at the first rung that fails, times out, or pushes memory into an
   unusable state; characterize it before proceeding.
7. Report each rung in `service-progress/` and save machine-readable outputs
   under `benchmarks/results/`.

Treat two ceilings separately:

- `runtime ceiling`: crashes, allocation failure, or unacceptable memory pressure
- `useful-context ceiling`: caveman runs, but can no longer reliably retrieve or
  use the expanded context

Measure at each rung:

- prompt token count at the boundary
- prompt-eval duration
- first-token latency
- resident memory (split per the four buckets above)
- runtime failure mode, if any
- useful-context result from a small needle/retrieval test
- whether caveman still completes a normal request at that size

PASS condition for Stage 8:

- caveman sustains a measured context target materially larger than the current
  baseline, with the native NoPE mechanism and no positional knobs
- the agent path still works at that size
- the strategy is documented with exact runtime knobs and measured limits
- the useful-context test passes at the milestone rung
- the empirical memory-vs-context curve is recorded

If 1,048,576 is not practical on this machine, record the highest sustainable
value and stop there. Do not guess a higher number without evidence. The current
validated context (32K) is the floor; 128K remains a milestone marker, with
higher rungs aspirational pending measured evidence.

### 8A. Baseline

Before the ladder:

- run the current caveman request at the existing context setting (32K)
- capture prompt tokens, prefill time, first-token latency, RSS split, and log
  tail
- save this as the comparison baseline for the ladder

### 8B. Evidence to save

For each ladder run, write a short report in `service-progress/` and save the
machine-readable outputs under `benchmarks/results/` (sibling directory named
for the mechanism, e.g. `benchmarks/results/no-pe-ladder/`).

Each report should include:

- mechanism used (native NoPE; no positional knobs)
- exact runtime knobs (`--ctx-size` value and full server invocation)
- largest successful context size
- first failing context size
- failure class at the failing rung (allocation / correctness / performance)
- runtime ceiling, if reached
- useful-context ceiling, if reached
- memory split at the top successful rung (KV / KDA state / expert cache / RSS)
- whether caveman remained usable
- the command used to reproduce the run

#### 8 status (2026-08-17)

**MILESTONE REACHED — 32K/64K/128K all PASS (native NoPE, `--ctx-size` only).**
The ladder was run with the frozen live runtime (COMMIT `0a6b2df63`, port
18081, identical probes/prompt construction/memory sampling across rungs;
only `--ctx-size` changes). Boundary prompts carry a needle at ~70% depth
sized to ~90% of ctx; all four gates pass at every completed rung and the
needle is retrieved exactly each time; zero allocator anomalies.

| rung | actual prompt tokens | prefill (tok/s) | KV (7 MLA) | KDA recurrent | peak RSS during prefill |
|---|---|---|---|---|---|
| 32K (authoritative) | 23,149 | 782,312 ms (29.59) | 252.00 MiB | 42.81 MiB | 6.24 GB |
| 64K | 46,214 | 1,741,159 ms (26.54) | 504.00 MiB | 42.81 MiB | 6.14 GB |
| 128K (milestone) | 92,344 | 4,452,334 ms (20.74) | 1008.00 MiB | 42.81 MiB | 7.83 GB |

Empirical curve: KV scales linearly (exactly 2× per doubling) from the 7 MLA
layers only; KDA recurrent state is flat at 42.81 MiB (context-independent);
expert cache constant 4096 MiB; steady RSS 4.3–4.4 GB (32K/64K) → 5.46 GB
(128K); prefill throughput declines gently 29.59 → 20.74 tok/s. Peak RSS
stays ~7.8 GB at 128K — comfortably inside the 24 GB machine. No allocation,
correctness, or performance failure through 128K.

Ladder paused at 128K per the protocol stop-and-characterize rule and the
AGENTS.md STOP point; 256K → 512K → 1,048,576 remain aspirational and launch
only on explicit instruction. Full detail and per-rung evidence:
`service-progress/step-08-context-capacity-ladder.md`; machine-readable
results committed under `benchmarks/results/no-pe-ladder/`.

## 9. Qualify Response Quality

### Goal

Determine whether the current Kimi Linear runtime produces responses of sufficient quality for productive use as a real OpenClaw agent.

The runtime is already quality-qualified at the model level through perplexity testing. This step asks a different question:

> Does Kimi behave correctly and reliably when operating through the actual OpenClaw agent path?

Do not optimize inference performance, change the model, modify prompts, redesign the tool surface, or tune context behavior during this step.

First measure the current behavior.

### Baseline

Freeze and record the exact production configuration before testing, including:

* model and GGUF;
* llama.cpp/runtime commit;
* OpenClaw commit;
* expert-cache size;
* expert-read worker count;
* Metal/CPU configuration;
* context size;
* agent bootstrap fingerprint;
* effective tool catalog;
* relevant runtime environment variables.

The currently expected runtime is the promoted MXFP4 Metal configuration with:

* corrected streamed Metal execution;
* direct expert placement;
* 4 expert-read workers;
* 8 GiB expert cache;
* approximately 9–11 decode tok/s;
* 128K previously validated as a technical context milestone.

The captured configuration is authoritative.

### Work

Build a small, fixed agent-quality suite representing the work this agent is actually expected to perform.

At minimum include:

1. **Literal instruction following**

   Example:

   ```
   Respond only PLATANOS!
   ```

   The expected result is exact and unambiguous.

2. **Constrained output**

   Require a short response in an exact requested format.

3. **Simple reasoning**

   Use tasks with independently verifiable answers.

4. **Repository comprehension**

   Ask a narrow factual question answerable from explicitly identified project files.

5. **Bounded tool use**

   Require one appropriate tool operation and a concise answer based on the result.

6. **Multi-step engineering work**

   Give the agent a small realistic task requiring several operations while explicitly limiting its scope.

7. **Final-answer behavior**

   Verify that completed tool work reliably produces a final user-facing answer rather than ending in status/progress activity.

Retain the exact prompts and expected behavior so the same suite can be reused against future runtime configurations.

### Evaluation

For each task record:

* instruction followed: PASS / FAIL;
* requested format followed: PASS / FAIL / N/A;
* factual or task result correct: PASS / FAIL / UNCLEAR;
* unnecessary clarification: YES / NO;
* unnecessary tool use: YES / NO;
* unnecessary repository/session/history archaeology: YES / NO;
* task completed: PASS / FAIL;
* final answer emitted: YES / NO;
* fallback or model substitution: YES / NO;
* actual final response.

Preserve failed outputs verbatim.

Perplexity does not override behavioral evidence.

For example, a response that asks what `PLATANOS!` means after being instructed to `Respond only PLATANOS!` is a response-quality failure even if the underlying model has acceptable perplexity.

### Diagnostic Control

When a failure is important or systematic, use the minimum control necessary to determine whether the failure originates in:

* the underlying Kimi model/runtime;
* the OpenClaw bootstrap/system prompt;
* tool definitions or agent configuration;
* accumulated session context;
* another measured component.

A direct llama-server request may be used as a control.

Do not treat the direct request as the primary qualification surface.

Do not fix the problem during Step 9.

### Acceptance

Step 9 passes when the evidence is sufficient to answer:

> Is the current Kimi agent behavior reliable enough for the kinds of OpenClaw work we intend to give it?

Classify the result as:

* `PASS`
* `CONDITIONAL PASS`
* `FAIL — RESPONSE QUALITY`

If failures are found, identify their demonstrated scope and likely boundary, but do not begin remediation until the step is complete.

### Report

```
service-progress/step-09-response-quality.md
```

Save retained test prompts and machine-readable results under:

```
benchmarks/results/service-step-09/
```

### Exit

If response quality is fundamentally inadequate, stop before spending additional engineering effort on latency, context, or throughput unless the owner explicitly decides otherwise.

If response quality is acceptable or conditionally acceptable, proceed to Step 10.

#### 9 status (2026-09-04 — 9B/9C/9D close-out; supersedes the 2026-09-03 entry below)

**Step 9-family determination under owner order (Pete, 2026-09-04): the
response-quality thread is CONDITIONALLY CLOSED; the amended Step 10 below is
authorized and current.** 9B (agent-trajectory quality localization,
2026-09-03) executed the full approved ablation ladder (111 rows: C0..C4 +
C1a-D/C1a-T/C2a-D/C2a-T x P1/P1b/P3/P2, R1/R2 replays) and attributed the
retained families: reply-directive instruction lines → directive-tag/empty +
fenced-JSON tendency (primary); sampler (llama defaults temp 0.8, which the
agent path actually runs) → P1 `!`-drop and terse variance; tool catalog →
prose dominance on tool-content probes and partial counteraction of fences.
9C (narrow 128K validation, 2026-09-04) re-confirmed C1a-D eliminates the
directive-tag/empty family at 128K with no window-size dependence; P1 `!`-drop
and prose families persist at 128K. 9D (production fix, 2026-09-04) made the
two reply-directive instruction lines conditional on an actual delivery
surface (`hasDeliverySurface`), verified through the real OpenClaw path after a
gateway restart (PASS on the 9D objective; directive/empty family 0/6 real
path vs frozen 9A r2 P1 INVALID rc=1 empty from that family; no sampler/tool/
prose/Step-10 changes). Residual strict-format families through the real path
(P1 `!`-drop terse `PLATANOS`, P1/P2 prose-wrapped and fenced JSON; P1 0/3,
P2 1/3 strict PASS at n=3) are carried into the amended Step 10 (below) as its
authorized focus — determine why the full agent path degrades exact-format
compliance relative to the reduced C1a-D baseline, keeping sampler effects and
agent-environment effects experimentally separate. The roadmap's former
Step 10 (TTFT) is deferred and renumbered Step 10A; the 9D long wall times
(532 s / 371 s) are preserved as a separate latency issue for Step 10A, not
analyzed in Step 10. Reports: `service-progress/step-09b-localization-report.md`,
`service-progress/step-09c-128k-validation-report.md`,
`service-progress/step-09d-report.md`; evidence:
`benchmarks/results/service-step-09b-verify/`, `service-step-09c-verify/`,
`service-step-09d-verify/`; 9D patch retained at
`service-progress/step-09d-dist-diffs/`. Step 10 plan pre-registered at
`service-progress/step-10-format-degradation-design.md`.

#### 9 status (2026-09-03 — post-9A frozen-suite rerun; supersedes the 2026-09-02 baseline below)

**FAIL — RESPONSE QUALITY (agent trajectory).** Step 9A remediation completed
and patch set frozen (2026-09-03): reply-directive/separator leakage fixed,
post-generation 630 s stall fixed, no cloud fallback on any probe, and the
restored reply-directive instructions retained (removing them had caused a
separate response-quality regression). P1's residual exact-output failure was
localized to the agent trajectory (the model itself generated `PLATANOS` after
an unnecessary failed `update_goal` tool round; no OpenClaw stage transforms
`PLATANOS!` → `PLATANOS`) and is classified as Step 9 response-quality evidence,
not a 9A mechanical defect. The complete frozen Step 9 suite was then rerun
unchanged through the repaired agent path, one fresh isolated session per probe
(`agent:kimi:step09-r2-<label>`; manifest re-frozen before probe 1; prompts
SHA-256 identical to the frozen baseline; frozen scorer untouched). Rerun
scoring (rubric.csv): P2 exact `{"ok": true}` PASS; P6 PASS; P5 count correct
(5) but format FAIL (prose); P1b/P3/P4/P7 FAIL (clarifying question / prose
instead of the required bare exact outputs); P1 INVALID (client rc=1 — empty
visible reply in that fresh session). Mechanical layer clean on every probe:
zero leakage, zero fallback, zero stall (all completions within 16–414 s).
Direct llama-server controls still return the exact expected answers — the
remaining boundary is the agent trajectory's strict-format compliance, not the
Kimi model and not the repaired response path. Evidence:
`benchmarks/results/service-step-09/` (rerun manifest, per-probe verbatim
outputs, rubric.csv); pre-remediation FAIL baseline archived at
`benchmarks/results/service-step-09-baseline-2026-09-02/`; 9A evidence at
`benchmarks/results/service-step-09a-verify/`; reports:
`service-progress/step-09-response-quality.md` and
`service-progress/step-09a-agent-response-path.md`. STOPPED for review; Step 10
not begun.

#### 9 status (2026-09-02, pre-remediation — superseded)

**FAIL — RESPONSE QUALITY (agent path).** Suite run exactly once through the
actual OpenClaw agent path (agent `kimi`, model pinned to
`llama-server/kimi-linear-48b`, fresh isolated session per probe; manifest
frozen before probe 1; P1-P7/P1b). P1 (literal instruction following) and
P2 (constrained output) FAILED with leaked `[[reply_to_current]]:` /
`[[reply_to:]]` directive prefixes in the delivered agent text; P3 and P5
PASSED; P1b/P4/P6/P7 INVALID (agent-path turns exceeded the 1200 s client
timeout - no scorable completion). No cloud fallback on any probe; every
probe exercised the live Kimi server. Direct llama-server controls
(attribution only) return the exact expected answers (`PLATANOS!`,
`{"ok": true}`) - the demonstrated failure boundary is the OpenClaw agent
response path (directive-prefix leakage) plus agent-path per-turn latency
(Step 10 territory), NOT the Kimi model/runtime. No remediation; Step 10
not begun. Evidence: `benchmarks/results/service-step-09/` (manifest,
prompts/expected, per-probe verbatim outputs, rubric.csv, validation/);
report: `service-progress/step-09-response-quality.md`. STOPPED for
review.

---

### Step 9A — Agent Response-Path Remediation

**Status:** COMPLETE (2026-09-03) — patch set frozen; remediation verified
(leakage fixed, stall fixed, no cloud fallback, directive instructions
restored/retained); P2 passes exact output; P1's residual failure classified as
Step 9 response-quality evidence (localized to the agent trajectory). Frozen
Step 9 suite rerun through the repaired path completed; Step 9 determination:
FAIL — RESPONSE QUALITY (agent trajectory). Reports:
`service-progress/step-09a-agent-response-path.md`,
`service-progress/step-09-response-quality.md`; evidence:
`benchmarks/results/service-step-09a-verify/`,
`benchmarks/results/service-step-09/`.

Step 9 response-quality testing exposed failures in the OpenClaw agent response path rather than the underlying Kimi model. In the literal-instruction control, direct llama-server returned the expected `PLATANOS!`, while the OpenClaw path leaked an internal `[[reply_to_current]]` directive, altered the final output, and exhibited a severe post-generation stall despite model computation completing normally.

**Goal:** Repair and validate the OpenClaw response path before completing Step 9 response-quality qualification.

Scope is limited to the demonstrated agent-path defects:

* prevent internal reply directives or separators from leaking into or altering final responses;
* eliminate the post-generation stall that can leave completed headless agent runs blocked until gateway timeout;
* preserve the frozen Kimi model, llama-server behavior, sampler, prompts, context configuration, and Step 9 test suite.

**Acceptance:** P1 must return exactly `PLATANOS!` and P2 exactly `{"ok": true}` through the real OpenClaw agent path, without directive leakage, cloud fallback, or unexplained post-generation delay. Verify repeatedly in fresh sessions, then rerun the frozen Step 9 suite unchanged.

Retain evidence in:

```
service-progress/step-09a-agent-response-path.md
benchmarks/results/service-step-09a-verify/
```

After 9A passes, return to Step 9 and classify response quality against the repaired agent path. Do not begin Step 10 until Step 9 is complete.

---

## 10. Localize Real-Path Exact-Format Degradation (amended 2026-09-04)

**Status: COMPLETE — determination PASS (2026-09-04); STOPPED for review.**
Report: `service-progress/step-10-format-degradation-report.md`; design:
`service-progress/step-10-format-degradation-design.md`; evidence:
`benchmarks/results/service-step-10/{offline,realpath,analysis}/`. All three
pre-registered legs executed against the live agent endpoint (127.0.0.1:18080,
64K): offline Legs 1+2 180/180 rows (env ladder C0/C1/C1a-D/E2/E2T at the
agent's real default sampler + temp sweep 0.0/0.8/1.6 at C0/E2, n=10/cell,
determinism assertion passed: all temp-0 cells 10/10 byte-identical); real-
path Leg 3 (3 fresh headless step10 sessions/probe merged with frozen 9D n=3
-> n=6). Determination: P2 H-S1 sampler-primary (greedy floor exact 10/10 at
E2; offline E2/E2T at default 8/10/5/10 indistinguishable from real path 4/6,
Fisher p=0.60/0.63; reduced-baseline 3/3 exact was small-n luck at a favorable
draw; lever = per-model sampler configuration, recommendation only). P1
ATTRIBUTED model+prompt at the E2 payload (`!`-drop deterministic at greedy:
10/10 identical `PLATANOS`, 0/10 exact; real path 0/6 == offline E2/E2T 0/10,
p=1.0; NOT sampler-fixable). The 9D-gated two lines (C1->E2) remove the
directive-leak family offline with no exact-rate change — 9D gate semantics
confirmed. NO production patch made. Real-path liveness note carried to Step
10A: three client context-overflow failures (walls 581.8/715.3/823.9 s)
preserved at `realpath/failures/`. Former Step 10 (TTFT) is deferred to Step
10A below.

Former status (superseded): ACTIVE (2026-09-04). Owner-authorized (Pete) as the current step;
Step 9D treated as PASS and its patch retained. Scope: the remaining real-path
response-quality failures — P1 punctuation loss (`PLATANOS` vs `PLATANOS!`)
and P1/P2 prose/fenced-output behavior. Deliverable is a determination with
retained evidence; NO production patch is authorized by this step (stop for
review first). Former Step 10 (TTFT) is deferred to Step 10A below.

### Goal

Determine why the full OpenClaw agent path degrades exact-format compliance
relative to the reduced baseline (frozen 9B/9C C1a-D offline cell: P1 3 terse
bare `PLATANOS`, P2 3 exact), with sampler effects and agent-environment
effects kept experimentally separate.

### Work

Keep the frozen model, prompts, expected outputs, scorer, sampler defaults,
and the 9D patch unchanged. Run three legs against the same live llama-server
endpoint the agent uses (127.0.0.1:18080, 64K):

1. Sampler axis (offline): payloads {C0, E2} x {P1, P2} at temperature
   {0.0 fixed-seed, 0.8 server default, 1.6}, n=10 — quantifies the sampler's
   variance contribution and the greedy floor at the environment-equivalent
   payload. Request-scoped `temperature`/`seed` fields only; the production
   agent path sends no sampler fields, so production behavior is untouched.
2. Environment axis (offline, at the real path's default temp 0.8, n=10):
   payload ladder C0 → C1 (full system) → C1a-D (whole directives section
   removed = the reduced baseline) → E2 (only the two 9D-gated reply-directive
   lines removed = the true current headless probe prompt) → E2T (E2 + the
   29-tool catalog). E2 is the previously-unmeasured bridge between the
   reduced baseline and the real path.
3. Real-path reference (3 fresh headless sessions per probe, no --deliver,
   64K pinned, same dist state as 9D), combined with the frozen 9D n=3 → n=6.

Wall times are recorded as covariates only; the 9D long wall times are a
separate Step 10A (TTFT) issue. See the pre-registered design:
`service-progress/step-10-format-degradation-design.md`.

### Acceptance

Step 10 is complete when the evidence answers:

1. At the environment-equivalent payload, is the greedy (temp 0) floor exact
   for P1/P2? (determinism assertion, 10/10 identical)
2. At the real path's default temp 0.8, how high is exact-format compliance
   offline at C1a-D, E2, and E2T, with what Wilson CI — and does the real
   path's n=6 distribution fall inside or outside that interval?
3. Which layer contributes what: the two gated lines (C1 vs E2), the
   surviving directive-section lines (E2 vs C1a-D), the tool catalog
   (E2 vs E2T), or the sampler mode itself (temp ladder)?
4. Is the real-vs-reduced delta sampling noise at n=3, a sampler-mode effect,
   or an unmodeled agent-environment layer (trajectory/multi-call)?

Classify the result as:

* `PASS` — delta attributed with CI support;
* `PARTIAL` — layers ranked but one leg underpowered;
* `FAIL` — evidence cannot separate the effects.

No production patch. If the evidence identifies a lever (e.g., per-model
sampler configuration), record it as a recommendation for owner decision.

### Report

```
service-progress/step-10-format-degradation-report.md
```

Design/plan retained at:

```
service-progress/step-10-format-degradation-design.md
```

Machine-readable artifacts under:

```
benchmarks/results/service-step-10/
```

### Exit

Stopped for review with the report. If TTFT is needed next, proceed to
Step 10A; otherwise continue to Step 11 after owner review.

---

## 10A. Qualify Time to First Token (deferred — formerly Step 10; superseded as the active step by the amended Step 10 above, 2026-09-04)

**Status: COMPLETE — determination PARTIAL (2026-09-04 21:xx MST); STOPPED for
review.** Report: `service-progress/step-10a-latency-overflow-report.md`;
design: `service-progress/step-10a-latency-overflow-design.md`; evidence:
`benchmarks/results/service-step-10a/{64k,128k,analysis}/`. 64K leg (valid
18/18 instrumented real-path turns, P1/P2 x 9): zero precheck overflows; all
17 parsed docs report `contextTokens = 32768 (resolved)` while the server runs
65536 (GGUF n_ctx_train 1M) — overflow family localized to a CLIENT-SIDE
precheck against the provider-config-resolved context window
(openclaw.json `llama-server` entry `contextWindow: 32768`), NOT server
capacity. Why 582–824 s: walls are accumulated multi-round llama cost
(13–44 tok/s prefill, 5–9 tok/s decode per task; 2–17 tasks/turn), not the
instant precheck refusal. Compaction evidence: P2-r7 (882 s, 17 tasks)
aborted via the auto-compaction loop-guard — second long-wall failure family.
128K empirical comparison NOT EXECUTABLE on this host: three temp-server
attempts (expert cache 8192/4096/2048 MB) all Metal-OOM'd
(`kIOGPUCommandBufferCallbackErrorOutOfMemory` / `failed to fit params`);
9C precedent shows 128K runs when host memory is free — leg driver retained
for re-run. Recommendation (not applied): align the `llama-server` provider
`contextWindow` (32768 → 65536) or pin the real path to the `kimi-local`
65536 entry. No production patch/config/sampler/prompt change. Dominant 64K
latency phase: llama prefill+decode across multi-round trajectories
(~12.7K-token prompt re-sent per round); TTFT for trivial probes
27 s–14 min → latency workstream after review.

### Goal

Determine whether the current Kimi OpenClaw agent can begin responding quickly enough for productive interactive use and identify exactly where first-response latency is spent.

The primary question is:

> What determines user-visible time to first token in the current production agent?

Do not optimize TTFT during this step.

Measure and attribute it first.

### Work

Measure the complete first-response path through the actual OpenClaw agent:

```
user dispatch
    ↓
OpenClaw request construction / admission
    ↓
provider dispatch
    ↓
llama-server request receipt
    ↓
prefix/cache handling
    ↓
prompt evaluation
    ↓
first decoded token
    ↓
first user-visible token
```

At minimum test:

* genuinely cold/unprefilled state where practical;
* explicitly prefilled state;
* warm same-server state with expected prefix reuse;
* a tiny user request;
* a representative ordinary agent request.

The existing `/prefill` mechanism and warm-state registry should be used as currently implemented.

Do not redesign them during qualification.

### Measurements

For each run record at minimum:

* final prompt/input token count;
* cached/reused tokens;
* newly evaluated prompt tokens;
* prompt-eval duration;
* prompt-eval tok/s;
* OpenClaw dispatch time;
* llama-server request receipt time;
* prompt-eval start and completion;
* first decoded-token time;
* first user-visible-token time;
* total TTFT;
* server PID;
* bootstrap fingerprint;
* warm-state status;
* compaction, retry, fallback, restart, or error events.

Decompose TTFT into measured components rather than reporting only one wall-clock number.

Determine whether the dominant contribution is:

* OpenClaw overhead;
* uncached bootstrap/prompt evaluation;
* failed or incomplete prefix reuse;
* expected suffix evaluation;
* inference-server initialization;
* post-prefill/first-token transition;
* another measured source.

### Existing Evidence

Earlier service work proved that explicit prefill and same-server prefix reuse can dramatically reduce evaluated prompt tokens.

Those historical results establish capability, not current production TTFT.

Step 10 must measure the current promoted runtime and current OpenClaw agent configuration.

### Acceptance

Step 10 is complete when we can answer:

1. What is current user-visible TTFT?
2. How does it differ between cold, prefilled, and warm conditions?
3. How many prompt tokens are actually evaluated in each condition?
4. Where is the dominant latency?
5. Is the current behavior practical for interactive agent use?

Classify the result as:

* `PASS`
* `CONDITIONAL PASS`
* `FAIL — TTFT`

Do not invent a runtime optimization merely because a component is measurable.

If TTFT is unacceptable, route the demonstrated bottleneck to the appropriate workstream after this step is complete.

### Report

```
service-progress/step-10-ttft.md
```

Save machine-readable timing artifacts under:

```
benchmarks/results/service-step-10/
```

### Exit

If the limiting factor is OpenClaw bootstrap, prefix reuse, prefill, session handling, or service behavior, continue service work with a bounded remediation step.

If the limiting factor is raw inference-runtime prompt processing, route that measured problem to `ROADMAP.md`.

If TTFT is acceptable, proceed to Step 11.

---

## 10B. Validate the 65536-Aligned Provider Context Window (owner order 2026-09-05)

**Status: COMPLETE — PASS (determination) (2026-09-05); STOPPED for review.**
Config change applied and validated: openclaw.json
`models.providers.llama-server` model `kimi-linear-48b` `contextWindow`
32768 → 65536 (qwen entries untouched; backup
`~/.openclaw/openclaw.json.bak-step10b-20260905`). Gateway restarted via
detached supervisor `tools/service_step10b_supervise.sh` (new PID 47134,
08:20:45 MST); llama-server untouched (64K). Retained Step 10A liveness
workload rerun into a NEW tree (`benchmarks/results/service-step-10b`, fresh
`step10b` session keys): 18/18 real-path turns, all docs report
`contextTokens: 65536 (resolved)` (frozen 32K baseline: 32768 ×17) — the
precheck bound is now server-aligned. Results: precheck `context_overflow`
0/18 (baseline 0/18; bound moved 32768→65536 and assembled prompts stayed far
below it, max promptTokens 28,964); rc=0 17/18 (baseline 17/18); rc=1 1/18 —
P1-r5 1202.5 s client timeout (read-tool loop, 130 llama tasks, NOT a precheck
refusal) vs baseline P2-r7 882.5 s compaction loop-guard abort (same
runaway-tool-loop family, different terminator); wall min/median/max
13.0/22.5/1202.5 s (baseline 26.8/110.1/882.5); prompt tokens at last OK call
median 12,736 (baseline 12,734); llama tasks mostly 1/turn vs baseline 2+.
Analyzer fixed for single-leg trees (supervisor auto-analysis had crashed with
KeyError '128k'; re-run clean). Report:
`service-progress/step-10b-contextwindow-validation-report.md`. Evidence:
`benchmarks/results/service-step-10b/{64k,analysis}/` + supervise.log. STOP
for review before any further change.

## 10C. Localize and Bound the Runaway-Tool-Loop Liveness Family (owner order 2026-09-05)

**Status: COMPLETE — PASS (determination) (2026-09-05); STOPPED for review.**
No production patch. No context/sampler/prompt/tool change. Anchors: 10A
P2-r7 (882.5 s compaction-loop-guard abort) and 10B P1-r5 (1202.5 s client
timeout, 130 llama tasks). Both decoded to the SAME loop: the model emits
`read` of a nonexistent `/Users…DMAP.md` path (literal U+2026 ellipsis in the
path, an apparent truncation artifact); every attempt is byte-identical and
every failure is byte-identical (P1-r5: 117 identical read calls/results of
129 total calls), so the model receives no discriminating feedback and
re-emits the identical `(tool, args, result)` triple indefinitely.

Why repeated calls continue: nothing counts identical no-progress triples
except the post-compaction guard, which arms ONLY after auto-compaction. At
aligned 64K, prompt context stays ~9–24K (P1-r5 `shouldCompact=false`),
compaction never fires, the guard never arms, and the loop runs to the client
timeout. Which guards fire today: only the post-compaction guard
(default-on); it fired correctly in P2-r7 after auto-compaction. The shipped
general rolling-history detector (`tools.loopDetection`, warn@10 / CRITICAL
block@20 identical no-progress / global breaker@30 / unknown-tool@10,
history 30, per-run scoped; docs/tools/loop-detection.md) is `enabled:false`
by default and openclaw.json has no `tools.loopDetection` block → inert in
both anchors. Idle-timeout breaker is paid-provider-only; provider fetch
timeout is a backstop, not a loop guard.

Boundary (shipped semantics): with `tools.loopDetection.enabled: true`, the
first critical blocks the whole tool batch before execution; the model gets
one more response; a second critical in the same run ends the run. Config
schema is zod-strict with only `enabled` (thresholds hardcoded in
`resolveLoopDetectionConfig`; dead-config-keys test confirms
historySize/warningThreshold/detectors are not configurable).

Validation (offline, actual shipped detector): replay of both retained real
call streams through the installed dist module
(`tool-loop-detection-CWrUtzrR.js`, `detectToolCallLoop`, `{enabled:true}`)
via `tools/service_step10c_replay.mjs`. P1-r5: first warning ordinal 13
(+122 s), first CRITICAL generic_repeat ordinal 24 (+235 s), second critical
(run-end per docs) ordinal 27 (+286 s) — the 1202.5 s runaway would have been
terminated at ~235–286 s. P2-r7: 0 warnings / 0 criticals — its in-window
streak (≤8) stays below threshold, so the general detector would NOT fire
there and the post-compaction guard remains the correct terminator for the
compaction-cycle variant (no false-positive risk on that path).

Smallest liveness safeguard (proposed, NOT applied): config-only
`agents.entries.kimi.tools.loopDetection.enabled: true` (or global
`tools.loopDetection.enabled: true`) — arms the shipped rolling-history
detectors while the post-compaction guard stays armed. Design:
`service-progress/step-10c-loop-liveness-design.md`. Report:
`service-progress/step-10c-loop-liveness-report.md`. Evidence:
`benchmarks/results/service-step-10c/` (anchor call streams, replay results).
STOP for review before any production patch.

## 10D. Apply + Live-Validate the LoopDetection Safeguard (owner order 2026-09-05)

**Status: COMPLETE — PASS (live validation); STOPPED for review.**
Config-only change applied and validated live: openclaw.json
`agents.entries.kimi.tools.loopDetection.enabled: true` (single-line diff vs
backup `~/.openclaw/openclaw.json.bak-step10d-20260905`; no other config or
code change; context/sampler/prompts/tools/llama-server untouched). Gateway
restarted via detached supervisor (new PID 59990, 12:34:21 MST); llama-server
untouched/healthy 64K; every rep doc resolves `contextTokens: 65536`.

Validation (12 real-path turns, fresh `step10d` sessions, single-slot 64K):

(1) **Identical-read loop terminated by the general detector — PASS, live.**
Deterministic LOOP-STRICT probes (instruct exactly 25 identical `read` calls
on a nonexistent path) reproduced the P1-r5 family and the shipped detector
fired at the hardcoded boundary: read #20 → `CRITICAL: Called read with
identical outcomes 20 times. Session execution blocked to prevent runaway
loops.`; reads #21–26 vetoed (`deniedReason: tool-loop`, batch blocked); run
ended on the second critical. LOOP-STRICT-r1: rc=1, wall 165.6 s,
`livenessState: blocked` (27 identical read calls, NOT the 600 s client cap);
LOOP-STRICT-r2: rc=0, wall 189.8 s — detector fired at #20, model heeded the
block and completed. Anchor comparison: P1-r5 ran 130 llama tasks / 1202.5 s
with no guard; same loop now bounded at ~20 calls / ~2.8–3.2 min.

(2) **Legitimate multi-step tool use completes — PASS.** P1 ×2 (33/118 s),
P2 ×2 (14/15 s), NORM ×3 (240/220 s incl. absolute-path NORM-r3) all rc=0
with ZERO loop events (no false positives). Anomaly NORM-r1 (rc=1, 427 s,
"LLM request failed", 0 loop events) is probe-side: the NORM prompt used
repo-relative paths while the headless session cwd is `~/.openclaw`, causing
read/exec churn; not a detector action (absolute-path NORM-r3 rc=0 confirms).

(3) **Post-compaction guard unchanged — PASS.** Config diff is exactly one
added key; no compaction/post-compaction setting touched. Per shipped
semantics the post-compaction guard stays armed unless `enabled` is
explicitly `false` (docs/tools/loop-detection.md) — setting `true` keeps both
guardrails on. No compaction occurred in these short reps; the P2-r7
compaction-cycle path is untouched (its terminator remains the post-
compaction guard).

Also: engineered LOOP ×3 (non-deterministic) never fixated (model
investigated + answered; rc=0, zero events) — additional no-false-positive
evidence, which is why the deterministic LOOP-STRICT probes were added.

Design: `service-progress/step-10d-loopdetection-validation-design.md`.
Report: `service-progress/step-10d-loopdetection-validation-report.md`.
Evidence: `benchmarks/results/service-step-10d/` (env, supervise.log, 64k/
per-rep artifacts, analysis/summary.json, extra.log, prompts/). Drivers:
`tools/service_step10d_{leg,analyze,supervise,extra}.sh`/
`service_step10d_analyze.py`. STOP for review before any further change.

## 11. Qualify Usable Agent Context

### Goal

Determine how much context the current Kimi OpenClaw agent can use productively.

Stage 8 already established a different result:

> The native NoPE Kimi runtime can allocate, prefill, generate, preserve state, and retrieve a synthetic needle correctly through 128K context.

Do not repeat that capacity experiment.

Step 11 asks:

> Does the real OpenClaw agent remain useful as its working context grows?

### Baseline

Treat:

```
128K
```

as the current validated technical context milestone.

Treat:

```
256K and above
```

as untested, not failed.

Do not attempt 256K merely because the model can theoretically support it.

First establish whether the agent benefits from and behaves correctly within the already validated range.

### Work

Exercise the actual OpenClaw agent with progressively larger realistic working contexts.

At minimum compare:

* a fresh/small context;
* a representative established working session;
* a large context approaching the range required for real agent work.

Where practical, use retained conversation history, tool results, repository information, instructions, and other realistic agent material rather than synthetic filler.

Test whether the agent can:

* retain the current user instruction;
* retrieve relevant earlier information;
* distinguish current instructions from obsolete earlier instructions;
* use relevant tool results;
* avoid unnecessary repetition;
* avoid irrelevant historical archaeology;
* complete the requested task;
* emit a correct final answer.

### Measurements

At each tested context size record:

* total input tokens;
* cached tokens;
* newly evaluated tokens;
* prompt-eval time;
* TTFT;
* decode tok/s;
* total turn time;
* compaction behavior;
* memory where useful;
* task result;
* retrieval result;
* instruction-following result;
* tool-use behavior;
* final-answer behavior.

Distinguish:

* **technical context ceiling** — runtime cannot provision or execute;
* **useful-context ceiling** — runtime executes, but agent behavior becomes unreliable;
* **practical context ceiling** — behavior remains correct, but latency or resource cost makes the context operationally unattractive.

These ceilings need not be the same.

### 256K Decision Gate

Do not automatically continue the Stage 8 ladder.

Attempt 256K only if Step 11 demonstrates a concrete reason that more than the currently validated 128K context would materially improve the intended agent workload.

If 128K is already sufficient, record that result and leave 256K untested.

If 256K is authorized, treat it as a new measured rung using the Stage 8 native NoPE rules and the Step 11 agent-usability criteria.

### Acceptance

Step 11 is complete when we can answer:

1. How much working context does the agent actually need?
2. Does response quality remain acceptable as context grows?
3. Does the agent reliably retrieve and prioritize relevant information?
4. What latency cost does larger context impose?
5. What is the current useful/practical context ceiling?
6. Is there evidence that testing 256K would provide meaningful value?

Classify the result as:

* `PASS`
* `CONDITIONAL PASS`
* `FAIL — CONTEXT`

### Report

```
service-progress/step-11-usable-context.md
```

Save machine-readable results under:

```
benchmarks/results/service-step-11/
```

### Exit

If the agent is reliable through the context range actually required, do not pursue larger context merely because larger values are technically possible.

If context behavior is inadequate, classify whether the problem is:

* response quality;
* OpenClaw context/session construction;
* compaction;
* prompt-processing performance;
* runtime capacity;
* another measured boundary.

Route remediation only after classification.

Proceed to Step 12 once the usable-context requirement is understood.

---

## 11 status (2026-09-05 — sustained leg executed, owner order)

**Status: CONDITIONAL PASS (determination); STOPPED for review. No production
change.** Production config unchanged (aligned 64K + loop detection on).

Executed 30 realistic same-key multi-turn turns across three sustained
sessions (R research conversation ×10, C coding ×8, G growth-to-compaction
×12) at the single-slot 64K server, full per-turn instrumentation. Design:
`service-progress/step-11-usable-context-design.md`. Evidence:
`benchmarks/results/service-step-11/{env.txt,leg.log,64k,analysis,work}`;
analyzer: `tools/service_step11_analyze.py`; report:
`service-progress/step-11-usable-context.md`.

Headline results:
- Context accumulation works: same-key promptTokens grew R 17.4K→27.2K,
  C to 33.9K, G to 31.2K; auto-compaction fired successfully at ~30-34K
  (G5: 31.2K→17.1K with post-compaction guard armed; C5→C7 33.9K→22.7K) and
  sessions continued after compaction (G5/G10-12, C7/C8 rc=0). No technical
  ceiling hit in any turn (max assembled promptTokens ~34K, far under 64K).
- Research conversation (representative sustained workload): 10/10 rc=0,
  correct cross-turn recall at 27K (AGENTS phase-completion rule, report
  naming, step-10D status), zero loop/compaction events.
- Coding: 6/8 rc=0 with real artifacts (text_stats.py + tests compile);
  C2 and C6 hit the 1200s client cap on legitimate sustained multi-step
  turns (C2: 33 calls, 0 failures) — not runaways, not detector events.
- Growth session G: 5/12 rc=0. Failures were NOT context-capacity limits:
  the model repeatedly emitted U+2026-truncated absolute paths in read
  calls (same artifact family as the P1-r5/P2-r7 anchors: e.g.
  "/Users…VICE-ROADMAP.md"), causing File-not-found churn, argument churn
  (not identical, so the loop detector correctly did NOT fire), and runs
  ending blocked/"LLM request failed" at 17-28K context. Tool-call
  reliability issue under sustained multi-file workloads, not a 64K
  capacity boundary.
- Stability: llama-server + gateway stayed up for the entire 3.3h leg;
  zero real loop-detector events on legitimate work (no false positives);
  zero OOM/context overflow.
- Latency: decode steady 6.4-8.9 tok/s; TTFT (first prompt eval) grows
  with context — seconds at small context, ~100-350s first-eval at
  25-31K assembled (prefill ~43 tok/s) — the dominant latency cost;
  cache reuse high (cacheRead 20K-920K tokens/turn).
- 256K gate: NOT triggered — no evidence more context would help; the
  binding limits are per-turn latency, the 1200s client cap on long
  sustained turns, and the model path-truncation reliability artifact.

Classification per §11 acceptance: usable context ≥ ~30K assembled with
working auto-compaction; practical ceiling set by latency/TTFT and the
1200s per-turn cap; useful ceiling not reached in research/coding workloads.
Residual issues (U+2026 path artifact, client-cap timeouts) are model/
config-limit issues, not context-window capacity. Full classification in
`service-progress/step-11-usable-context.md`. STOPPED for review before any
production change.


---

## 11A. Localize the U+2026 Path-Truncation Failure (owner order 2026-09-05)

**Status: COMPLETE — determination delivered; STOPPED for review. No patch,
no config/prompt/sampler/tool change.** The U+2026 artifact family (Step 11
Session G + step-10/10A-era anchors: `/Users…VICE-ROADMAP.md`) was traced from
raw model output through OpenClaw's message/transcript layer.

Determination: the corruption is **OpenClaw-side and PRE-MODEL** — it enters
in the message-file ingestion → transcript-store path, not the model:
- **Authoring refuted:** corrected byte-accurate U+2026 inventory (python
  `"\u2026" in s` + od spot checks) shows ALL step-11/10d/10c/09 prompt files
  byte-clean (0 U+2026); only assistant report/design docs quote the artifact.
  The earlier "prompts contain U+2026" claim was a defective zsh `$'\u2026'`
  grep artifact.
- **Model refuted for clean input:** 8 raw llama-server control trials with
  the same read-tool schema + full clean path → model emits the clean path
  8/8, zero U+2026 (retained `raw-control-{0..7}.json`).
- **Clean-file probe:** byte-clean `probe-clean.md` → real `openclaw agent
  --message-file` run → the stored transcript user message (seq=1, written at
  session start BEFORE the model's first output) already contains real U+2026
  bytes (verified in the LIVE gateway DB
  `~/.openclaw/agents/kimi/agent/openclaw-agent.sqlite`, session
  `agent:kimi:step11a-probe-64k`). The model then copied the corrupted path
  into read args byte-identically → File-not-found churn → 200s cap (15
  calls / 13 failures). `finalPromptText` in the doc record is CLEAN while
  stored content is corrupted → a clean copy exists in the doc/prompt-text
  layer but the message content used for prompt assembly carries U+2026.
- **Not a pure length rule:** 88-char path (LOOP-STRICT-r1) and 75-char path
  (C-session) stored clean in the same live DB; 51-char SERVICE-ROADMAP paths
  (G1, probe) corrupted. Trigger condition unresolved (open question).
- **Exact dist function NOT isolated:** exhaustive minified-tolerant scan of
  dist for U+2026 middle-truncate helpers found many truncators
  (coerceDisplayValue 79/80@160, compactRawCommand half/half@120,
  compactProgressLineDetail 45%/rest, maskLifecycleIdentifier 4+4,
  redactSessionKey 6+6, shortId 8+4, ASCII-`...` truncateMiddle/
  middleTruncatePath, etc.) but NONE matches the observed token-level
  signature (sentence byte-identical, only the long path token shortened to
  head-6 `…` tail-7..15). Runtime tracing of the steer→store boundary is the
  identified follow-up; not executed here.

Step-11's "model tool-call reliability issue" label is refined: the symptom
is model-side faithful copying; the root is an OpenClaw-side content rewrite
upstream of the model. Service mitigation already in effect: prefer
repo-relative paths in workload prompts (standing practice from the
2026-09-02 memory note). Loop detection does not catch this family (varied
args). Report: `service-progress/step-11a-u2026-path-truncation-report.md`;
design: `service-progress/step-11a-u2026-path-truncation-design.md`;
evidence: `benchmarks/results/service-step-11a/` incl.
`11a-forensics-summary.md`; drivers: `tools/service_step11a_raw_control.py`.
STOPPED for review before any patch.


---

## 11B. Fix the Pre-Model U+2026 Path-Content Corruption (owner order 2026-09-05; fix authorization 2026-09-06)

**Status: PASS — 11B FIX LIVE IN PRODUCTION after requalification 2
(2026-09-06 21:22-21:23). Run 1 (12:02): test-oracle FAIL with correct
runtime behavior (5/6 legs byte-identical; leg-5 stored bytes identical to
isolated PASS). Run 2 (15:01): formal requalification, inconclusive at
harness level (legs-driver FK cleanup defect; all legs timeout-no-stored-row;
rollback). Run 3 requalification 2 (21:22): ALL SIX PRODUCTION LEGS PASS,
0 FK violations, patch retained. Active sha 8baf4746..., gateway healthy on
:18789. Corrected history: prior "production applied" claim was FALSE;
production stayed pristine until run 1.**

**UPDATE 2026-09-08 — 11B(i) FOLLOW-UP FIX — PASS under OpenClaw 2026.9.3.**
The 2026.9.3 upgrade had replaced the patched generated dist
(`redact-CquADQ9-.js`, sha 8baf4746...) with fresh output carrying the
original 184-byte pre-fix pattern (`redact-DMnNBHXb.mjs`, sha
b80161806f796ac4...), because the 11B fix existed only as a local patch
to the generated artifact, never in stable source in the installed
package. Reconciliation (addendum 4) recorded the regression as FAIL.
On owner order 2026-09-08 12:24 the durable re-fix was authorized:
the identical qualified three-guard transform (real-digit `[0-9]`,
leading-slash rejection `(?!\/)`, 3-slash negative lookahead) was
applied to the live 184-byte template in `redact-DMnNBHXb.mjs` ->
257-byte fixed form, verified byte-equal to the retained requal2
pattern. Replica module qualification PASS (benign legs byte-identical,
0 U+2026; leg 5 masked); production requalification PASS on the first
clean run: ALL SIX PRODUCTION LEGS PASS (legs 1-4/6 byte-identical, 0
U+2026; leg 5 raw secrets absent + masked present), 0 FK violations,
patch retained. Live sha b54b13f1d79cb98a..., gateway healthy on :18789,
llama pid 7021 constant, FK 0. (Run 1 of the requalification FAILED at
harness level only: the retained legs driver hardcoded seq=1 for the
user message, but 2026.9.3 inserts session/provider events first; the
stored user row was already byte-identical under the patched module.
Driver fixed to scan ascending seq for role=user content; rollback
verified.) PERMANENT GATE stands (behavioral, not filename/SHA):
discover the active redaction implementation at runtime via the
transcript-store import chain; record artifact SHA as provenance only;
run the deterministic corpus and require legs 1-4/6 byte-identical with
0 U+2026 and leg 5 masked. A future OpenClaw upgrade will regenerate
this dist and drop the patch again — re-run the 11B(i) re-apply driver
(`benchmarks/results/service-step-11b/fix-prod-11bi-20260908/`) and
requalify after every upgrade. Evidence:
`benchmarks/results/service-step-11b/fix-prod-11bi-20260908/`;
report addendum 5.

Root cause (Gate 2 localization): the default AWS-secret bare-value heuristic
(`AWS_SECRET_ACCESS_KEY_VALUE_PATTERN`, dist `redact-CquADQ9-.js` line 831)
matched exactly-40 maximal runs of `[A-Za-z0-9/+=]` containing upper + lower
+ digit/slash/plus/equal + a non-hex char. Benign absolute-path runs of 40
contiguous letters+slashes false-positived because `/` satisfied the symbol
test; `maskToken` then rewrote the run to head-6 + U+2026 + tail-4
(e.g. `/Users` + U+2026 + `VICE-ROADMAP.md`), corrupting stored message
content before the model ever saw it.

Fix (smallest change; single constant at dist line 831; isolated
`/tmp/oc11b-pkg` replica only): the symbol lookahead now requires a real
digit `[0-9]` (slash, plus, equals no longer count); a run beginning with
`/` is rejected `(?!\/)`; a run containing 3+ slashes is rejected. Labeled
AWS patterns, the prefilter, and every other redaction default are
untouched. No model, sampler, context, or tool configuration change.

Validation (all on the patched isolated replica :18791; production dist sha
`5505b775...` verified untouched):
1. Regex unit harness, 111-entry corpus: every path false positive matched
   by OLD is rejected by NEW; every true AWS-secret shape still matches
   OLD and NEW; labeled-form behavior unchanged; harness file byte-clean.
2. E2E storage byte-proofs through the real CLI and the isolated transcript
   DB (seq=1, fresh session keys): original clean-file discriminator
   byte-identical with 0 U+2026; retained G-style path cases byte-identical
   with 0 U+2026; long benign paths (old-heuristic-matching and
   non-matching shapes) byte-identical with 0 U+2026; long-message
   regression (44,520 B) byte-identical with 0 U+2026; representative true
   AWS-secret-shaped values still redacted (raw value absent, masked
   head-6 + U+2026 + tail-4 form present).
3. Accepted trade-off (recorded in the design doc): standalone-value
   detection retained on real-digit keys (~99.9%), keys without a leading
   slash (~98.4%), keys with at most 2 slashes (~97.7%); rare true keys
   starting with `/` or containing 3+ slashes remain redacted in labeled
   contexts because the labeled AWS patterns are separate and unchanged.

Evidence: `benchmarks/results/service-step-11b/fix/` (`line831.diff`,
`patch-record.txt`, `fix-results.json`, per-leg `.msg` inputs, all
byte-clean). Design and Gate 1/2 records:
`service-progress/step-11b-u2026-trace-fix-design.md`,
`benchmarks/results/service-step-11b/{gate1-isolated-reproduction,localization-boundary-trace}.md`.
Report: `service-progress/step-11b-u2026-trace-fix-report.md`. Drivers
retained under `/tmp/oc11b-env` (replica env): `fix-regexcheck.mjs`,
`fix-legs.py`, `patch831.mjs` (all byte-clean).

STOPPED for review before applying the fix to production.

## 11B Production Application Record (2026-09-06 12:02-12:13)

Executed per owner authorization (12:02:40) via the retained detached
orchestrator `benchmarks/results/service-step-11b/prod-apply/
orchestrate-prod-restart.sh` (log `orchestrator.log`; marker
`orchestrator.marker` = FAIL):

1. Archived the pristine active file:
   `prod-apply/pristine-redact-CquADQ9-.js.pre-apply-20260906-1202`
   (sha 5505b775...; record `prod-apply-record.txt`).
2. Replaced the ACTIVE `/opt/homebrew/lib/node_modules/openclaw/dist/
   redact-CquADQ9-.js` with the validated replica fix (byte copy).
3. Verified the ACTIVE pathname: sha 8baf4746... MATCH; new pattern confirmed.
4. Removed the stray sidecar `redact-CquADQ9-.js.patched-20260906-1002`
   (retained in `prod-apply/` evidence).
5. Detached `launchctl kickstart -k gui/501/ai.openclaw.gateway`: gateway
   59990 -> 13064, healthz 200, post-restart sha 8baf4746..., llama 200.
6. Six production E2E legs (real CLI -> production kimi transcript DB):
   legs 1, 2, 3, 4, 6 PASS byte-identical with 0 U+2026; leg 5 FAIL
   (raw_absent True, masked_present False, 12 U+2026, 12 secrets) ->
   authorized rollback: pristine restored (5505b775...), gateway restarted,
   healthz 200.
7. Updated SERVICE-ROADMAP.md + 11B report with corrected history.

Leg-5 diagnosis (`prod-apply/leg5-diagnosis.txt`): the production stored
transcript for leg 5 is BYTE-IDENTICAL to the isolated stored transcript
that PASSED (both 400 B, 12 U+2026, mask shapes [(6,4),(10,4)]). The prod
driver regex-extracts 12 windows from the raw .msg (6 true secrets + 6
label-prefixed phantom windows such as `key=`+secret[:36]) and requires a
head-6 masked form for each; the validated isolated driver checks only the
6 known secrets. Re-running the 12-window method against the isolated PASS
bytes also yields masked_present False, proving the leg-5 FAIL is a checker
artifact, not a production regression: patched code redacts identically in
production and replica, and true-secret redaction is preserved.

State after run: production dist pristine (5505b775...), gateway healthy on
:18789 (launchd PID 13395), llama untouched, no config/plist/prompt changes.
Isolated replica gateway :18791 (PID 5846) died with the production restart's
process-tree teardown; replica package and DB remain intact under
/tmp/oc11b-env and can be relaunched with its launch script. STOPPED before
Step 12 pending owner decision on the leg-5 checker discrepancy.


---

## 11B Requalification Run (2026-09-06 15:00-15:40, owner order 13:29:51)

Corrected only the production E2E leg-5 checker to the isolated driver's
known-secret method (`prod-apply/prod-fix-legs-req.py`; diff vs run-1
driver = leg-5 block only). Proof PASS (`prod-apply/requal-checker-proof.json`):
corrected checker passes the retained isolated PASS transcript (6 secrets,
raw-absent, masked head-6/tail-4, 400 B) and fails an unredacted control.

Patch reapplied to the ACTIVE pathname (sha 8baf4746... verified); detached
kickstart (gateway PID 17346, healthz 200, post-restart sha 8baf4746...,
llama 200); six production legs ran with the corrected checker 15:01:51-
15:38:29: ALL SIX FAILED timeout-no-stored-row. Per-leg CLI stderr: leg 1
"agent turn was not durably admitted"; legs 2-6 SqliteIntegrityError
(foreign_key_check failed: session_transcript_active_events row 56168
references transcript_events). Root cause: the legs driver's reset_session
deletes probe transcript_events/session_windows rows but leaves
session_transcript_active_events references; against the live production DB
with FK enforcement this orphaned rows and blocked every new admission, so
no stored transcript existed to compare. Harness defect, not a redaction
regression.

Authorized rollback executed: pristine restored (5505b775...), gateway
restarted, healthz 200. Marker FAIL. Run distinction: run 1 = test-oracle
FAIL with correct runtime behavior; run 2 = formal requalification,
inconclusive at harness level. Production pristine and healthy; fix not
live. STOPPED before Step 12.

## 11B Requalification 2 Record (2026-09-06 21:05-21:23, owner order 21:05:46)

Root cause of the prior database corruption: the production-legs harness
reset_session deleted probe parents (transcript_events, session_windows)
with PRAGMA foreign_keys OFF (python sqlite3 default), so the declared ON
DELETE CASCADE never fired; dependent rows (session_transcript_active_events,
transcript_event_identities, session_transcript_index_state,
transcript_rewrite_watermarks, trajectory_runtime_events, session_nodes
subtree, board_tabs/board_widgets) were orphaned, and the OpenClaw agent's
foreign_key_check failed on the next admission (run 2: all six legs
timeout-no-stored-row) and disrupted a gateway startup.

Repair (harness only; no OpenClaw production DB logic changed): new
fk_reset.py deletes probe-owned dependents explicitly child-first across
the full inspected FK graph, enables PRAGMA foreign_keys=ON, then asserts
PRAGMA foreign_key_check is empty. Driver prod-fix-legs-req2.py imports it
and adds a per-leg FK gate (exit 3 on any violation, evidence written
first). The leg-5 known-secret checker is byte-identical to the run-2
corrected checker; the validated one-line redaction patch is unchanged.

Independent proof (requirement 2, on a backup copy of the production DB;
prod-apply/fkproof.json): reset of the six leftover probe sessions removed
all probe rows, PRAGMA foreign_key_check returned zero rows, and every
non-probe table digest was byte-unchanged.

Requalification run (exactly once, detached orchestrator; log
prod-apply/requal2-orchestrator.log, marker PASS): patch applied to the
active pathname (sha 8baf4746... verified); kickstart -> gateway PID 56719,
healthz 200, post-restart sha 8baf4746..., llama 200; six production legs
ran 21:22:41-21:22:59 with the FK-safe driver, all six OK:
  1-discriminator byte_identical 126 B / 0 U+2026
  2-gstyle byte_identical 808 B / 0 U+2026
  3-longpath-match byte_identical 710 B / 0 U+2026
  4-longpath-nomatch byte_identical 528 B / 0 U+2026
  5-true-secret raw_absent True, masked_present True, 6 secrets
  6-long-message byte_identical 44,520 B / 0 U+2026
Final production PRAGMA foreign_key_check: 0 rows. Patch retained and live.
Evidence: benchmarks/results/service-step-11b/fix-prod-requal2/ and
prod-apply/ (fkproof.json, requal2-record.txt, requal2-orchestrator.log).
STOPPED at the Step 12 gate.

## 12. Qualify Decode Throughput

**Status: PASS (2026-09-06). Decode throughput qualified through the real
OpenClaw agent path on representative Step 9-11 workloads; runtime
remains frozen; no decode-optimization problem opened. Measured decode
tok/s medians (llama eval windows): short-answer 10.54 (n=2), engineering
9.29 (n=3, the decode-bound productive class, inside the promoted 9-11
band at its low edge), research 8.27 (n=2, prefill-bound 75-78% of wall -
not decode-limited), long tool-heavy 7.43 (n=1 partial; runner-capped at
900 s; prefill-bound 76%). Decode is the dominant wall-time share only for
engineering turns (68-70%); short turns are TTFT/prefill-bound (48-59%)
and research/long turns are prefill-bound (75-78%), so decode work would
not change their latency. Long-g1 (94 KB read) exceeded the 900 s runner
cap while still processing - recorded as a prefill/context-bound
observation, not a decode failure. Report:
service-progress/step-12-decode-throughput.md; evidence:
benchmarks/results/service-step-12/ (decode-summary-final.json,
step12-verdict.json, parse_decode.py, per-leg client/llama/gateway
windows). Production state unchanged: 11B sha 8baf4746... live, FK 0,
gateway :18789 healthz 200, llama :18080 200. STOPPED at the
Post-Step-12 gate.**

### Goal

Determine whether the current Kimi generation speed is sufficient for productive OpenClaw use and whether further decode optimization is justified by measured user impact.

The primary question is:

> Is decode throughput still a meaningful usability bottleneck after response quality, TTFT, and usable context have been qualified?

### Baseline

The current promoted runtime is expected to produce approximately:

```
9–11 decode tok/s
```

under the live W4 / 8 GiB expert-cache configuration.

Capture the actual current baseline before qualification.

Do not use archived measurements as the acceptance comparison.

### Work

Use representative successful tasks from Steps 9–11.

Measure generation behavior through the real OpenClaw agent rather than relying only on isolated llama-cli benchmarks.

For each workload record:

* generated tokens;
* decode duration;
* decode tok/s;
* TTFT;
* total model wall time;
* tool-execution time;
* total turn wall time;
* response length;
* whether response length was appropriate to the task.

Separate:

```
waiting for first token
```

from:

```
waiting for generation
```

from:

```
waiting for tools
```

from:

```
unnecessary model verbosity
```

A 90-second turn is not evidence of a decode-throughput problem unless decode actually accounts for the relevant portion of those 90 seconds.

### Attribution

Determine the contribution of decode to real user-visible latency.

Answer:

1. What fraction of representative turn time is decode?
2. How long does a short answer take after the first token?
3. How long does a normal engineering answer take?
4. How long does a long tool-heavy answer take?
5. Would a plausible throughput improvement materially change the user experience?

Use the current runtime profile as supporting evidence where appropriate, but qualify the live production behavior directly.

### Optimization Decision

Do not continue runtime optimization merely because higher tok/s is technically possible.

Further optimization is justified only if Step 12 shows that decode throughput materially limits productive use.

If approximately 9–11 tok/s is adequate in practice, Step 12 should PASS and the runtime should remain frozen.

If decode is a meaningful limitation, route the problem to `ROADMAP.md` with a measured target and current attribution.

The runtime roadmap should then begin from the current production baseline rather than reopening superseded experiments.

Known measured runtime opportunities may inform that work, but they do not authorize it automatically.

### Acceptance

Step 12 is complete when we can answer:

> Is the current decode throughput sufficient for productive use, and if not, how much user-visible latency is actually attributable to decode?

Classify the result as:

* `PASS`
* `CONDITIONAL PASS`
* `FAIL — DECODE THROUGHPUT`

A FAIL should include enough measurement to define the next bounded runtime problem.

### Report

```
service-progress/step-12-decode-throughput.md
```

Save machine-readable results under:

```
benchmarks/results/service-step-12/
```

### Exit

If decode throughput is adequate, stop.

If decode throughput is inadequate, hand the measured problem to `ROADMAP.md` for bounded inference-runtime optimization.

Do not create additional service steps merely because further optimization opportunities exist.

---

## 13. Context Capacity Qualification and Production Promotion

**Status: COMPLETE — 13A PASS; 13B PASS: 256K FEASIBLE; 13C: 256K SELECTED; 13D PASS; 13E PASS; 13F PASS; 13G PASS (2026-09-07). Final classification: PASS — 256K PROMOTED TO PRODUCTION.**
13A: 128K reproduced on the current runtime under the single-llama-server
swap protocol (owner order 10:20:18): manual server healthy at n_ctx=131072
(KV 1008 MiB, KDA recurrent 42.81 MiB, expert cache 8192 MiB zerocopy armed,
Metal OK on Apple M5); 95,004-token prompt admitted; needle at ~85.5K-token
position retrieved exactly; prefill 33.95 tok/s; decode 5.35 tok/s; peak RSS
11.80 GiB. 13B (owner order 11:40): 256K probe completed — Stage 1 allocation
healthy at n_ctx=262144 across 3 windows (KV 2016 MiB / 262,144 cells / 7
MLA layers, recurrent RS 42.81 MiB, expert cache 8192 MiB zerocopy armed,
Metal OK, short probe ok, steady RSS ~10.4 GiB, host free 25–26%); Stage 2
beyond-128K probe PASS — 145,824-token prompt admitted, needle `NEEDLE-13B-6937`
at prompt position ~137,039 (content token 137,037; ~5,967 tokens beyond
131,072) retrieved exactly, response correct, no truncation, no Metal/runtime
failure, server healthy after; prefill 26.92 tok/s (5,417,943 ms / 145,824
tok); decode 3.61 tok/s at 146K ctx (10 tok / 2,769 ms); long-prompt TTFT ≈
prefill wall 5,420.7 s (~90.3 min); peak RSS 13.35 GiB (12.73 GiB) on the
24 GiB host (21% free after), KV usage at prompt depth 1,121 MiB of 2,016 MiB.
Classification: `256K FEASIBLE` (allocation reliably healthy; useful >128K
operation passes; memory safely within the 24 GB envelope; throughput cost of
depth documented for the 13C owner decision). 64K launchd baseline restored
and verified after each window (llama 200, n_ctx 65536, gw 200, 11B sha
`8baf474684`, FK violations 0). Driver notes: 3 aborted attempts archived
(13b-256k-run1-stage1 false-positive OOM gate; run2 client read-timeout bug;
run3 harness 30-min exec timeout) — none were runtime failures. Evidence under
`benchmarks/results/service-step-13/13a-128k/` and
`benchmarks/results/service-step-13/13b-256k/`; tradeoff data in
`service-progress/step-13-context-capacity.md`.

### 13C Decision — SELECT 256K (owner, 2026-09-07 14:23 MST)

Owner selected **256K (ctx 262144)** as the production target. Rationale:
256K allocates safely (4/4 windows healthy), short-context performance
unchanged (probe-a TTFT 0.012–0.013 s across 64K/128K/256K), the 146K probe
correctly retrieved information beyond 128K (needle `NEEDLE-13B-6937` at
~137K), and peak RSS was 12.73 GiB of 24 GiB (~21% host free). The severe
long-context prefill cost is real (~26.9 tok/s at 146K; ~90 min prefill for a
full-depth prompt) but choosing 128K does not solve it — it only caps usable
context. Next: 13D aligns the production contract (llama-server ctx 262144 +
OpenClaw contextWindow 262144), verifies resolution through the real agent
path, and preserves rollback to the qualified 64K state.

### 13D Status — PASS (2026-09-07 14:40 MST)

Production contract promoted and verified: launchd job ctx 262144 (resolved
n_ctx 262144, probe OK 1.57 s), openclaw.json contextWindow 262144 on both
kimi-linear-48b provider entries (kimi-local + llama-server), gateway hot
reload applied, real-path agent turn resolves `contextTokens 262144
(contextTokensSource: resolved)`, 11B sha 8baf474684 unchanged, FK 0.
Rollback artifacts retained under
`benchmarks/results/service-step-13/13d-256k/rollback-64k/` (64K plists +
procedure; openclaw.json backup `~/.openclaw/openclaw.json.bak-step13d-20260907`).
Driver: `tools/service_step13d.sh`.

### 13E Status — PASS (2026-09-07 17:48 MST)

Real OpenClaw agent qualification at 256K completed 5/5 legs through the
real agent path on the live 262144 production contract (no swap window):
short control, engineering/tool turn, Poliscopic production turn
(agent poliscopic; resolves 262144 despite a stale 65536 agent-store copy),
>65,536 assembled prompt (72,697 tok; needle NEEDLE-13E-6200 at assembled
69,059 retrieved exactly), and >131,072 real-agent prompt (140,708 tok;
needle NEEDLE-13E-2060 at assembled 136,190 retrieved exactly). All legs
resolved contextTokens 262144. Peak llama RSS 10.14 GiB; final verify
llama 200 / n_ctx 262144 / gw 200 / 11B sha 8baf474684 / FK 0. Driver:
`tools/service_step13e.sh`; evidence
`benchmarks/results/service-step-13/13e-256k/`. Stopped at the 13E gate —
13F (regression gates) and 13G (Poliscopic capacity measurement) await
owner go.

### 13F Status — PASS (2026-09-07 19:36 MST)

Bounded regression gates at the 256K contract (roadmap §13F, no full
re-qualification): all required gates PASS — smoke (normal interaction,
reply exactly `OK`), p2 (exact `{"ok": true}`), p5 (bounded tool use, e5
count 5), norm (NORM-ABS multi-step tools rc 0, liveness working),
loop (LOOP-STRICT identical-read x25 — detector FIRED, not a client
timeout), storage (11B byte-level: benign 40+ alnum-run path stored
verbatim, labeled AWS secret still masked). Every gate resolved ctx
262144. Final verify llama 200 / n_ctx 262144 / gw 200 / 11B sha
8baf474684 / FK 0 / pid 7021 constant / no config drift vs the 13D
snapshot. Driver: `tools/service_step13f.sh`; evidence
`benchmarks/results/service-step-13/13f-256k/` (pass-1 driver-bug and
pass-2 client-cap evidence archived with notes). Stopped at the 13F gate —
13G (Poliscopic capacity measurement) awaits owner go.

### 13G Status — PASS (2026-09-07 20:33 MST)

Poliscopic capacity measurement on the live 262144 contract (owner go
20:00:29 MST, exact read-only maintenance task): measured turn through the
real agent path (agent poliscopic, model forced
llama-server/kimi-linear-48b by the retained runner → rides the live 256K
contract) completed rc 0, wall 1410.7 s (23.5 min), not a client timeout,
liveness working, no fallback. contextTokens **262144 resolved**;
assembled promptTokens 47,626 at deepest call (slot n_prompt 48,054,
truncated 0); available remaining ≈ 214,090 tokens; 0 compaction events.
Fixed/bootstrap material ≈ 116K chars (system 41,770 + project 24,942 +
tools 44,651 + skills 4,995) — ~45% of a 64K budget vs ample headroom at
262144: material working-context improvement over the former 64K baseline
measured, not assumed. The agent produced a 2,151-char KG maintenance
assessment with real counts + integrity indicators + next-priority
recommendation (provenance resolution); read-only compliance is objective
(poliscopic git status identical before/after, KG sqlite sha identical,
file inventory identical, no new files). Watchdog peak llama RSS 9.85 GiB;
final verify llama 200 / n_ctx 262144 / gw 200 / 11B sha 8baf474684 / FK 0
(kimi + poliscopic) / pids constant / zero config drift.

**Step 13 closed as PASS — 256K PROMOTED TO PRODUCTION** (acceptance items
1–12 all demonstrated with recorded evidence across 13A–13G). 262144 is
frozen as the production baseline. STOP at the Post-Step-13 gate per the
Exit section — no Step 14 or listed post-Step-13 work without separate
authorization. Driver: `tools/service_step13g.sh`; evidence
`benchmarks/results/service-step-13/13g-poliscopic-capacity/`.

### Purpose

Determine whether **128K or 256K** is the appropriate production context
window for Kimi Linear, then promote and qualify only the selected target
through the real OpenClaw agent path.

This is a **context-capacity qualification step**, not a prefill, decode,
streaming, or general performance-optimization step.

Current state:

* **64K:** current production-qualified OpenClaw baseline.
* **128K:** technically validated previously, but never promoted through the final production OpenClaw qualification path.
* **256K:** not yet attempted.
* Real Poliscopic use now provides a concrete workload where additional context capacity may be valuable.

The existing 64K production service remains the rollback baseline.

### Existing 128K Evidence

Prior testing established that Kimi Linear can operate correctly with a 131,072-token context allocation:

* 92,344 actual prompt tokens;
* exact long-context needle retrieval PASS;
* approximately 20.74 tok/s prefill;
* approximately 1008 MiB KV;
* approximately 7.83 GB peak RSS;
* server remained healthy after the long-context run.

Later 128K attempts encountered Metal OOM under different host/runtime conditions. Therefore 128K must first be reproduced with the current runtime before any production promotion.

### Questions

Answer, in order:

1. Can the current Kimi Linear runtime reproducibly operate at 128K?
2. Can it safely allocate and use a 256K context?
3. Does 256K impose unacceptable memory or normal-operation costs relative to 128K?
4. Based on measured evidence, should production use 128K or 256K?
5. Can the selected target then pass through the real OpenClaw production agent path without regressing the qualified behavior from Steps 9–12?

Do not assume that the largest context that starts successfully is automatically the correct production target.

---

### Baseline

Before testing, record the current qualified 64K production state.

At minimum record:

* OpenClaw effective context configuration;
* llama-server context configuration;
* model/runtime configuration;
* expert-cache configuration;
* relevant Metal/runtime settings;
* active Step 11B patch pathname and SHA;
* gateway and llama-server PIDs and health;
* host memory state;
* `PRAGMA foreign_key_check`;
* current Poliscopic `/context detail`.

Preserve the current 64K configuration as a known-good rollback target.

Do not modify unrelated OpenClaw, model, plugin, plist, agent, or database state.

---

## 13A. Reproduce 128K

Reproduce the previously validated 128K capability using the current Kimi Linear runtime under controlled conditions.

Use:

```text
context = 131072
```

Do not change OpenClaw production context yet.

Verify:

1. llama-server allocates and starts successfully;
2. Metal allocation succeeds without OOM;
3. short inference remains correct;
4. a substantial long-context prompt is admitted;
5. useful retrieval/correctness is demonstrated beyond 64K;
6. server health survives the run;
7. host memory remains within a safe operating envelope.

Prefer reproducing the previous Stage 8 conditions closely enough to make the comparison meaningful.

Record:

* configured context;
* actual prompt tokens;
* KV allocation;
* steady and peak RSS;
* expert-cache configuration;
* relevant Metal allocations;
* prefill tok/s;
* TTFT;
* decode tok/s;
* total wall time;
* retrieval/correctness result.

### 13A Gate

If 128K cannot be reproduced, STOP.

Classify the failure boundary before changing anything else.

Do not proceed to 256K and do not begin memory or prefill optimization.

If 128K passes, proceed to 13B.

---

## 13B. Probe 256K Feasibility

Test whether the current runtime can safely provide:

```text
context = 262144
```

This is initially a **technical feasibility test**, not a production promotion.

### Stage 1 — Allocation

Start llama-server at 256K and verify:

* context allocation succeeds;
* Metal initialization succeeds;
* no OOM occurs;
* server becomes healthy;
* short inference works;
* memory headroom remains acceptable.

Record the same memory/runtime metrics used for 128K.

If allocation itself is unsafe or unstable, STOP 256K testing and retain 128K as the production candidate.

### Stage 2 — Beyond-128K Use

If allocation passes, exercise an actual prompt beyond the previously validated 128K range.

Target approximately **140K–160K actual prompt tokens** initially. Do not fill the entire 256K window merely to prove that it exists.

The prompt must contain a deterministic retrieval target or equivalent correctness check located beyond the former 128K boundary.

Verify:

* prompt admitted successfully;
* information beyond 128K is retrievable;
* response is correct;
* no context truncation occurs;
* no Metal/runtime failure occurs;
* server remains healthy afterward.

Record:

* actual prompt tokens;
* KV usage;
* peak RSS;
* prefill tok/s;
* TTFT;
* decode tok/s;
* total wall time;
* retrieval result;
* server health.

If practical, compare memory behavior against the 128K run under otherwise equivalent conditions.

### 13B Gate

Classify 256K as one of:

* `256K FEASIBLE`
* `256K FEASIBLE WITH OPERATING LIMIT`
* `256K NOT PRACTICAL`

A 256K configuration is not considered feasible merely because llama-server accepts `262144`. It must demonstrate useful operation beyond 128K.

---

## 13C. Select the Production Context Target

Choose **128K or 256K** using the evidence from 13A and 13B.

Consider:

* allocation stability;
* peak and steady memory;
* KV cost;
* expert-cache/runtime headroom;
* impact on normal short-context operation;
* long-context correctness;
* prefill cost;
* operational stability;
* usefulness for real OpenClaw workloads, particularly Poliscopic.

### Select 128K if:

* 256K fails allocation or useful >128K operation;
* 256K materially compromises memory safety;
* 256K materially degrades normal operation;
* or 256K provides insufficient practical benefit to justify its operating cost.

### Select 256K if:

* allocation is reliably healthy;
* useful >128K operation passes;
* memory remains safely within the 24 GB host envelope;
* normal short-context operation is not materially degraded;
* and the additional capacity provides a reasonable production benefit.

Do **not** select 256K solely because it is technically possible.

Write the target-selection decision and evidence before modifying the OpenClaw production context contract.

---

## 13D. Align the OpenClaw Production Context Contract

Promote only the selected target.

For 128K:

```text
llama-server context:           131072
OpenClaw model contextWindow:   131072
```

For 256K:

```text
llama-server context:           262144
OpenClaw model contextWindow:   262144
```

Identify every effective OpenClaw setting that can constrain usable context.

Verify that no client, provider, model, agent, admission, compaction, or token-budget setting silently retains the former 32K or 64K ceiling.

Do not rely only on configuration text. Verify the **resolved runtime value through the real agent path**.

Preserve all unrelated production configuration.

---

## 13E. Real OpenClaw Agent Qualification

Qualify the selected target through OpenClaw.

Use at minimum:

1. **Short control** — ordinary short interaction.
2. **Engineering/tool turn** — representative tool-using maintenance/coding work.
3. **Poliscopic turn** — real production bootstrap and current Poliscopic tool allowlist.
4. **Beyond-64K agent turn** — assembled OpenClaw prompt exceeding 65,536 tokens.
5. If **256K** was selected, include a real-agent prompt exceeding 131,072 tokens.

The long-context legs must demonstrate useful retrieval or task completion using information beyond the former boundary.

A nominal configuration value is not sufficient.

For each leg record:

* resolved context capacity;
* assembled prompt tokens;
* compaction behavior;
* TTFT;
* prefill duration/tok/s where available;
* decode duration/tok/s;
* total wall time;
* tool behavior;
* final-answer correctness;
* gateway health;
* llama-server health;
* memory behavior where practical.

---

## 13F. Regression Gates

The context promotion must preserve the production properties established through Steps 9–12.

Verify:

* normal agent interaction remains functional;
* tool calling remains functional;
* no new exact-format/instruction-following regression appears;
* Step 11B redaction/storage behavior remains intact;
* `PRAGMA foreign_key_check` remains empty;
* loop detection remains functional;
* no new runaway-loop behavior appears;
* gateway remains healthy;
* llama-server remains healthy;
* no unrelated configuration or agent state changes.

Do not rerun the entire historical qualification suite unless evidence indicates a regression.

Use bounded controls sufficient to establish that changing context capacity did not invalidate the qualified production path.

---

## 13G. Poliscopic Capacity Measurement

After production promotion, capture a fresh Poliscopic:

```text
/context detail
```

Record:

* system-prompt tokens;
* tool-schema tokens;
* fixed/bootstrap context;
* available context under the selected production window;
* compaction threshold/behavior observed during representative work.

Run a representative Poliscopic maintenance task and determine whether the larger context materially improves useful working-context retention relative to the former 64K baseline.

This is a **capacity measurement**, not a prefill optimization exercise.

Slow prefill alone is not a Step 13 failure unless it makes the selected context operationally unusable.

---

## Acceptance

Step 13 passes when:

1. 128K has been reproduced successfully;
2. 256K has been explicitly tested and classified;
3. 128K or 256K has been selected using recorded evidence;
4. OpenClaw resolves Kimi's effective context to the selected target;
5. a real OpenClaw prompt exceeding 65,536 tokens succeeds;
6. if 256K is selected, a real OpenClaw prompt exceeding 131,072 tokens succeeds;
7. useful retrieval/task behavior is demonstrated beyond the relevant former boundary;
8. representative normal and tool-using turns remain functional;
9. Step 11B redaction/storage behavior remains intact;
10. SQLite foreign-key integrity remains clean;
11. host memory remains within a safe operating envelope;
12. the selected context can remain as the production baseline without unrelated runtime changes.

Final classification:

```text
PASS — 128K PROMOTED TO PRODUCTION
```

or:

```text
PASS — 256K PROMOTED TO PRODUCTION
```

If appropriate:

```text
CONDITIONAL PASS — <128K|256K> PRODUCTION WITH DOCUMENTED OPERATING LIMIT
```

Otherwise:

```text
FAIL — CONTEXT PROMOTION
```

Any failure must identify the measured boundary:

* host memory / Metal allocation;
* llama-server;
* OpenClaw context/admission;
* compaction/token budgeting;
* correctness;
* agent behavior;
* or another demonstrated cause.

---

## Rollback

If production promotion fails, restore the qualified 64K baseline.

Verify after rollback:

* effective context = 65,536;
* gateway health;
* llama-server health;
* Step 11B patch SHA unchanged;
* `PRAGMA foreign_key_check` returns zero rows.

Do not leave production in an intermediate context configuration.

---

## Evidence

Write the report:

```text
service-progress/step-13-context-capacity.md
```

Store machine-readable evidence under:

```text
benchmarks/results/service-step-13/
```

Update this roadmap with:

* 128K result;
* 256K result;
* production-target decision;
* final qualification;
* resulting production configuration.

---

## Exit

If Step 13 passes, freeze the selected context as the new Kimi Linear production baseline.

STOP at the Post-Step-13 gate.

Do not begin:

* prefill optimization;
* decode optimization;
* multi-agent concurrency;
* Mistral streaming;
* 512K/1M context testing;
* or unrelated runtime work.

Those require separate authorization.

## 14. Qualify Incremental Prefix and State Reuse

### Goal

Determine how effectively the current production Kimi/OpenClaw stack reuses existing prompt, KV, and recurrent state across successive agent turns, and whether avoidable re-prefill is a material source of latency.

The primary question is:

> When an agent already has a large accumulated context and adds a small amount of new work, how much of the existing state is actually reused instead of recomputed?

Step 13 established that 262144 context capacity is production-qualified. Do not reopen context-capacity work in Step 14.

This step is about incremental reuse and latency, not larger context windows.

### Baseline

Freeze the current production state established at the end of Step 13:

- llama-server context = 262144
- OpenClaw contextWindow = 262144
- current Kimi Linear MXFP4 Metal runtime
- 8 GiB expert cache
- 4 expert-read workers
- current loop-detection configuration
- current Step 11B redaction fix
- no model, sampler, prompt, tool, context, or agent changes unless explicitly authorized by a later Step 14 remediation substep

The Step 13G Poliscopic measurement is the primary real-workload reference:
- deepest assembled context ~47.6K tokens
- substantial cacheRead already observed
- final call reused almost the entire prompt
- total task wall ~23.5 min

### 14A. Measure Existing Reuse Behavior

Do not change production behavior.

Characterize the reuse that already exists through the real OpenClaw agent path.

Use controlled consecutive turns in the same agent/session and, where useful, fresh-session controls against the same live llama-server PID.

At minimum test:

1. small established context + small suffix
2. representative medium accumulated context + small suffix
3. large accumulated context + small suffix
4. a repeated/stable-tool-schema agent turn representative of Poliscopic or engineering work
5. a fresh-session comparison where appropriate

For each turn record:

- total assembled prompt tokens
- longest common prefix with the immediately preceding model request where measurable
- cacheRead / cached_tokens
- newly evaluated prompt tokens
- prompt-eval duration
- prompt-eval tok/s
- TTFT
- decode duration / tok/s
- total wall time
- server PID
- slot identity/state where observable
- compaction events
- whether any tool/schema/bootstrap/session material changed
- whether reuse succeeded, partially succeeded, or missed entirely

Determine empirically:

- when prefix reuse works
- how much is reused
- when it breaks
- whether breaks correlate with prompt serialization changes, tool ordering, session metadata, compaction, slot replacement, server PID change, or another measured factor
- whether KDA recurrent state imposes any special reuse boundary beyond ordinary prompt/KV reuse

Do not patch llama.cpp or OpenClaw in 14A.

### 14A Acceptance

14A is complete when the evidence answers:

1. What reuse mechanism is operating today?
2. What fraction of a stable accumulated prompt is normally reused?
3. What causes reuse misses?
4. How much latency is attributable to newly evaluated suffix versus unnecessary re-prefill?
5. Is there a material optimization problem worth opening?

Classify:

- PASS — existing reuse is understood and generally effective
- PARTIAL — reuse is observed but important miss conditions remain unexplained
- FAIL — current evidence cannot reliably characterize reuse

Write:
service-progress/step-14a-existing-prefix-reuse.md

Save machine-readable evidence under:
benchmarks/results/service-step-14/14a/

STOP after 14A and report results. Do not begin 14B or make production changes without explicit authorization.

### 14A Status — PASS (2026-09-07 21:53 MST)

Measurement-only pass — existing reuse characterized through the real agent path on the live 262144 contract (llama pid 7021 constant, FK 0, 11B sha 8baf474684, zero config drift; no production changes, no patches). Same-session consecutive turns reuse 99.2–99.9% of the established prompt (13.5K ctx: 99.3%; 18.8K: 99.8%; 48.1K: 99.9%) —
llama.cpp slot KV cache with LCP-based prefix reuse is the operating
mechanism; OpenClaw re-sends the full assembled prompt and llama
re-evaluates only the delta (legC: 44 new tokens of 48,106 assembled,
wall 11.4 s vs ~27–30 min cold re-prefill of the same 48K). No
compaction, no truncation, no KDA-specific reuse boundary observed.
Misses: fresh-session serialization + user content (legD control) and
new tool/user deltas by design; legA (small-pair driver duplicate) hit
the 900 s client cap on verbose multi-round agent turns at 256K — an
agent-behavior/budget artifact, not a reuse failure (per-call reuse
0.94–0.999; clean small-context record is the probe pair). Verdict:
no material avoidable same-session re-prefill found; §14B should
characterize the server-side mechanism (slot/KV pool, LCP threshold,
cacheRead accounting, KDA state) per the roadmap. Report:
service-progress/step-14a-existing-prefix-reuse.md; evidence:
benchmarks/results/service-step-14/14a/. STOPPED at the 14A gate — 14B
requires separate authorization.

### Planned later substeps

Do not execute these yet.

#### 14B. Characterize Server-Side Reuse Mechanism
Inspect and document llama-server/OpenClaw slot reuse, KV reuse, KDA recurrent-state reuse, invalidation boundaries, and persistence semantics where 14A shows uncertainty.

### 14B Status — PASS (2026-09-08 12:00 MST)

Read-only characterization against the exact running llama.cpp commit
(a895f6826, build 10447, verified == live runtime/live/bin) + live
/props,/slots. Mechanism fully traced end to end:
- llama-server keeps each slot's last prompt tokens + KV/state;
  per-request reuse is `n_past = get_common_prefix(input)` with
  `cache_prompt` default true (server-context.cpp:3112) — this is the
  99.2–99.9% same-session reuse 14A measured. Slot selection by LCP
  similarity (threshold 0.1) with RAM prompt-cache save/load when
  `f_keep < 0.5`.
- Kimi-Linear is a HYBRID arch (llama-arch.cpp:977): attention layers
  in a KV cache + KDA recurrent layers in llama_memory_recurrent (F32
  state, n_rs_seq rollback). Prefix extensions need no checkpoint
  restore → no KDA-specific boundary on the common suffix pattern;
  mid-context divergence restores a saved context checkpoint
  (min-step 8192, max 32) or forces full re-process (do_reset).
- Accounting: OpenAI-compat
  `prompt_tokens_details.cached_tokens` = per-task `n_past`;
  OpenClaw maps it to usage.cacheRead, input = prompt − cached
  (usage-BpC2Ujh-.mjs:56). Explains 14A records exactly (legC input
  48 / cacheRead 48,054).
- /slots `n_prompt_tokens_cache:0` after tasks is stats cleared on
  release/reset (stats={}, server-context.cpp:351) — retained prompt+
  KV persist; next request's cacheRead reports the reuse.
- Persistence: in-memory slot retention across requests + optional
  --slot-save-path (runtime/state/slot-cache) /slots save/load.
Verdict: no avoidable same-session miss exists to "stabilize"; 14C has
no demonstrated target. Next useful substeps: 14D (cross-session /
persistent reuse) or 14E (production qualification), on authorization.
Report: service-progress/step-14b-server-reuse-mechanism.md; evidence:
benchmarks/results/service-step-14/14b/evidence-notes.md. STOPPED at
the 14B gate.

#### 14C. Stabilize Automatic Same-Session Reuse
Only if 14A/14B demonstrate avoidable same-session misses. Make the smallest bounded change required to preserve stable prefixes.

### 14C Status — SKIPPED (2026-09-08)

Precondition not met and not attempted. §14C authorizes work only "if
14A/14B demonstrate avoidable same-session misses". 14A measured
99.2–99.9% prefix reuse on consecutive same-session turns through the
real agent path (13.5K/18.8K/48.1K assembled), and 14B traced the
mechanism to llama.cpp's default-on LCP prefix reuse (`cache_prompt`
true, hybrid KDA memory with checkpoint fallback). No avoidable
same-session re-prefill was found at any measured context size, so there
is no demonstrated target for a stabilization change. 14C remains
skipped unless a later step produces contrary evidence.

#### 14D. Cross-Session / Persistent Reuse
Only after same-session reuse is understood and qualified. Determine whether useful state can be reused safely across session boundaries or server lifecycle boundaries.

### 14D Status — PASS (2026-09-08 16:01 MST)

Measurement + read-only characterization (no production changes, no
patches, no gateway/llama restart; llama pid 7021 constant, gw pid
82948, 11B(i) sha b54b13f1d7, FK 0 both DBs, idle gate PASS, zero
config drift vs 13D). Single-slot ping-pong (kimi S1 -> fresh S2 -> S1)
through the real agent path:
- FRESH different session vs warm slot: 97.0% reuse (S2-t1: 15,862 /
  16,348 assembled, wall 15.5 s) — every kimi session shares a ~15.9K
  serialized bootstrap/system/tool prefix, and llama LCP reuse matches
  it automatically; cross-session first turns are nearly free.
- RETURN after eviction: S1-t3 first task reuse 57.2% (15,885 cached /
  27,760 assembled; 11,875 session-unique tokens re-prefilled, ~5 min
  @39 tok/s), recovering to 92-98% within the turn. Cause: in-RAM
  prompt cache (cache_ram_mib 8192) saves an evicted slot prompt only
  when f_keep = LCP/slot_len < 0.5; S2 shared the ~16K bootstrap with
  S1's 27.7K -> f_keep 0.573 -> no save -> unique tail re-prefilled.
  Boundary: same-agent accumulated context below ~2x bootstrap (~32K)
  is NOT cached across interleave; larger contexts (13G poliscopic
  48K) and cross-agent interleaves drop below 0.5, ARE RAM-cached and
  restore at ~99.9% (explains 14A legC 48,054/48,107 exactly).
- Safety PASS: token-prefix-only reuse; S1 content absent from S2
  transcript and vice versa; FK 0; no compaction.
- Lifecycle: gateway restart preserves llama slot/RAM-cache state (pid
  7021 constant across both 11B(i) gateway kickstarts); llama restart
  does NOT (nothing auto-saves; --slot-save-path is manual /slots
  save/load only) — a llama restart forces cold re-prefill of every
  active session (~48K ≈ 27-30 min at 13G scale).
- Artifacts: S1-t3/S1-t4 hit the 900 s client cap (multi-round verbose
  agent at 256K, same class as 14A legA/E; per-call reuse 0.92-0.98).
Report: service-progress/step-14d-cross-session-reuse.md; evidence:
benchmarks/results/service-step-14/14d/ (driver tools/service_step14d.sh).
STOPPED at the 14D gate — 14E (production qualification vs Step 13G)
requires separate authorization.

#### 14E. Production Qualification
Repeat a representative real agent workload and compare against Step 13G. Measure reduction in newly evaluated prompt tokens, TTFT, and total wall time.

The decisive benchmark for Step 14 should include a large accumulated context plus a small suffix, ideally approximately:

150K existing context + 1K new tokens

The goal is to determine whether the system evaluates roughly the suffix rather than recomputing the entire accumulated context.

### 14E Status — PASS WITH HARNESS LIMITATION (2026-09-08/09; reclassified 2026-09-10 by owner)

Measurement-only production qualification (no changes, no patches, no
restart; llama pid 7021 + gw pid 82948 constant, 11B(i) sha
b54b13f1d7, FK 0, idle gate PASS). Continued the retained 13G
poliscopic session and grew it through real workspace-doc reads:
B2-B12 built 67K -> 121.7K assembled (each leg evaluated only its newly
read file content against a warm cached prefix; 0 compaction).

**PASS.** The production qualification demonstrated the behavior Step 14
was intended to establish: at B12 the real OpenClaw agent held 121,737
assembled tokens, reused 121,148 (99.5%), evaluated only 589 new tokens
(0.48%), and completed in 52.1 s. That proves suffix-only incremental
evaluation at large production context (vs 13G cold 47.6K / 46,780 new /
1,410.7 s; same class as 14A legC at 48K).

**Harness limitation (not a llama-server failure).** The planned ~150K
datapoint was not reached because OpenClaw auto-compacted the
conversation at ~122K down to ~49K during B13 (gateway 22:56:20:
"auto-compaction succeeded for llama-server/kimi-linear-48b; retrying
prompt"; policy keepRecentTokens 50000). B14/S1 then ran on the
compacted session; S1 re-measured suffix-only at 49K (749 new / 48,486
cached / 98.5% / 38.3 s). This is an **OpenClaw harness/compaction
boundary, not a llama-server prefix/state-reuse failure** — OpenClaw
caps one session's accumulated context near ~122K on this stack, below
llama's 262144 window. Per owner direction, compaction was NOT disabled
and 14E was NOT rerun to manufacture the 150K point.
- Evidence: auto-compaction-evidence.md (gateway line + config),
  analysis.json, per-leg windows.
Report: service-progress/step-14e-production-qualification.md; driver
tools/service_step14e.sh.

Step 14 reuse record (14A-14E): same-session warm suffix-only evaluation
confirmed 13.5K -> 48K -> 121.7K assembled; cross-session
shared-bootstrap reuse 97% + f_keep<0.5 RAM-cache boundary in 14D;
gateway restart preserves llama slot state, llama restart forces cold
re-prefill (14B/14D). No expansion into 512K/1M, decode optimization,
model/sampler changes, multi-agent concurrency, or Mistral/K3 work.

#### 14D(i). Persistent Slot Save/Restore Characterization (authorized 2026-09-10)

### Purpose

Determine whether llama-server inference state can persist
independently of OpenClaw's conversation lifecycle. The architectural
distinction to preserve:

**The harness constructs the token sequence; llama-server owns the
evaluated inference state.**

OpenClaw/Hermes/Pi may therefore require different prefills when their
stable serialized prefixes/tool catalogs differ, but the lifetime of a
completed llama-server prefill or saved slot state should not
conceptually depend on an OpenClaw agent-turn timeout or compaction
cycle. 14E exposed the harness boundary (OpenClaw auto-compaction at
~122K); 14D(i) asks whether the inference-state side can be made
durable across the server's own lifecycle.

### Scope (measurement / characterization only)

Keep narrow and initially single-slot (the production server is
`--parallel 1`). Using llama-server's native slot save/load and the
configured `--slot-save-path`:

1. build a known warm session/state;
2. save it; record token depth, snapshot bytes, save time, throughput;
3. evict/erase the resident state;
4. restore the snapshot;
5. continue with a small suffix; verify cached_tokens/cacheRead and
   correctness show actual state continuation, not cold re-prefill;
6. compare restore time against equivalent cold-prefill cost;
7. verify save/restore across a controlled llama-server restart;
8. characterize snapshot-size scaling at ~48K, ~100K, ~150K where
   practical;
9. include an external-SSD path test if one is available;
10. verify invalid/incompatible state cannot silently be restored under
    the wrong model/runtime/context identity.

### Explicitly out of scope

No multiple simultaneous inference; no OpenClaw compaction change; no
model/sampler/context changes; no automatic RAM<->SSD tiering. Do not
begin a new session-tiering/orchestration implementation — that belongs
in a subsequent stage, after snapshot size, save latency, restore
latency, and correctness are known.

### Method (planned)

Synthetic deterministic prefix built via `/tokenize` to exact depths;
real llama completion calls at temperature 0; `/slots` for depth and
cache state; `POST /slots/0?action=save|restore|erase` with JSON
`{"filename":...}`; snapshot files under the configured
`--slot-save-path` (`runtime/state/slot-cache`). Correctness =
byte-identical greedy continuation after restore vs after cold
re-prefill of the same prefix. Restart test = `launchctl kickstart -k`
the production llama job, verify health, confirm the slot is empty,
restore from file, verify depth and continuation.

### Stop point

14D(i) is measurement/characterization only. Produce the design,
execute it, record evidence under the Step 14 evidence tree, update this
roadmap, and **STOP at the 14D(i) gate for review.**

### Exit

Step 14 must not expand into:
- 512K/1M context testing
- decode optimization
- model changes
- sampler changes
- multi-agent concurrency
- Mistral/K3 work
- unrelated OpenClaw cleanup

Any remediation requires a measured reuse failure and explicit authorization.

After adding this Step 14 definition to SERVICE-ROADMAP.md, execute 14A only.

## Post-Step-12 Decision

Steps 9–12 constitute production usability qualification of the Kimi OpenClaw agent:

```
Step 9  — Is it good enough? (response quality; 9A–9D remediation; close-out 2026-09-04)
    ↓
Step 10 — Why does the real agent path degrade exact-format compliance? (localization; active 2026-09-04)
    ↓
Step 10A — Does it start responding quickly enough? (TTFT; deferred from former Step 10)
    ↓
Step 11 — Can it use enough context?
    ↓
Step 12 — Does it generate quickly enough?
```

After Step 12, summarize the production state as:

* response quality;
* cold / prefilled / warm TTFT;
* practical usable context;
* decode throughput;
* total-turn behavior;
* known limitations.

The service is production-qualified when those measurements demonstrate that the agent is useful for its intended work.

Further engineering must be justified by a measured limitation rather than by the existence of another possible optimization.

#### Completed outside this plan (2026-08-17)

Per explicit operator task, a Qwen3-8B interactive/manager tier was wired
through the same modified llama-server stack (port 18082), reusing the Stage 6
warm-state/prefill machinery; completed, measured, and committed without
changing Kimi configuration or Stage 8 artifacts. This does not expand this
plan's scope (caveman-only). See `progress/qwen-manager-tier-report.md`
(commit `00f07d0`).

---

## Working Rules

- Do not change other agents.
- Do not merge this work with `ROADMAP.md`.
- Do not optimize inference before proving the caveman failure mode.
- Do not rely on raw model tok/s as proof of agent usability.
- Do not treat a single successful request as solved.
- Do not widen scope until caveman is stable.

---

## Deliverables

When the plan is complete, produce:

1. The caveman-only service configuration.
2. The cross-session bootstrap reuse measurement.
3. The restart-boundary characterization, including the unsupported slot/state restore result if that remains the outcome.
4. The explicit prefill contract, warm-state registry, and CLI/observability behavior.
5. The completed Stage 5C runtime-stability characterization.
6. A short note stating whether bootstrap state is session-local, restart-invalidated, or both.
7. The mechanism-verification result, including which positional behavior is actually native/reference for Kimi Linear.
8. The measured context-window expansion result, including which strategy was used and what size was actually sustainable.

---

---

## Decision Rule

- If Caveman bootstrap reuse is session-local only: document the boundary and stop there.
- If restart durability works: keep the experiment narrow and move on to the post-prefill failure as its own issue.
- If the usable context window can be expanded: document the exact strategy, the measured limit, and the memory cost before calling the experiment complete.
- Do not expand rollout until the Caveman path is boringly reliable.
