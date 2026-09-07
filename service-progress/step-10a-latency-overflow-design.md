# Step 10A Design — Localize Real-Path Latency + Context-Overflow Family

## Status

PLAN (pre-registered 2026-09-04 21:1x MST before any Step 10A execution).
Owner order (Pete, 2026-09-04 21:11 MST) re-scoped Step 10A from plain TTFT
qualification to: localize the real-path latency AND context-overflow failure
family; compare equivalent real OpenClaw workloads at 64K and 128K; instrument
the full turn to attribute wall time to context construction/precheck,
compaction, llama-server prefill, generation, retries, and gateway/client
handling; determine why overflow takes 582–824 s to surface and whether 128K
eliminates or materially reduces the failures. NO sampler, prompting,
response-quality, or production-configuration changes. STOP for review before
any patch. Report: `service-progress/step-10a-latency-overflow-report.md`.

## Evidence entering Step 10A (retained, frozen)

Step 10 real-path legs (9D verify n=3/probe + step10 n=3/probe) produced walls
of 34.7–532.1 s (9D) and 48.5–398.6 s (step10) plus THREE client-side
`context_overflow` failures preserved verbatim at
`benchmarks/results/service-step-10/realpath/failures/`:

| rep | wall_s | error | agentMeta |
|---|---|---|---|
| P1-r2 (attempt 1) | 581.8 | context_overflow (precheck) | contextTokens 32768 (resolved), usage input 11548/output 460/cacheRead 77178, promptTokens 17450 |
| P2-r1 (attempt 1) | 715.3 | context_overflow (precheck) | same shape |
| P1-r2 (attempt 2) | 823.9 | context_overflow (precheck) | same shape |

Document fields seen in the failure records:
`durationMs`, `agentMeta.{contextTokens, contextTokensSource:"resolved",
usage, lastCallUsage, promptTokens}`, `systemPromptReport` (system prompt
chars 29737 incl. project context 13246; tools schema chars 16108; skills
promptChars 5900; bootstrap limits 20000/60000 chars), `error.kind =
context_overflow`, `error.message = "Context overflow: prompt too large for
the model (precheck)"`, `livenessState = blocked`, `replayInvalid = true`,
`executionTrace.attempts[0] = {provider llama-server, result success}`,
`fallbackUsed = false`.

### Key pre-registration finding (config-resolved precheck bound)

- The failure message says **(precheck)** — the refusal is CLIENT-side, before
  the request reaches llama-server.
