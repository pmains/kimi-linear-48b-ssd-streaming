# Qwen Manager Tier Report (fast interactive/manager model)

## Status

PASS — working interactive configuration for a Qwen3-8B local manager tier,
wired through the modified llama.cpp/llama-server stack and the Stage 6
warm-state machinery. Kimi configuration and Stage 8 artifacts untouched.

## Objective

Bring the existing local Qwen 8B model (Ollama `qwen3:8b`) into the modified
OpenClaw runtime as a fast interactive/manager tier, reusing the Stage 6
prefix-cache/warm-state infrastructure (not forking it), with baseline
measurements, tool-call verification, an acceptance harness, and isolated
commits. Explicitly out of scope (per task): Qwen-35B, OSS-120B, additional
model research, custom kernels, quantization changes.

## Asset inspection

- Model: `qwen3:8b` (Ollama) = Qwen3 8B, **Q4_K_M** (file_type 15), 36 blocks,
  32 heads / 8 KV heads, embedding 4096, 399 tensors, 5.2 GB.
- GGUF metadata: `context_length=40960`, `rope.freq_base=1e6`, **no rope
  scaling keys** baked in. Official Qwen3-8B config.json confirms:
  `max_position_embeddings=40960`, `rope_scaling=null` → **no official
  YaRN/extension for this model**. Decision: run at native 40960; no scaling
  flags applied (per "do not assume a scaling method is valid").
- Model file copied to `models/qwen3/qwen3-8b-q4_k_m.gguf`; sha256 matches the
  Ollama manifest digest (`a3de86cd...`).
- Existing integration was an `ollama` provider (num_ctx 40960) plus stale
  `llama-server/qwen3-8b` entries pointing at the *Kimi* port (18080) that no
  agent referenced. Neither reused the Stage 6 warm-state path.

## Runtime decision

- Serve via the **modified llama.cpp stack** (`runtime/live/bin/llama-server`,
  same pinned commit `0a6b2df` as the Kimi live bundle; qwen3 arch confirmed in
  the build) on **port 18082**, full Metal offload (`-ngl 99`), native
  `--ctx-size 40960`, dedicated slot cache `runtime/state/qwen-slot-cache`,
  pidfile `/tmp/qwen-llama-server.pid`.
- New `tools/serve_qwen_local.sh` (start/stop/status), modeled on the Kimi
  script but with no expert-streamer env vars (those are Kimi-specific).
- New provider `qwen-local` (baseUrl 18082) + new agent `manager` added to BOTH
  the dev config (`dev-openclaw/config/openclaw.json`) and home config
  (`~/.openclaw/openclaw.json`), additive only. Config hot-reloads (no gateway
  restart needed). Thinking mode disabled via
  `extra_body.chat_template_kwargs.enable_thinking=false` (verified; the GGUF
  defaults to thinking and would otherwise add `reasoning_content` latency).

## Results (acceptance harness, 2026-08-17)

Server: pid 97300, healthy in ~3 s. RSS: 10.54 GB at startup, 14.53 GB after
30 K-token prefill (model + 40960-ctx KV + Metal buffers).

| probe | wall | TTFT (first token) | prompt tok/s | decode tok/s | cached tokens |
|---|---|---|---|---|---|
| tool_call (163 tok) | 2.27 s | 0.38 s | 381.8 | 25.1 | 3 |
| cold (30,484 tok) | 240.5 s | 238.5 s | 128.2 | 10.0 | 3 |
| warm replay (identical) | 2.95 s | **0.99 s** | 9.3* | 9.8 | **30,483 / 30,484** |
| mutated suffix | 2.75 s | 1.65 s | 16.4* | 10.7 | 30,464 / 30,479 |

\* warm/mutated prompt tok/s reflects only the small evaluated suffix (the
bulk was cache-read), so the low number is expected and not a regression.

- **Cold TTFT 238.5 s** vs **warm TTFT 0.99 s** → the Stage 6 prefix-cache
  mechanism delivers the interactive-tier goal (bootstrap prefill is the cost;
  steady-state turns are sub-second).
- **Tool-call correctness: PASS** — `get_weather(city=Phoenix)` with
  `finish_reason=tool_calls` (non-streaming and streaming-merged probes).
  Verified end-to-end through the real gateway: a manager-agent turn executed
  `exec` (returned `2026-08-17T23:04:29Z`), `read` (TOOLS.md), and `web_search`
  (provider returned an error payload wrapped in the runtime's external-content
  notice; the model/runtime tool-call flow itself worked), then produced a
  final summary.
