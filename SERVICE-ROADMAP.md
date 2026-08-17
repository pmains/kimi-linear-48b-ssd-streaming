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
7. The usable context window has been expanded to a measured target, with `128k` treated as the first major milestone and `256k` only as an aspirational upper target if it proves practical.

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

Current implementation note (2026-08-16):

- The registry is implemented as a single JSON store at
  `runtime/state/warm-state.json` managed by `tools/warm_state.py`.
- Registry key: `(agent_id, model_id, bootstrap_fingerprint)`; entry fields:
  `status`, `server_pid`, `cached_tokens`, `warmed_at`, `updated_at`, and a
  bounded transition `history` (last 20 transitions with timestamps and
  reasons).
- The bootstrap fingerprint is sha256 over the canonical ordinary-request
  bootstrap body: system prompt + tool declarations + `tool_choice` from
  `dev-openclaw/state/stage6a4-request-body.json` (the captured exact Caveman
  request shape). If that source changes, the fingerprint changes and any
  READY entry is marked STALE.
- The probe discovers the live `llama-server` PID (pidfile, then pgrep of the
  frozen live bundle) read-only and applies the state machine:
  - READY -> COLD when the live PID differs from the recorded `server_pid`
    or no server is running;
  - READY -> STALE when the current bootstrap fingerprint differs;
  - PREFILLING -> FAILED if the prefill target server died;
  - FAILED -> PREFILLING on explicit retry (`begin-prefill`).
- CLI: `status`, `probe`, `begin-prefill`, `ready --pid --cached-tokens`,
  `fail`, `reset`, `fingerprint`; `--json` for machine-readable output;
  `--registry` to operate on an alternate store.
- Verified on 2026-08-16 against live PID 64991 (scratch store):
  COLD->PREFILLING->READY, READY stable on same PID/fingerprint,
  READY->COLD on PID change (12345 -> 64991), READY->STALE on fingerprint
  change, PREFILLING->FAILED, FAILED->PREFILLING on retry.
- Seeded store state: `(caveman, kimi-linear-48b, <fp>)` = COLD with
  `live_pid` 64991 observed. Nothing was restarted; the live server was not
  touched. Acceptance of the full prefill loop remains Stage 6D.

#### 6C. CLI and observability

Implement:

    openclaw prefill caveman kimi-linear-48b
    openclaw prefill caveman
    openclaw prefill status
    openclaw prefill status caveman
    openclaw prefill caveman kimi-linear-48b --json

The command itself must not be subject to the normal agent stuck-session
watchdog. A long prefill is legitimate work, not a stalled conversational turn.

Progress should come from actual inference progress rather than an elapsed-time
animation:

    PREFILLING 7,782 / 10,240 tokens 76%
    15.8 tok/s · ETA 2m35s · PID 11752

If `llama.cpp` cannot expose exact token progress cleanly, document the best
authoritative signal available rather than fabricating percentages.

The CLI should expose the same underlying operation through a chat command if
desired, but the first implementation only needs the core prefill path, the
state registry, and machine-readable status.

#### 6D. Prove that `/prefill` actually works

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

Stage 6 is complete when OpenClaw can explicitly prefill the exact bootstrap
used by an agent/model pair outside a normal agent turn, accurately track that
warm state against its bootstrap fingerprint and inference-server lifetime,
expose observable CLI progress and status, and prove that a subsequent new
agent session actually reuses the prefilled state.

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

### 8. Expand the usable context window

The next narrow question is separate from durability:

> Can caveman be given a much larger usable context window without breaking the basic agent path or blowing the memory budget?

Test only the verified native/reference mechanism first. Do not make `RoPE`, `YaRN`, and `NoPE` peer experiments unless the verification gate proves they are all actually applicable.

If a non-native mechanism is later shown to participate, document it as a separate follow-up, not as an equal first-round candidate.

Concrete protocol:

1. Capture a baseline run at the current known-good configuration.
2. Use the verified native/reference mechanism and one exact runtime knob set.
3. Run a fixed ladder of context sizes: `32k`, `64k`, `96k`, `128k`, `160k`, `192k`, `256k`.
4. Use the same caveman request shape at every rung.
5. Keep the rest of the agent configuration unchanged.
6. Stop the ladder at the first rung that fails, times out, or pushes memory into an unusable state.
7. Repeat the same ladder for any later, separately validated mechanism only after the native/reference result is documented.

Treat two ceilings separately:

- `runtime ceiling`: crashes, allocation failure, or unacceptable memory pressure
- `useful-context ceiling`: caveman runs, but can no longer reliably retrieve or use the expanded context

Measure at each rung:

- prompt token count at the boundary
- prompt-eval duration
- first-token latency
- resident memory
- runtime failure mode, if any
- useful-context result from a small needle/retrieval test
- whether caveman still completes a normal request at that size

Implementation notes:

- Use the runtime's actual supported long-context knobs, not guessed CLI flags.
- Record the exact knob names and values used for each mechanism.
- Keep the prompt content fixed and extend only the length of the context window input.
- If the runtime exposes a separate `ctx-size`, `rope-scale`, `yarn-*`, or equivalent parameter, record it explicitly.
- If a mechanism is unsupported in the current `llama-server` path, mark it `UNAVAILABLE` and move on instead of inventing behavior.
- Make `128k` the first major success milestone. Treat `256k` as the aspirational upper target if the machine and runtime make it practical.

PASS condition:

- caveman can sustain a measured context target that is materially larger than the current baseline
- the agent path still works at that size
- the chosen strategy is documented with its exact runtime knobs and measured limits
- the useful-context test still passes at the milestone rung

If `256k` is not practical on this machine, record the highest sustainable value and stop there. Do not guess a higher number without evidence.

### 8A. Baseline and compare

Before changing the positional strategy:

- run the current caveman request at the existing context setting
- capture prompt tokens, prefill time, first-token latency, RSS, and log tail
- save this as the comparison baseline for the ladder

### 8B. Evidence to save

For each ladder run, write a short report in `service-progress/` and save the machine-readable outputs under `benchmarks/results/phase-06/` or a sibling directory named for the mechanism.

Each report should include:

- mechanism used
- exact runtime knobs
- largest successful context size
- first failing context size
- runtime ceiling, if reached
- useful-context ceiling, if reached
- memory footprint at the top successful rung
- whether caveman remained usable
- the command used to reproduce the run

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