- `agentMeta.contextTokens = 32768` with `contextTokensSource = "resolved"`.
- openclaw.json defines TWO provider entries pointing at the SAME server
  (http://127.0.0.1:18080/v1):
  - `kimi-local` → `kimi-linear-48b` `contextWindow: 65536` (matches server
    `--ctx-size 65536`; NOT in modelPolicy.allow).
  - `llama-server` → `kimi-linear-48b` `contextWindow: 32768` (IN
    modelPolicy.allow; this is the entry the real path pins:
    `--model llama-server/kimi-linear-48b`).
- The live server actually runs `--ctx-size 65536` (`/v1/models` meta
  `n_ctx: 65536`; GGUF `n_ctx_train: 1048576`).
- Therefore the overflow bound is the **provider-config-resolved context
  window (32768)**, not the server's real 64K window and not the model's 1M
  train context. A 128K server is NOT expected to move the precheck bound
  unless the gateway re-resolves context from server metadata at runtime —
  which the retained docs contradict (32768 vs server 65536 already).

### Hypotheses (pre-registered)

- H-A (config-bound precheck): overflow trips when the assembled prompt
  (system + tools + skills + project context + accumulated history) exceeds
  the provider-resolved context window (32768). Server ctx (64K/128K) is
  irrelevant to the bound. Prediction: 128K leg shows the SAME overflow
  incidence and the SAME resolved contextTokens=32768.
- H-B (trajectory accumulation): the 582–824 s walls are multi-round
  trajectories (many llama-server calls, cacheRead >> input in the usage
  totals ≈ ~5–10 calls re-sending a ~15–17K-token prefix) whose assembled
  context finally crosses the precheck bound on a later round; the wall time
  is dominated by slow per-round prompt eval (~13–43 tok/s new-token
  processing in the retained llama-server log) + decode (~5–7 tok/s) +
  inter-call gateway/tool gaps — not by a single long prefill.
- H-C (bound unchanged at 128K): even with a 128K server, `llama-server`
  provider `contextWindow` stays 32768 → resolved context stays 32768 →
  overflow persists. The lever would be aligning the provider `contextWindow`
  to the server's real ctx (config change) — recorded as a recommendation,
  NOT applied in this step.
- H-D (single-call bloat, alternative to H-B): a single round appended a very
  large payload (e.g., huge tool result) pushing one call over the bound. The
  instrumentation distinguishes H-B from H-D via per-call prompt sizes.

## Workload (equivalent real OpenClaw workloads)

Probes P1 (`Respond only PLATANOS!`) and P2 (exact `{"ok": true}` JSON) from
the frozen Step 9 suite (`benchmarks/results/service-step-09/prompts/`) — the
exact real-path workload that produced the overflow family in Step 10.
Fresh headless session per rep (no `--deliver`, model pinned
`llama-server/kimi-linear-48b`, 64K/128K ctx per leg), same shape as
9D/step10 legs. Reps: 6 per probe per ctx leg (12 turns/leg); resumable via
per-rep caching. If zero overflows occur in a leg, the leg is extended to 9
reps/probe on that ctx (overflow incidence ~1/3 at 64K per Step 10 → expect
~2 overflows in 6, ~3 in 9).

## Legs

1. **64K leg** — current launchd server (127.0.0.1:18080, `--ctx-size
   65536`, PID 92661). Record env, gateway log window, llama-server log
   window, /slots cache state before/after each turn.
2. **128K leg** — temporary llama-server instance on the SAME port with
   `--ctx-size 131072` (9C precedent: temp instance, restore launchd 64K
   after and verify `/health` + `/v1/models` n_ctx). Same workload, same
   instrumentation. launchd 64K server is stopped for the leg and restored +
   verified afterward. No config, sampler, prompt, or dist changes.

## Instrumentation (per turn)

- Turn runner: `openclaw agent --json` (same CLI as 9D/step10), fresh session
  key `agent:kimi:step10a-<ctx>-<probe>-r<N>`, --timeout 1200.
- Captures per turn:
  - wall start/end (monotonic + UTC);
  - gateway log window: `~/Library/Logs/openclaw/gateway.log`
    (per `model-fetch start/response` lines with provider/model/url,
    status, elapsedMs, timestamps — the client-side per-call timeline);
  - llama-server log window: `/tmp/kimi-llama-server.launchd.log` (64K leg)
    or the temp server's own log (128K leg) — per-task `slot launch`,
    `print_timing` (prompt eval ms / n tokens / tok-s, eval ms / n tokens /
    tok-s, total), `release` lines;
  - `/slots` snapshot before/after each turn (cache state, n_prompt);
  - CLI stdout/stderr (full), reply text, client.json (rc, wall_s), and the
    final JSON doc (durationMs, agentMeta usage/contextTokens/promptTokens,
    error, executionTrace).
- Phase attribution model:
  - context construction/precheck + gateway dispatch: wall-start → first
    gateway `model-fetch start`;
  - per model call: gateway fetch elapsedMs (gateway log) = llama prompt eval
    + decode + transport/queue (llama log per task);
  - inter-call gaps (tool execution, gateway thinking/compaction, retries):
    fetch-to-fetch intervals minus the enclosed llama task time;
  - overflow: precheck refusal event (no matching llama task; error in final
    doc) and its time-from-start;
  - compaction/retry: any fetch whose llama prompt shape or gateway-log
    marker indicates compaction, and any repeated fetch after an error.

## Analysis (pre-registered)

1. Per-turn phase table + wall composition (construction, Σ prefill, Σ
   decode, Σ gaps, overflow time).
2. Why 582–824 s: rounds × per-round cost decomposition (H-B vs H-D).
3. 64K vs 128K: overflow incidence (n/N, Fisher exact), resolved
   contextTokens (agentMeta), promptTokens at last successful call, wall
   distributions, per-phase medians.
4. Answer owner questions: (a) does 128K eliminate or materially reduce the
   failures? (b) where is the precheck bound resolved from? (c) which phase
   dominates wall time?
5. Classification: PASS / CONDITIONAL PASS / FAIL-TTFT per the roadmap
   acceptance, extended with the overflow-family determination.

## Constraints

- No sampler/prompting/response-quality/production-config changes.
- 128K leg uses a temporary server process on the same port; the launchd 64K
  server is restored and verified afterward (9C precedent).
- Single-slot server: legs and turns run sequentially; load covariates
  recorded before/after.
- Wall times are the object of study here (this step is the latency step).

## Artifacts

- Design: this file.
- Drivers (retained before execution): `tools/service_step10a_turn.py`,
  `tools/service_step10a_leg.sh`, `tools/service_step10a_analyze.py`.
- Results: `benchmarks/results/service-step-10a/{64k,128k,analysis}/`
  per-turn JSON records, gateway/llama log windows, env.txt, tables.
- Report: `service-progress/step-10a-latency-overflow-report.md`.

## Reproduction (pre-registered)

```sh
cd /Users/pmains/Code/openclaw/kimi
# 64K leg (current server):
bash tools/service_step10a_leg.sh 64k
# 128K leg (temp server on same port; restores 64K after):
bash tools/service_step10a_leg.sh 128k
# analysis:
python3 tools/service_step10a_analyze.py
```

## Stop Rule

Ends with the written report and a STOP for review. No patch under Step 10A
authorization. If the evidence shows the precheck bound is config-resolved
(32768) and 128K does not move it, the recommendation (align the `llama-server`
provider `contextWindow` with the server's real ctx, or pin the real path to
the `kimi-local` 65536 entry) is recorded for the owner's decision, not
applied.
