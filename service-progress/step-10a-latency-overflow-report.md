# Step 10A Report — Localize Real-Path Latency + Context-Overflow Family

## Status

**PARTIAL** (determination delivered; empirical 128K leg hardware-blocked).
STOPPED for review. No production patch, config, sampler, prompt, or
response-quality change made.

- Mechanism determination: **PASS-quality** — overflow family localized to a
  CLIENT-SIDE precheck against the provider-config-resolved context window
  (32768), independent of server `--ctx-size`; latency attributed per phase.
- Empirical 64K-vs-128K comparison: **NOT EXECUTABLE on this host** — the temp
  128K llama-server OOMs on Metal at first decode in three independent
  attempts (expert cache 8192/4096/2048 MB). Retained verbatim under
  `benchmarks/results/service-step-10a/128k/`.

## Objective

Owner order (Pete, 2026-09-04 21:11 MST): (1) localize the real-path latency
and context-overflow failure family; (2) compare equivalent real OpenClaw
workloads at 64K and 128K; (3) instrument the full turn to attribute wall time
to context construction/precheck, compaction, llama-server prefill,
generation, retries, and gateway/client handling; (4) determine why overflow
takes 582-824 s to surface and whether 128K eliminates or materially reduces
the failures. Constraints: no sampler/prompting/response-quality/production-
config changes; retain evidence; update SERVICE-ROADMAP.md; write this report;
STOP before any patch.

## Changes

- `SERVICE-ROADMAP.md` §10A: status block amended to the owner scope with the
  preliminary config-bound finding (2026-09-04).
- `service-progress/step-10a-latency-overflow-design.md` — pre-registered
  design (hypotheses H-A config-bound precheck, H-B trajectory accumulation,
  H-C bound unchanged at 128K, H-D single-call bloat).
- `tools/service_step10a_turn.py` — instrumented turn runner: captures wall
  clock, llama-server log window, gateway log window, /slots before/after, the
  final CLI doc (agentMeta contextTokens/usage/promptTokens/error/
  executionTrace), reply, client.json. (Repaired one transit-corruption marker
  on write; verified `ast.parse` clean.)
- `tools/service_step10a_leg.sh` — ctx leg driver (64k/128k), temp-server swap
  with EXIT-trap guaranteed 64K restore (a first abort left production down;
  the trap now makes that impossible), expert-cache size override.
- `tools/service_step10a_analyze.py` — merges per-turn records, parses llama
  log windows into per-task prefill/decode/total, parses gateway log
  model-fetch windows, attributes phases, compares legs.
- Results: `benchmarks/results/service-step-10a/{64k,128k,analysis}/`.

## Results

### Environment

Live endpoint 127.0.0.1:18080 (MXFP4, `--parallel 1`, expert-streaming Metal
runtime). Gateway PID 10951 (started 2026-09-04 11:55, after the 9D dist
patch). 64K leg used the launchd server (`--ctx-size 65536`, pid 92661 -> later
31219 after restore); llama log `/tmp/kimi-llama-server.launchd.log`. 128K leg
attempted a temp server (`--ctx-size 131072`) on the same port; the launchd
64K job was restored and verified (`/health` ok, `/slots` n_ctx=65536) after
every attempt.

### 64K leg (valid: 18/18 instrumented real-path turns; P1/P2 x 9)

| metric | value |
|---|---|
| rc=0 turns | 17/18 |
| rc=1 turns | 1/18 (P2-r7, 882.5 s — auto-compaction loop-guard abort, see below) |
| precheck `context_overflow` | 0/18 |
| wall min / median / max | 26.8 / 110.1 / 882.5 s |
| resolved contextTokens (all 17 parsed docs) | **32768 (source: resolved)** |
| prompt tokens at last successful call, median | 12,734 |

Per-turn phase attribution (selected rows; full table in
`analysis/analysis-summary.txt`):

| turn | wall_s | llama tasks | prefill_s | decode_s | server_s | gw_s | resid_s |
|---|---|---|---|---|---|---|---|
| P1-r1 | 42.6 | 3 | 24.7 | 14.2 | 38.9 | 0.4 | 3.7 |
| P1-r5 | 110.1 | 2 | 46.7 | 51.3 | 98.0 | 0.7 | 12.0 |
| P2-r1 | 316.7 | 6 | 75.6 | 107.0 | 182.6 | 2.5 | 134.1 |
| P2-r9 | 402.8 | 7 | 158.5 | 102.8 | 261.2 | 1.1 | 141.6 |
| P2-r7 (rc=1) | 882.5 | 17 | 492.4 | 362.8 | 855.2 | 4.0 | 27.3 |

