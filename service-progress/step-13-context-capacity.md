# Step 13 Report — Context Capacity Qualification and Production Promotion

## Status

IN PROGRESS — **13A PASS (2026-09-07)**. 128K reproduced on the current
production runtime under the single-llama-server swap protocol. 13B (256K
probe) not yet started; waiting at the 13A→13B gate for owner word.

## Objective

Step 13 (owner-authored 2026-09-07, §13–13G) determines whether 128K or 256K
is the appropriate production context window for Kimi Linear, then promotes
and qualifies only the selected target through the real OpenClaw agent path.

13A specifically: reproduce the previously validated 128K capability
(ctx 131072) using the **current** runtime (frozen live binary a895f6826,
MXFP4 GGUF, E3E operating env) under controlled conditions — and record the
TTFT/memory tradeoff data the owner asked to track ("just so long as you
track what the tradeoffs are in terms of TTFT, we can decide on 256k or 128k
later. Right now, we are testing and exploring.").

## Changes

- `SERVICE-ROADMAP.md` — committed Pete's §13 text as `641b5f2`; this report
  updates §13 Status: PENDING → IN PROGRESS with the 13A PASS record.
- `tools/service_step13a.sh` — new retained 13A driver: single-llama-server
  swap protocol (bootout launchd 64K job → manual ctx-131072 server on the
  live port → gates/probes → unconditional restore of the launchd 64K job).
  Revision note: first version died mid-swap on a bash `set -u` bug
  (`local tmo=... deadline=$((...tmo...))` single-line form expands before
  assignment); production was restored manually and verified, and the driver
  was rewritten with split `local` statements, a preflight abort guard, and
  streamed TTFT capture.
- `benchmarks/results/service-step-13/` — baseline-64k JSON + `13a-128k/`
  evidence (preflight, probe-a, probe-b payload/stream, server log, startup
  mem lines, RSS samples/peak, results.json, needle). Telemetry CSVs
  (stats/retr/mem/cache_layers) are env-redirected into
  `13a-128k/telemetry/` and retained locally only (retr.csv ~1.8 GB — not
  committed, consistent with repo telemetry policy).
- This file: `service-progress/step-13-context-capacity.md`.

## Baseline (recorded 2026-09-07, before the swap)

64K qualified production state (see `baseline-64k-20260907.json`):

- llama-server: ctx 65536, MXFP4_MOE.gguf, `runtime/live` (a895f6826),
  ngl 999, no-mmap, parallel 1, port 18080; launchd job
  `com.openclaw.kimi-llama-server`; RSS ~8.8 GB at 64K.
- OpenClaw: kimi-local provider models[0] contextWindow 65536
  (unchanged during 13A, per roadmap).
- E3E env: naive stream, KIMI_EXPERT_CACHE_MB=8192, READ_WORKERS=4,
  zerocopy, KIMI_STREAM_METAL_STAGE=1, KIMI_STREAM_E2_DIRECT_PLACE=1.
- 11B: active pathname `/opt/homebrew/lib/node_modules/openclaw/dist/
  redact-CquADQ9-.js`, sha `8baf474684…`.
- Gateway PID 56719 healthz 200; llama 200; FK violations 0.
- GGUF metadata: `kimi-linear.context_length = 1048576` (1M trained ctx —
  both 131072 and 262144 are inside the trained range; rope freq_base
  10000, dim 64). 13A/B are therefore memory/Metal residency questions, not
  rope/out-of-range questions.

## Method (13A)

Single-llama-server swap window (owner-confirmed protocol 2026-09-07: "There
can be only one llama-server"):

1. Preflight record + abort guard (llama/gw must be 200 before swap).
2. `launchctl bootout` of the 64K launchd job; port-free wait.
3. Manual server start: identical binary/model/env, only `--ctx-size`
   131072; `-lv 4` observability; telemetry env vars redirected to the
   13A evidence dir; `--slot-save-path` omitted (stage-8 rung parity; avoids
   touching the production warm-state slot cache).
4. Gates: G1 allocation/startup healthy + KV/recurrent/Metal lines;
   G2 boundary prompt admitted with tokens > 65536; G3 short probe ok AND
   needle (embedded at 90% char depth ≈ 85.5K-token position — beyond the
   64K boundary) retrieved; G4 server healthy after.
5. Restore: kill test server → `launchctl bootstrap` the 64K plist → verify
   health, ctx 65536, FK 0, 11B sha unchanged.

## Results (13A, 2026-09-07)

Classification: **PASS** (`results.json`, window 10:43:27–11:30:22 MST).

| item | value |
|---|---|
| configured ctx | 131072 (server resolved n_ctx = 131072) |
| KV allocation | 1008.00 MiB (131072 cells, 7 layers, K f16) |
| KDA/recurrent | 42.81 MiB (27 layers, R 2.81 + S 40.00 MiB) |
| expert cache | armed 8192 MiB, zero-copy mode |
| Metal | ggml_metal_init OK (Apple M5); MTL0 KV buffer 1008 MiB |
| probe-a (short "ok") | ok=true; streamed TTFT 0.011 s |
| probe-b prompt | **95,004 tokens admitted** (gate2 pass) |
| needle | NEEDLE-13A-7394 retrieved exactly (position ≈ 85,504) |
| prefill | 2,797,977.72 ms / 95,004 tokens = **33.95 tok/s** |
| TTFT (long prompt) | ≈ prefill wall ≈ 2,798 s (~46.6 min) |
| decode | 1,868.04 ms / 10 tokens = **5.35 tok/s** at 95K ctx |
| total wall | 2,799.92 s (~46.7 min) |
| RSS | steady ~10.85 GiB; peak during prefill **11.80 GiB** |
| host free | 26% after run (24 GiB machine) — safe envelope |
| gate4 | server healthy after long-context use: pass |
| gates | G1 pass, G2 pass, G3 pass, G4 pass |

### TTFT tradeoff data (for the 128K-vs-256K decision)

- Short-prompt TTFT: 64K ≈ 0.103 s (baseline probe), 128K ≈ 0.011 s —
  no material change; short TTFT is not the cost of a bigger window.
- Long-prompt TTFT at 128K ≈ the full prefill (2,798 s for 95K tokens at
  33.95 tok/s). Prefill throughput at depth is the dominant cost and is
  expected to be the dominant differentiator at 256K.
- Decode degrades with context depth: ~9.3 tok/s at 14–21K ctx (Step 12
  eng-c1s12), ~7.4 at ~30K (Step 12 long-g1), **5.35 tok/s at ~95K ctx**
  (this run). A bigger window only matters where deep context is actually
  used; normal short/medium turns will decode at their usual depth.
- Memory headroom at 128K: peak 11.80 GiB of 24 GiB (26% free) with the
  8 GiB expert cache and 1008 MiB KV. 256K adds ~1 GiB KV (K f16 linear)
  plus prefill buffers — allocation feasibility is 13B's question.

## Problems

- **Driver v1 bash bug (recovered):** first 13A attempt died mid-swap on a
  `set -u` bug (single-line `local tmo=... deadline=$((...tmo...))` expands
  the arithmetic before `tmo` is assigned). The launchd job had already been
  booted out; production llama was down ~2 min until manual restore
  (`launchctl bootstrap`). Verified healthy (llama 200, ctx 65536, gw 200,
  sha `8baf4746`, FK 0) before re-running. Root cause fixed in the rewritten
  driver (split `local` statements + preflight abort guard); second run
  clean end-to-end with automatic restore verified.
- **Streamed TTFT metric caveat:** the first SSE frame arrives immediately
  (role/metadata frame), so the recorded streamed "TTFT" (0.073 s) is not
  time-to-first-content-token. True TTFT to first content ≈ prefill wall
  (2,798 s); reported as such above.
- Telemetry CSVs (1.8 GB retr.csv) kept locally, not committed (repo
  telemetry policy). All curated evidence is small and committed.

## Decisions

- Single-llama-server swap protocol for all Step 13 testing (owner order
  2026-09-07), restoring the launchd 64K job after every window so the
  validated 64K baseline is never left in an intermediate state.
- 13A uses the production model/env exactly; only ctx differs. GGUF trained
  ctx (1,048,576) recorded so 13B's 262144 ask is framed correctly.
- 13A classified PASS on the stage-8 invariant gates; no failure boundary
  to classify, no optimization opened.

## Next Phase

13B (probe 256K feasibility: allocation at 262144, then a 140–160K-token
prompt with a retrieval target beyond 128K) — same swap protocol, new
window. Requires owner go at the 13A gate (roadmap: 13A pass → proceed to
13B; owner said testing/exploring and decision on 128K vs 256K comes later).

## Reproduction

```bash
# 13A (swap window; restores 64K launchd job on exit regardless of outcome)
bash tools/service_step13a.sh
# evidence: benchmarks/results/service-step-13/13a-128k/{results.json,
#   preflight-*.json, probe-a.json, probe-b.json, probe-b.stream.json,
#   server-131072.log, startup.mem.txt, rss-samples.tsv, rss-peak.txt}
# baseline: benchmarks/results/service-step-13/baseline-64k-20260907.json
```
