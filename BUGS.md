## Investigation — Step 1 findings (2026-08-14, in progress)

### Finding A: exit #1 (17:48, pid 34814) was a GGML_ASSERT abort, NOT spontaneous

Crash report `~/Library/Logs/DiagnosticReports/llama-server-2026-08-14-174801.ips`:

- termination: `SIGNAL / SIGABRT` (abort trap 6), `byProc: llama-server` (self-inflicted)
- faulting thread 0 (main): `update_slots -> decode -> process_ubatch_streamed -> load_layer -> zc_place -> cache_ensure_temp -> ggml_new_tensor_3d -> ggml_new_tensor_impl -> GGML_ASSERT(obj_new) at ggml.c:1813 -> ggml_abort`
- disassembly of the live `libggml-base.0.dylib` at `ggml_new_tensor_impl.cold.5` (0x6f274) confirms: literal `"obj_new"`, `mov w1, #0x715` (= 1813), `bl _ggml_abort`

⇒ `ggml_new_object()` returned NULL inside the streamer's 1 MiB no_alloc temp context
(`cache_ensure_temp`, llama-expert-stream.cpp): the temp-context memory pool ran out.

Per-layer expert tensor shapes are UNIFORM (GGUF: up/gate Q4_K (2304,1024,256) all 26 MoE
layers; down Q6_K/Q4_K (1024,2304,256) alternating runs). ~18 expected recreations do not
obviously exhaust 1 MiB — recreation count vs pool size is under active investigation.

### Finding B: exits #2/#3 (17:59, 18:03) were external SIGTERMs, NOT spontaneous

- Code-path analysis: non-router server reaches `cleaning up before exit` ONLY via
  `start_loop()` returning, which only happens via `queue_tasks.terminate()`, which only
  runs from `shutdown_handler` — which only fires on SIGINT/SIGTERM or the (callerless)
  `llama_server_terminate()`. No internal raise/kill exists in server or streamer code.
- Instrumented dev build confirmed: external SIGTERM while idle yields exactly the
  observed log sequence, plus a backtrace showing the delivery point (idle condvar wait).
- No crash reports exist for #2/#3 (orderly exits don't produce .ips). Timeline matches
  operator stop/restart churn while migrating to the launchd wrapper (17:59-18:13).

### Finding C: "CPU compute buffer size does not match expectation" is benign

Reproduced on a clean external SIGTERM shutdown; it is destructor-time noise in
`~llama_context`, present on every orderly exit. Not a crash cause.

### Finding D: current launchd instance (pid 36532) has 1h+ uptime, zero restarts

launchctl `runs = 1`, `last exit code = (never exited)`, processed 10,240- and 14,336-token
prefills with repeated `failed to allocate loaded ids buffers` warnings but no exit.

The `failed to allocate loaded ids buffers` warning (streamer ids-buf alloc failure) is
NOT yet linked to any exit; it will be characterized separately (Step 3).

### Reproduction — instrumented harness, run 1 (19:17–19:44 + soak, 2026-08-14)

`tools/repro_stability.sh` runs the instrumented dev build on :18081 with the same
runtime config (zerocopy, 4096 MiB, ctx 32768) through idle/short/11k-token-prefill/
repeated battery, restarting on exit. Logs: `/tmp/kimi-stability-18081/`.

**Result: all phases PASS, no exit, no assert, no internal signal. BUG-001's original
failure mode is currently NON-REPRODUCIBLE under this aggressive battery.**

| Phase | Window (local) | Result |
|---|---|---|
| idle-only 900 s | 19:24:06 → 19:39:06 | PASS (survived) |
| short request | 19:39:12 | PASS |
| ~11k-token prefill (45,000 chars) | 19:39:06 → 19:44:08 | PASS — no GGML_ASSERT, no overflow warnings, no signal-handler hits, no aborts |
| repeated requests | 19:44 | 10/10 PASS |
| idle soak | 19:44:18 → (still running, pid 40748 alive) | PASS so far |

Instrumentation observations during run 1 (dev server, pid 40748):

- `[temp] recreate` (temp-context pool re-creation in `cache_ensure_temp`): **215**
  occurrences, log-only, non-fatal (KIMI_STREAM_DEBUG instrumentation).
- `failed to allocate loaded ids buffers` (il=26, n_used=8, n_tokens=0, 0B tensors):
  **19** occurrences, all identical, non-fatal. Still unexplained — kept as a separate
  bug (see Finding D / Step 4).