- **Context capacity**: native 40960 (server configured at 40960). The manager
  canonical bootstrap is **24,390 tokens** (20.6 K with a smaller tool surface
  under a different state dir) → ~15–20 K working headroom, matching the
  "bootstrap + useful working context, not maximum" target.
- **Warm-state wiring**: `openclaw prefill` CLI (in-process, stage 6E pattern)
  for `manager qwen-local/qwen3-8b` → **READY** in
  `dev-openclaw/state/warm-state/registry.json`, bound to qwen pid 97300
  (fingerprint `009a6cb1...`, 24,390 prompt tokens, cacheRead 8,312 on
  re-prefill). `prefill status` reconciles correctly.

## Problems / traps encountered (recorded)

1. **Shared warm-state registry churn**: running `prefill status` for the qwen
   tier with the qwen pidfile swept the caveman READY entry → COLD (single
   registry + single PID assumption in the prefill CLI). Fixed by restoring
   caveman's READY via re-prefill against the live Kimi server and by using the
   **same state dir the gateway uses** (`dev-openclaw/state`) for the qwen
   prefill. A separate state dir (`state-qwen`) produced a *different*
   fingerprint (bootstrap depends on state dir) and was discarded as
   non-authoritative; the shared registry is the single source of truth, with
   strict pidfile discipline (KIMI_PIDFILE per tier).
2. **Thinking mode default**: qwen3 GGUF emits `reasoning_content` first.
   Must set `chat_template_kwargs.enable_thinking=false` (in provider/agent
   `extra_body`) for the interactive tier.
3. **Bootstrap size varies with tool surface**: the canonical manager
   bootstrap is 24,390 tokens under the gateway's state dir (vs 20,631 in an
   earlier run before agent state/tools settled). Fingerprints are
   state-dir-sensitive; always prefill with the same `OPENCLAW_STATE_DIR` and
   config the gateway uses.
4. **Streaming tool-call accumulation**: naive first-delta capture misses
   streamed tool arguments; harness merges deltas by index.
5. The `measure_kimi_prefix_reuse.py` mutator requires a user-message field;
   the stage6a4 body is system-only, so the harness builds its own
   interactive-shaped body.

## Decisions

- Run Qwen through the frozen modified llama-server binary (same commit as
  Kimi live), NOT Ollama, to reuse the Stage 6 warm-state/prefix-cache path.
- New `qwen-local` provider id (like `kimi-local`); did NOT repurpose the stale
  `llama-server/qwen3-8b` entries (they point at the Kimi port and would
  conflate warm-state PID semantics).
- Native 40960 context; no rope scaling applied (model has no official
  scaling config).
- Qwen tier shares the dev warm-state registry with strict pidfile discipline
  rather than a separate state dir (fingerprints are state-dir-dependent).
- `manager` agent: primary `qwen-local/qwen3-8b`, fallback
  `deepseek/deepseek-v4-flash`, `localModelLean: true`, mirroring caveman's
  shape.

## Next Phase / Usage

- Pete can now create agents backed by `qwen-local/qwen3-8b` (or use the
  `manager` agent) for interactive OpenClaw use; the prefill path warms the
  manager bootstrap for sub-second warm TTFT.
- Operation: `tools/serve_qwen_local.sh start|stop|status` (port 18082).
  Re-prefill after any server restart:
  `OPENCLAW_STATE_DIR=dev-openclaw/state KIMI_PIDFILE=/tmp/qwen-llama-server.pid
  KIMI_SERVER_LOG=/tmp/qwen-llama-server.log node --import tsx
  prefill-cli-live.tmp.ts prefill manager qwen-local/qwen3-8b --json`
  (temp CLI entry pattern in tools/stage6e_acceptance.sh).
- Stopping point reached per task: no Qwen-35B / OSS-120B / further research.

## Reproduction

- Serve: `tools/serve_qwen_local.sh start` (port 18082, ctx 40960, ngl 99).
- Baseline/acceptance:
  `tools/qwen_acceptance.sh` →
  `benchmarks/results/qwen-acceptance-2026-08-17T23-06-33Z.json`,
  `benchmarks/results/qwen-baseline-prefix.json`,
  `benchmarks/results/qwen-tool-call-probe.json`.
- Ordinary-turn tool verification (gateway dispatch):
  `openclaw-src` + `tools/qwen_ordinary_turn.ts` (stage6d dispatch pattern).
- Wiring research: `dev-openclaw/state/qwen-tier/wiring-research.md`.
