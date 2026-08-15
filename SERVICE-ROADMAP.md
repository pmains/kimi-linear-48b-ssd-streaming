We have successfully integrated our streamed Kimi Linear 48B model with OpenClaw through our custom llama.cpp build and llama-server.

The immediate objective is NOT to optimize model inference further. The objective is to make our local-model infrastructure behave like persistent inference services and determine whether persistent KV/prefix reuse makes Kimi practical for OpenClaw agents.

Work through the following tasks sequentially. Measure before changing behavior, preserve existing working configurations, and stop if a change would require redesigning the streaming runtime.

CURRENT STATE

Kimi:
- Custom llama.cpp build with SSD-streamed MoE experts
- llama-server OpenAI-compatible endpoint
- Provider: kimi-local/kimi-linear-48b
- Current server configuration:
  - CPU inference
  - zero-copy expert cache
  - 4 GB expert-cache budget
  - ctx 32768 (raised from 8192 on 2026-08-14 so the ~11.4k-token OpenClaw agent bootstrap fits)
- Model works through OpenClaw end-to-end, including tool calling (when the server stays alive).
- Approximate observed performance:
  - prefill ~32 tok/s at scale (4,873-token prompt: 153 s)
  - decode ~3 tok/s
  - 11.4k bootstrap => ~6 min prefill per turn (no prefix reuse yet)
- Server runs as a launchd LaunchAgent (`com.openclaw.kimi-llama-server`, KeepAlive, wrapper `tools/serve_kimi_local.launchd.sh`) — see BUGS.md BUG-001: the runtime spontaneously exits while idle; KeepAlive is containment, not a fix. Do not begin Task 4 until Task 3B passes.

Qwen:
- qwen3:8b is served through Ollama.
- Historical OpenClaw configuration did not specify keep_alive, meaning Ollama's normal expiration behavior could cause sporadic agent jobs to repeatedly cold-start the model.
- Current configuration specifies keep_alive: "24h".
- Current num_ctx is 40960, which may be unnecessarily large.
- Do not assume that configuration alone means the model is actually resident. Verify using Ollama runtime state.

GOAL

Turn the local models into persistent inference infrastructure:

OpenClaw
   |
   +-- Qwen utility model
   |      persistent Ollama service
   |      model kept warm
   |
   +-- Kimi heavy model
          persistent llama-server
          model weights/trunk resident
          expert cache warm
          investigate reusable KV/prefix state

The important distinction is:

MODEL PERFORMANCE != AGENT PERFORMANCE

Cold starts, context initialization, repeated prompt prefill, cache eviction, and service lifecycle may dominate perceived agent performance even when token-generation speed is acceptable.

TASK 1 — VERIFY QWEN WARM-RESIDENCY BEHAVIOR

Do not change configuration initially.

1. Inspect the running Ollama service and `/api/ps`.
2. Record whether qwen3:8b is resident.
3. Issue one representative OpenClaw inference using qwen3:8b.
4. Measure:
   - cold request wall time
   - model load time if exposed
   - prompt evaluation time
   - decode time
   - tokens/sec
5. Confirm qwen3:8b appears in `/api/ps`.
6. Repeat the same request while warm.
7. Compare cold vs warm latency.
8. Verify that keep_alive=24h actually prevents eviction over the observation period.

Do not optimize yet. Produce a cold-vs-warm table first.

Also record the memory cost of the current num_ctx=40960 configuration.

TASK 2 — EVALUATE QWEN CONTEXT SIZE

After Task 1:

Determine whether 40,960 context is justified for the jobs this model performs.

Measure memory allocation at:
- 8192
- 16384
- current 40960

Do not change the permanent configuration merely because a smaller value uses less RAM.

Report:
- model residency
- KV/cache residency
- total process footprint
- startup/load latency
- practical input capacity

Recommend a context size for a permanently resident utility/verification agent.

TASK 3 — TURN KIMI INTO A MANAGED SERVICE

Do not modify the Kimi streaming implementation.

Convert tools/serve_kimi_local.sh from an on-demand helper into infrastructure suitable for persistent use.

On this Mac, create a launchd LaunchAgent or equivalent appropriate service definition that:

- starts llama-server automatically
- uses our custom llama.cpp binary, NOT stock llama.cpp/Ollama
- uses the existing streamed Kimi environment variables
- defaults to the validated 4 GB zero-copy expert cache
- exposes only loopback unless explicitly changed
- uses the current model
- restarts after unexpected failure
- writes useful stdout/stderr logs
- does not spawn duplicate servers
- shuts down cleanly
- survives reboot/login as appropriate

Preserve tools/serve_kimi_local.sh as a manual debugging interface if useful.

Before enabling the service:
1. show the exact service configuration
2. verify paths
3. verify environment variables
4. verify no existing llama-server process conflicts with it

After enabling:
1. confirm health endpoint
2. confirm model endpoint
3. run an OpenClaw inference
4. restart the service
5. run another OpenClaw inference

The second inference must prove that OpenClaw does not depend on some transient shell state.

TASK 3B — RUNTIME STABILITY GATE (MANDATORY BEFORE TASK 4)