- First incarnation (19:17:44, pid 40152) received an **external SIGTERM (signal 15)**
  seconds after start and shut down orderly — that is the harness/operator restart,
  NOT a spontaneous exit (instrumented `signal_handler` log confirms delivery point).

**Direction (Pete, 19:5x relay):** the conclusion is NOT "BUG-001 fixed". The original
failure is currently non-reproducible under an instrumented, fairly aggressive test.
Do NOT start changing the corruption/temp-context code without a reproducer — the
hypothesis and all instrumentation/results are preserved here. Continue the soak; if
it stays clean, downgrade BUG-001 per the section below and proceed per the
stability-gate criteria rather than inventing additional corruption fixes.

### Soak closure (20:4x, after reset) — Task 3B stability gate PASS

- Dev harness run 1 (instrumented, :18081, pid 40748): all phases PASS — idle-only
  900 s, short request, ~11k-token prefill (45,000 chars), 10/10 repeated requests,
  then idle soak from 19:44:18. Server still alive at 20:4x (no exit since 19:23;
  the only termination was the intentional harness/operator SIGTERM at 19:17:44).
  Logs: `/tmp/kimi-stability-18081/`.
- Live launchd server (:18080): zero restarts since 18:13:12 (2.5 h+), including
  real agent requests (some aborted client-side mid-prefill — BUG-002 — but the
  server itself stayed healthy). `/tmp/kimi-llama-server.lifecycle.log` shows only
  the single START line.
- Stability gate criteria (>= 1 h idle, repeated realistic requests incl. the
  4,873-token prompt, zero launchd restarts): MET.
- Status remains: BUG-001 downgraded / non-reproducible; reopen at Critical only
  with a fresh reproducer + captured evidence. Task 4 remains gated on Task 3C
  (long-prefill transport survival) per the service roadmap update.

### Build comparison — instrumented dev vs 17:48 live binary (Pete direction #2)

Checked 20:0x local:

- Dev git HEAD == live frozen COMMIT (`e8baeb16e`, phase-07). The dev tree has TWO
  uncommitted files vs that commit, both instrumentation-only:
  - `tools/server/server.cpp` — signal-handler exit-cause + backtrace logging;
  - `src/llama-expert-stream.cpp` — `[temp] recreate` counter + expanded
    `failed to allocate loaded ids buffers` context (gated by KIMI_STREAM_DEBUG).
  Neither changes behavior when KIMI_STREAM_DEBUG is unset (server.cpp only adds
  log lines on signals).
- Both binaries are thin Mach-O executables linking the real implementation in
  dylibs (`libllama-server-impl`, `libllama`, `libggml*`); shasum differs (as
  expected for a rebuild at 19:23 vs freeze at 17:35), and the instrumentation
  strings live in the dylibs, not the exe.
- Runtime invocation is functionally identical: same GGUF
  (`models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`),
  `-ngl 0 --no-mmap --ctx-size 32768 --parallel 1`, KIMI_STREAM_EXPERTS=naive,
  KIMI_EXPERT_CACHE_MB=4096, KIMI_EXPERT_CACHE_MODE=zerocopy (confirmed in the
  launchd wrapper env). Only differences: port (18081 vs 18080) and absolute vs
  relative model path.
- **Conclusion:** nothing changed through rebuilding/config that could explain the
  improved stability — the only delta is log-only instrumentation. The earlier
  exits (17:48/17:59/18:03) and today's clean harness run are both consistent with
  the same binary lineage; the environment (load, memory pressure, operator churn)
  remains the most plausible uncontrolled variable.

---

## BUG-001: llama-server termination under expert-streaming runtime

**Severity:** Closed (fixed 2026-08-16, llama.cpp `0a6b2df63`)
**Status:** Closed — the historical `cache_ensure_temp` GGML_ASSERT under
long prefill is characterized and fixed (grow-only temp-context pool
recycle-on-overflow); the live server's two observed SIGABRTs (exit 134)
from this defect are covered by the promoted fix. The separate
`failed to allocate loaded ids buffers (il=26 ...)` benign warning remains
open as a minor follow-up.
**First observed:** 2026-08-14

### Closure evidence (2026-08-16)