Per-task server timings (llama log): prompt eval ~6-21 s for 87-809 new
tokens (~13-44 tok/s), decode ~4-9 s for 28-73 tokens (~5-9 tok/s). Each
real-path turn = 2-17 llama tasks (multi-round agent trajectories with tool
rounds). The system prompt + tools + skills + project context assembles to
~12.7K tokens (recorded `promptTokens`), so every round re-sends a large
mostly-cached prefix (`cacheRead` >> `input` in usage totals).

### Why overflow takes 582-824 s to surface

The three retained Step-10 failures (`realpath/failures/`, walls 581.8/715.3/
823.9 s) all carry `error.kind = context_overflow`,
`error.message = "... (precheck)"`, `livenessState = blocked`, and
`agentMeta.contextTokens = 32768` with `contextTokensSource = "resolved"`.
The refusal is therefore emitted by the GATEWAY before dispatch (client-side
precheck), not by llama-server. The wall time is NOT the precheck (instant) —
it is the accumulated multi-round trajectory cost: each round is a llama task
(~10-30 s server-side at the measured 13-44 tok/s prefill and 5-9 tok/s
decode) plus gateway/tool gaps, and the assembled prompt only crosses the
resolved 32768 bound on a later round. Step-10 usage totals (input 11,548 /
cacheRead 77,178 over the failing turn) confirm many rounds re-sending a
growing prefix before the crossing round. This leg's 882 s P2-r7 (17 llama
tasks, 855 s server time) is the same trajectory shape ending in the
compaction loop-guard instead of the precheck.

### Why the bound is 32768 and what 128K would change

