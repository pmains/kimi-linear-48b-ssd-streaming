# BUGS.md — Kimi Linear / Expert-Streaming Runtime

## BUG-001: Spontaneous llama-server termination under expert-streaming runtime

**Severity:** Critical (blocks the "persistent inference service" goal)
**Status:** Open — launchd KeepAlive containment in place (does NOT satisfy the gate)
**First observed:** 2026-08-14

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