- Root cause: `cache_ensure_temp`'s no_alloc temp context is grow-only;
  ggml never reclaims tensor objects, and the down expert tensor alternates
  Q6_K/Q4_K 14x per layer pass, so a diverse prompt creates ~15 permanent
  objects per step. Any fixed pool size eventually overflows:
  - plain 1 MiB: abort on the 2850th object (needed 1048800, available
    1048576)
  - 1 MiB + one measured tensor (frozen bundle): abort on the 2851st
    object (needed 1049168, available 1048944 — 224 B short) in Stage 6A.6's
    22,241-token prefill
- Fix: recycle the pool when it cannot fit another object (free per-kind
  buffers + `ggml_reset`). Full characterization, unit test, and regression:
  `progress/stage-06a6-temp-ctx-pool-fix-report.md`.
- Regression: two requests on one dev PID created 5,401 temp objects (old
  binary aborts at 2,850); one recycle fired at the boundary; zero aborts.
- Stage 6A.6 rerun (22,409-token prefill + followup on the fixed binary):
  PASS with `cacheRead=22410`; zero aborts. Live runtime promoted
  (`runtime/live/COMMIT` = `0a6b2df63`).

### Original record (historical)

### Symptom

`llama-server` (custom build, SSD-streamed MoE experts) exits on its own while idle —
no request in flight, no crash signature. Final log lines:

```
llama_expert_streamer: failed to allocate loaded ids buffers
...
I srv operator(): operator(): cleaning up before exit...
W ~llama_context: CPU compute buffer size of 84.0000 MiB, does not match expectation of 0.0000 MiB
```

`cleaning up before exit` indicates an **orderly shutdown path**, not a segfault or
OOM-kill. The trigger is unknown.

### Observed exits (2026-08-14)

| # | Context | Lifetime | Last activity before exit | Streamer warnings |
|---|---------|----------|---------------------------|-------------------|
| 1 | 8192 (serve script, 17:38) | ~20 min | found dead at 17:59; log lost (serve script truncates per start) | unknown |
| 2 | 32768 (serve script, 17:59) | ~40 s | 13-token smoke test completed | 1x during test |
| 3 | 32768 (serve script, 18:03) | ~3 min 52 s | 4,873-token prefill + 4-token reply completed OK (~75 s prior) | ~10x during prefill |

Exit lifetimes vary (40 s / 4 min / 20 min) — a simple deterministic timeout is unlikely.
Exit status/signal was NOT captured for any exit (process was orphaned/nohup; log
truncated on each restart). The launchd wrapper now records exit code, uptime, and the
server-log tail per termination (see `/tmp/kimi-llama-server.lifecycle.log`).

### Known correlation (NOT proven causation)

`failed to allocate loaded ids buffers` appears during prefill batches and was present
in exits 2 and 3. In both cases the request still completed successfully and the exit
happened later, while idle. **Do not assume this warning causes the exit.**

### Required investigation (per Stability Gate, SERVICE-ROADMAP.md Task 3B)

1. Capture exact exit status/signal on every termination (wrapper already does this —
   correlate exit code with the `cleaning up before exit` path).
2. Instrument the server shutdown path to distinguish:
   - signal handling (SIGTERM/SIGINT source — who sends it?)
   - HTTP/server lifecycle shutdown (slot cleanup, task queue teardown)
   - internal error propagation (streamer failure escalations)
   - memory/allocation failure
   - external process termination (launchd, watchdog, user)
3. Establish WHO requests shutdown before assuming the buffer warning is causal.
4. Separately characterize `failed to allocate loaded ids buffers`:
   - which allocation fails (requested dimensions/bytes, layer, batch, execution phase)
   - whether it correlates with termination across a supervised soak
5. Isolation test: run **stock/non-streamed llama-server** briefly (if the machine can
   tolerate it — conventional load is ~28 GB resident). If stock also exits, the bug is
   outside the expert-streaming subsystem. If stock survives while streamed dies, the
   regression is isolated to the streaming runtime.
6. Soak gate after any fix: >= 1 h idle + repeated realistic requests (including the
   ~4,873-token prompt that has already succeeded). launchd restart count must be zero.

### Workaround / containment

- `com.openclaw.kimi-llama-server` LaunchAgent (launchd `KeepAlive=true`,
  `ThrottleInterval=10`) restarts the server within ~10 s of any exit.
- Wrapper: `tools/serve_kimi_local.launchd.sh` — records start/exit evidence to
  `/tmp/kimi-llama-server.lifecycle.log`, server output to
  `/tmp/kimi-llama-server.launchd.log`.