openclaw.json defines two provider entries pointing at the SAME server
(http://127.0.0.1:18080/v1): `kimi-local` -> `kimi-linear-48b`
`contextWindow: 65536` (not in modelPolicy.allow) and `llama-server` ->
`kimi-linear-48b` `contextWindow: 32768` (allow-listed; the real path pins
`llama-server/kimi-linear-48b`). Every parsed doc in this leg (17/17) and the
retained Step-10 failures report `contextTokens = 32768 (resolved)` while the
server advertises `n_ctx: 65536` in /v1/models (GGUF `n_ctx_train` is
1,048,576). The gateway therefore resolves the precheck bound from the
provider CONFIG entry, not from the server. A 128K server would not move a
config-side 32768 bound (H-C): overflow incidence is expected to be unchanged
at 128K. The lever is aligning the `llama-server` provider `contextWindow`
with the real server window (or pinning the real path to the `kimi-local`
65536 entry) — a production-config change, recorded as a recommendation, NOT
applied in this step.

### Compaction (new evidence, same long-wall family)

P2-r7 (rc=1, 882.5 s, 17 llama tasks) aborted with:
`CRITICAL: tool read repeated 3 times with identical arguments and identical
results within 3 attempts after auto-compaction. The compaction did not break
the loop. Aborting to prevent runaway resource use.`
This is direct evidence that (a) auto-compaction engages on the real path
mid-trajectory, and (b) its loop-guard terminates runaway turns. It is a
second real-path failure family with the same 10-minute wall signature as the
overflow family and belongs in the same liveness workstream.

### 128K leg (hardware-blocked, 3 attempts)

| attempt | expert cache | outcome |
|---|---|---|
| 1 (05:27Z) | 8192 MB | server "ready" (n_ctx=131072), then Metal OOM on first decode: `kIOGPUCommandBufferCallbackErrorOutOfMemory`, `decode() failed: Compute error.` on every rep (rc=1, ~2 s); invalid artifacts quarantined to `128k/oom-artifacts/` |
| 2 (05:33Z) | 4096 MB | same OOM at first decode; invalid artifacts quarantined |
| 3 (05:42Z) | 2048 MB | `failed to fit params to free device memory` at startup; server never became ready; aborted |

Root cause: the 128K KV cache roughly doubles vs 64K and, together with the
resident host (gateway, browser, etc. — ~19 GB committed on the 24 GB
machine), exceeds unified memory available to Metal at any tested expert-cache
size. Startup log line:
`W common_fit_params: failed to fit params to free device memory:
n_gpu_layers already set by user to 999, abort`.
Note: Step 9C ran a 128K server successfully earlier on 2026-09-04 when host
memory pressure was lower — 128K is environment-feasible, not a hard model
limit. The 128K leg driver is retained and re-runnable
(`KIMI_EXPERT_CACHE_MB=... bash tools/service_step10a_leg.sh 128k`) when memory
headroom allows.

## Problems

1. First 128K leg attempt aborted before the restore block (missing
   `slot-cache-128k` dir), leaving the production 64K server down. Fixed
   immediately (server restored + verified); the leg driver now `mkdir`s the
   slot dir and installs an EXIT-trap restore so production can never be left
   down by an abort.
2. Retry-2 of the 128K leg silently "cached" the invalid OOM-attempt rep files
   (rc=1 artifacts present => skipped). Invalid artifacts were quarantined to
   `128k/oom-artifacts/` and the leg re-run; only valid rc=0-with-doc rows are
   analyzed.
3. 128K is not runnable on this host under current memory load (three
   independent OOM attempts, retained). This blocks the empirical
   64K-vs-128K overflow-incidence comparison; the mechanism-level answer
   (config-side 32768 bound) is unaffected.
4. Cosmetic: an awk-quoting bug left `gateway_pid` empty in the 64K env.txt
   (fixed in the driver; value recorded manually: 10951).

## Decisions

- Precheck-bound determination takes precedence: overflow is config-resolved
  (32768, `llama-server` provider entry), not server-capacity — established by
  17/17 parsed docs + retained failures, not assumed. 128K is therefore not
  expected to eliminate the family; recommendation recorded, not applied.
- Classified PARTIAL rather than PASS because the empirical 128K comparison
  leg could not execute on this hardware today; every other owner question is
  answered with retained evidence.
- Retained, not retried, the three 128K OOM attempts (hardware evidence);
  retried real-path reps in Step-10 style only where the server itself failed
  (invalid artifacts quarantined).

## Next Phase

- Owner decision on the recorded recommendation: align the `llama-server`
  provider `contextWindow` (32768 -> 65536, matching the actual server) or pin
  the real path to the `kimi-local` 65536 entry — expected to eliminate the
  context-overflow precheck family at its source (config change; outside this
  step's authorization).
- Re-run the 128K leg when host memory headroom allows (9C precedent shows
  128K is feasible on this machine when memory is free) to complete the
  empirical comparison.
- Carry the compaction loop-guard abort (P2-r7 family) into the same liveness
  workstream as the overflow family.
- Step 10A's TTFT decomposition (roadmap acceptance): dominant phase at 64K is
  llama prefill+decode across multi-round trajectories (~12.7K-token prompt
  re-sent per round at 13-44 tok/s prefill); walls of 27 s-14 min for trivial
  probes are NOT practical for interactive use and route to the latency
  workstream after review.

## Reproduction

```sh
cd /Users/pmains/Code/openclaw/kimi
# instrumented single turn:
python3 tools/service_step10a_turn.py <outdir> <label> \
    benchmarks/results/service-step-09/prompts/P1.md \
    agent:kimi:step10a-64k-P1-r1 --agent kimi --timeout 1200 \
    --llama-log /tmp/kimi-llama-server.launchd.log \
    --gateway-log "$HOME/Library/Logs/openclaw/gateway.log"
# full 64K leg (current launchd server):
bash tools/service_step10a_leg.sh 64k
# 128K leg (temp server, restores 64K; re-run when memory headroom allows):
KIMI_EXPERT_CACHE_MB=2048 bash tools/service_step10a_leg.sh 128k
# analysis:
python3 tools/service_step10a_analyze.py
```

Artifacts: `benchmarks/results/service-step-10a/64k/` (18 per-turn record/
client/llama-window/gateway-window/slots files, env.txt, leg logs),
`benchmarks/results/service-step-10a/128k/` (3 leg logs,
`llama-server-128k.log` OOM evidence, `oom-artifacts/`),
`benchmarks/results/service-step-10a/analysis/` (analysis.json,
analysis-summary.txt). Design:
`service-progress/step-10a-latency-overflow-design.md`.