Diagnose and eliminate spontaneous `llama-server` termination under the expert-streaming runtime (BUG-001). launchd KeepAlive may remain as operational containment, but automatic restart does NOT satisfy this gate.

1. Reproduce under `llama-server`, not just `llama-cli`. Run idle-only, short-request, long-prefill, and repeated-request cases. The launchd wrapper records exact exit status/signal, uptime, and final server-log lines per termination (`/tmp/kimi-llama-server.lifecycle.log`).
2. Determine what reaches `cleaning up before exit...`. Instrument the server shutdown path sufficiently to distinguish: signal handling (and WHO sends the signal), HTTP/server lifecycle shutdown, internal error propagation, memory/allocation failure, and external process termination.
3. Separately investigate `failed to allocate loaded ids buffers`. Do NOT assume it causes the exits. Establish whether the warning correlates with termination, and identify exactly which allocation fails: requested dimensions/bytes, layer, batch, execution phase.
4. Isolation test: run stock/non-streamed llama-server briefly if the machine can tolerate it (~28 GB conventional resident load). If stock also exits, we have been looking in the wrong subsystem. If stock stays alive while the streamed build dies, the regression is isolated.
5. Supervised soak after any fix: >= 1 hour idle PLUS repeated realistic requests, including the ~4,873-token prompt that has already succeeded. launchd restart count must remain zero. Preserve bit-identical inference/oracle behavior where applicable.
6. Only after the server survives the stability gate proceed to prefix/KV reuse testing (Task 4).

TASK 4 — MEASURE KIMI PREFIX/KV REUSE

This is the most important experiment. GATED on Task 3B: the server must survive a supervised soak first — there is little value measuring repeated-prefill savings if the server may disappear between turns and destroy the KV state anyway.

Do NOT increase context size yet.

Determine what llama-server actually reuses between requests.

Construct a controlled test with approximately:

    5,000–6,000 tokens identical prefix
    +
    short variable suffix

For example:

REQUEST A:
[large identical system/tool/context prefix]
Task: answer question A.

REQUEST B:
[exact same large prefix]
Task: answer question B.

REQUEST C:
[modified prefix]
Task: answer question C.

Measure separately where available:
- prompt tokens
- prompt-evaluation time
- time to first token
- decode time
- total wall time
- cache/prefix reuse statistics exposed by llama-server
- process memory before/after
- KV/cache behavior

Run at least:

A. first request after server startup
B. identical-prefix second request
C. identical-prefix third request
D. deliberately changed-prefix request

We are testing this hypothesis:

If OpenClaw repeatedly sends a large, mostly identical agent prefix, persistent llama-server prefix/KV reuse could eliminate much of Kimi's ~20 tok/s prefill bottleneck.

Do not assume this works. Prove or falsify it.

TASK 5 — TEST THROUGH OPENCLAW

If raw llama-server demonstrates prefix reuse, determine whether OpenClaw's actual request structure preserves enough prefix identity to benefit.

Capture/inspect requests without exposing secrets.

Run two or more turns through the SAME OpenClaw agent/session.

Determine:
- how much of the system prompt is identical
- whether tool definitions remain byte/token stable
- whether OpenClaw reorders or regenerates content
- whether conversation history structure allows llama-server prefix matching
- whether prompt-evaluation time falls on later turns

Compare:

raw llama-server controlled prefix reuse
vs.
actual OpenClaw multi-turn reuse

This distinction matters. llama-server supporting prefix reuse is useless to us if OpenClaw changes the prefix every request.

TASK 6 — PRODUCE AN AGENT-PERFORMANCE DECOMPOSITION

For both Qwen and Kimi, report latency approximately as:

total agent latency
  = cold-start/load
  + prompt prefill
  + inference/decode
  + tool execution
  + orchestration overhead

Where possible provide measured values rather than estimates.

For Kimi specifically distinguish:

server cold start
model/trunk residency
expert-cache warmup
KV/prefix reuse
SSD expert misses
decode

FINAL DELIVERABLE

Write a concise report containing:

1. Qwen cold vs warm performance
2. recommended Qwen context configuration
3. Kimi persistent-service configuration
4. Kimi first-turn vs repeated-prefix performance
5. OpenClaw real-session prefix-reuse results
6. memory footprints for both persistent services
7. expected idle RAM usage if both remain resident
8. expected latency for:
   - first job after reboot
   - first job after model is warm
   - subsequent turn in same agent/session
9. remaining bottlenecks
10. recommendation for which OpenClaw jobs should use Qwen, Kimi, or cloud models

IMPORTANT CONSTRAINTS

- Do not modify the expert-streaming algorithm.
- Do not change cache policy.
- Do not begin another optimization phase.
- Do not increase Kimi context merely to make a test fit.
- Do not replace our custom llama-server with Ollama.
- Do not optimize based on ru_maxrss alone on macOS.
- Do not attribute thermal/session noise to architecture.
- Preserve working configs before editing them.
- Make one meaningful change at a time.
- Keep raw measurements.
- Commit infrastructure/config/tooling changes separately from reports/results.
- Stop after the report. Do not automatically implement recommendations that emerge from the experiment.
- Do not begin Task 4 (prefix/KV reuse) until Task 3B (stability gate) passes with zero launchd restarts.