- Containment is operational only. The Stability Gate requires the exit to be
  diagnosed and eliminated; automatic restart does not satisfy it.

---

## BUG-002: OpenClaw aborts healthy long-running model requests mid-prefill

**Severity:** High (breaks real-agent use of the local service — Caveman, cron checks)
**Status:** Open — mechanism partially identified; fix requires OpenClaw-side change
**First observed:** 2026-08-14 (Caveman runs 01:35Z/01:56Z, kimi cron check 02:52Z)

### Symptom

An agent turn on a slow model (local kimi or cloud deepseek) is aborted internally
~157–173 s after prompt submission with **zero output tokens**, before the first
token arrives. Server-side (llama-server) logs show the request task was
**cancelled mid-prefill** (`stop: cancel task, id_task = 164/168`) — the inference
runtime itself stayed healthy.

### Evidence (all three observed runs, identical signature)

| Run | Model | prompt.submitted → abort | promptError |
|---|---|---|---|
| Caveman run 1 (webchat inter-session) | kimi-local | 01:35:15.536Z → 01:37:52.417Z (156.9 s) | "This operation was aborted" |
| Caveman run 2 (webchat inter-session) | kimi-local | 01:56:04.748Z → 01:58:52.606Z (167.9 s) | "This operation was aborted" |
| kimi cron check (session:kimi) | deepseek-v4-flash | 02:52:02.638Z → 02:54:55.537Z (172.9 s) | "This operation was aborted" |

Trajectory flags on all three: `aborted: true, externalAbort: false, timedOut: false,
idleTimedOut: false, timedOutDuringCompaction: false, timedOutDuringToolExecution:
false`, usage 0/0. The abort is an internal no-reason `AbortController.abort()`.

Live-server evidence (`/tmp/kimi-llama-server.log` / `.launchd.log`, uptime-stamped):

- task 0 (18:13): 4,873-token prefill, 153.06 s → **31.84 tok/s** — completed fine.
- task 164 (Caveman run 1): 2,048 tokens in 95.32 s = **21.49 tok/s** sustained,
  prompt ≈6.4k tokens → prefill would need ≈300 s — **cancelled at 24:40 uptime**
  (≈157 s in) while still prefilling.
- task 168 (Caveman run 2): 2,048 tokens in 94.74 s = **21.62 tok/s**, prompt ≈4.3k
  tokens — **cancelled at 45:40 uptime** (≈168 s in) mid-prefill.
- task 172 (small smoke): 12-token prefill took 5.91 s = **2.03 tok/s** — the "2 tok/s"
  figure: a tiny request where the fixed per-layer SSD expert load dominates, right
  after aborted large prefills left the expert cache cold/churning.

**Reconciliation (Pete's question):** 31.8 tok/s is sustained prefill on a large prompt
amortizing per-layer expert loads; ~2 tok/s is the effective rate on tiny requests
when the expert cache is cold; real agent prefills run ~21.5 tok/s sustained but take
200–300 s wall-clock for a full agent context. During ALL of that, llama-server emits
**zero SSE bytes** (HTTP 200 + headers only), so the client sees pure silence — and
OpenClaw aborts at ~157–173 s, mid-prefill, before the first token.

### Watchdog mechanism findings (OpenClaw dist, 2026-08-14)

- **LLM idle watchdog** (`streamWithIdleTimeout`, src/agents/embedded-agent-runner/
  run/llm-idle-timeout.ts): resets on ANY SSE chunk; **disabled by default for local
  providers** (loopback/private/.local) because local models "legitimately stay silent
  for many minutes during prompt evaluation". BUT `models.providers.kimi-local
  .timeoutSeconds: 600` in openclaw.json re-enables it via modelRequestTimeoutMs
  (idle = min(600 s, run timeout)). The three observed aborts were NOT this watchdog
  (idleTimedOut=false, and the error would be "LLM idle timeout (...)").
- **Cron watchdog** (server-cron: setup 60 s / pre-execution 60 s / execution =
  job timeoutSeconds) — did not fire (its errors carry "cron: ..." text; run aborted
  at 173 s < 300 s job timeout).
- **Lane task timeout** (command-queue: taskTimeoutMs = timeoutMs + 30 s grace) —
  error would be CommandLaneTaskTimeoutError. Not it.
- **Chat abort controller / gateway restart** — excluded (no gateway restart at
  abort times; externalAbort=false).
- **Exact abort call site NOT yet isolated** in the dist bundle (no source maps).
  Signature is a run-supervision abort with no reason, ~150–180 s, model-independent
  (hit both kimi-local and deepseek). Next step: reproduce under `openclaw` with
  `LOG_LEVEL=trace` and capture the abort stack, or check `reply-run-registry`
  cancel paths (`handle.cancel("superseded")`) with a live repro.

### Does OpenClaw recognize prompt evaluation as progress? — partially, if asked

llama-server ALREADY supports streaming prompt-progress (dev tree
`tools/server/server-context.cpp`):

- `return_progress: true` request param → SSE chunks with `prompt_progress` emitted
  at task start (0%) and during prompt processing (`SLOT_STATE_PROCESSING_PROMPT`),
  carried through the OpenAI-compat path as `chat.completion.chunk` deltas.
- `sse_ping_interval: N` → SSE comment pings while the stream is silent.
- Without either, stream=true sends only the HTTP 200/headers begin signal, then
  silence until the first token — the current behavior OpenClaw hits.

OpenClaw's OpenAI-completions body builder (`buildParams`) has **no generic
passthrough** for extra params; it has a compat-flag system (kimi-local already uses
`compat.supportsUsageInStreaming` → `stream_options.include_usage`). Sending
`return_progress: true` therefore needs one of:

1. **OpenClaw compat flag** (upstream): e.g. `compat.returnProgress` →
   `params.return_progress = true` for the kimi-local model. Cleanest.
2. **onPayload plugin hook** (openai-completions calls `options.onPayload(params,
   model)` before send) — inject `return_progress: true` for kimi-local without an
   upstream change.
3. **Server-side default change in our fork** (return_progress default true for
   stream) — smallest code change but alters upstream default behavior; only
   justified if 1/2 are unavailable.

### Recommendation (per Pete: do NOT merely raise the timeout)

1. Make prefill visible: get `return_progress: true` onto kimi-local requests
   (options 1/2 above), so any chunk-counting watchdog sees activity during prefill.
2. Then verify empirically: run a real agent turn with a long prompt against
   kimi-local and confirm the ~170 s abort disappears (progress chunks reset the
   idle watchdog; whether they satisfy the mystery run-supervision abort must be
   tested — if not, isolate that abort's call site with a trace-level repro).
3. Reconcile timeouts deliberately once progress is visible: kimi-local
   timeoutSeconds 600 vs cron job timeoutSeconds 300 vs run-level watchdog — the
   values should be coherent and based on measured prefill durations (~21 tok/s ⇒
   budget ≈ prompt_tokens/21 + generation time), not arbitrary bumps.
4. Consider `localModelLean` (experimental prompt trim) for kimi-local agents to
   shrink context, plus fewer tools (Caveman ships 79 tool schemas into every
   prompt).

### Task 3C probe — status (2026-08-14, post-reset; green-lit by design review)

Chosen mechanism: option 3 (server-side default) — `return_progress` defaults to
`true` for stream requests in our fork (`tools/server/server-task.h`, one-line
change; emission points already gated on `stream && return_progress`). OpenClaw
configuration is NOT touched: same model/prompt/cache/context/agent/timeout.
Rationale: zero client-side change keeps the diagnostic maximally clean, requires
no gateway restart (no in-flight-turn collateral), and follows the sanctioned
freeze/refreeze promotion workflow. If the probe works, a client-side compat flag
may still be adopted for scoping later.

Experiment plan (control → experiment → repeat):
- Control: current frozen live binary (off), real Caveman turn on kimi-local,
  expect ~160–175 s abort (most recent control evidence: Caveman session killed at
  runtimeMs 175267, abortedLastRun=true, ~30 min before this note).
- Experiment: refrozen binary (default on), same turn shape, expect survival.
- Repeat: second long request with default on, expect survival.

Evidence/artifacts: build log `/tmp/kimi-task3c-build.log`, server lifecycle log,
llama-server logs, OpenClaw trajectory flags on each run. Report: see
SERVICE-ROADMAP.md Task 3C + progress/task-3c-report.md.

### Related gateway crash (separate, OpenClaw-internal)

19:04:53 local: gateway crashed with uncaught `ERR_INVALID_STATE` — "A FileHandle
object was closed during garbage collection" for
`agents/kimi/sessions/<id>.jsonl.lock` (Node 26 file-handle GC rule). Gateway
restarted (launchd). Unrelated to llama-server; worth reporting upstream.

---
