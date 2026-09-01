# Phase 11 E5 — Live OpenClaw Deployment Qualification

## Status

**PASS (experiment complete) — CONDITIONAL PASS (deployment) / /prefill category A (likely unnecessary).**
The corrected, quality-qualified Metal MXFP4 streamed runtime was promoted into
the live runtime and measured through the actual modified OpenClaw (provider
`kimi-local` → live llama-server on 127.0.0.1:18080) with a realistic
Alkaline-style engineering workload, cold and warm, with reproducibility
repeats. All eight turns (4 cold + 4 warm) completed: EXIT=0, coherent
responses, zero server errors/asserts/NaN/Inf, no OpenClaw timeouts. Stopped
for review per directive. E3 not begun; /prefill not removed.

## Objective

Determine whether the corrected Metal Kimi runtime is practical for normal
OpenClaw agent use, and whether the custom /prefill workflow is still
operationally necessary. This is a deployment/performance experiment, NOT an
optimization experiment. Frozen baseline: corrected E2 Metal path with the
routed-expert-ID synchronization fix (fork `a895f6826`; E4 quality-qualified:
Metal PPL 6.7528 vs CPU 6.7596).

## Promotion (recorded before/after)

- **Before**: `runtime/live/COMMIT` = `cad71603` (pre-fix, "stage-6a7");
  live server ran the Q4_K_M model, `-ngl 0` (CPU), ctx 65536, port 18080.
- **After**: `runtime/live/COMMIT` = `a895f6826` (E4-fix: routed-expert-ID
  synchronization); live server runs the MXFP4 model (`moonshotai_Kimi-
  Linear-48B-A3B-Instruct-MXFP4_MOE.gguf`), `-ngl 999`, ctx 65536, port
  18080, `--no-mmap`, zerocopy expert cache 4096 MiB, naive stream, and the
  corrected-Metal envs `KIMI_STREAM_METAL_STAGE=1` + `KIMI_STREAM_E2_
  DIRECT_PLACE=1` (the frozen E2 baseline + sync fix).
- **Mechanism**: existing routine path — `tools/freeze_live_runtime.sh`
  (copies build-metal binary+dylibs+plugins into `runtime/live/`, rewrites
  rpath, records COMMIT), then the launchd wrapper
  `tools/serve_kimi_local.launchd.sh` (authoritative live lifecycle,
  KeepAlive) updated to the corrected baseline and the server restarted via
  `launchctl kickstart -k` (healthy in ~4–15 s). The launchd wrapper also
  enables the streamer's existing env-gated stats/retr/mem/cache_layers
  capture (Phase 7 observability, default-off in the streamer) pointing at
  the E5 results dir for hit/miss + SSD-traffic evidence.
- Full before/after records: `benchmarks/results/phase-11/e5-deployment/
  before-promotion.txt`, `after-promotion.txt`.

## Method

Driver: `tools/phase11_e5_deployment.sh` (orchestration; run-list
parameterizable for resume) + `tools/phase11_e5_ttft.py` (client-side timed
runner: wall + time-to-first-output through `openclaw infer model run`).
Prompts (retained): `benchmarks/prompts/phase-11-e5-engineering.md` (run A),
`-engineering-followup.md` / `-engineering-followup2.md` (run B variants),
`-alkaline-context.md` / `-alkaline-context-followup.md` (realistic current
restricted-toolset Alkaline context).

Runs (each A* preceded by a fresh server restart = cold; B* = immediate warm
follow-up on the same server):

| run | cold | wall s | client TTFT s | prefill ms/tok | prefill tok/s | decode ms/tok | decode tok/s | RSS peak MB | cache lookups | hit % | misses | evicts |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1 | yes | 64.97 | 64.88 | 11819.65/267 | 22.6 | 48370.64/158 | 3.27 | 8049.9 | 33237 | 60.7 | 13075 | 13726 |
| A2 | yes | 90.52 | 90.44 | 11791.32/267 | 22.6 | 73896.03/229 | 3.10 | 7846.0 | 48005 | 61.9 | 18290 | 18941 |
| A3 | yes | 61.79 | 61.69 | 12153.3/267 | 22.0 | 44757.92/143 | 3.20 | 8079.0 | 30117 | 60.1 | 12003 | 12654 |
| A4 | yes | 71.52 | 71.43 | 11885.18/347 | 29.2 | 54802.19/177 | 3.23 | 7975.2 | 37186 | 60.8 | 14581 | 15222 |
| B1 | no | 68.82 | 68.75 | 1543.56/4 (reuse) | — | 62338.69/192 | 3.08 | 6703.2 | 40324 | 59.9 | 16186 | 16186 |
| B2 | no | 72.76 | 72.70 | 11913.07/289 | 24.3 | 55974.58/168 | 3.00 | 8211.5 | 35332 | 59.6 | 14265 | 15276 |
| B3 | no | 86.40 | 86.32 | 1412.94/4 (reuse) | — | 80126.11/250 | 3.11 | 6656.6 | 52390 | 61.0 | 20419 | 20419 |
| B4 | no | 67.67 | 67.60 | 12336.81/374 | 30.3 | 50509.27/154 | 3.05 | 8066.9 | 32418 | 59.6 | 13090 | 14106 |

(Note: A3/A4 `summary.json` cold flags are stale false from a driver-label bug
fixed after those runs; both were preceded by `restart_server` per driver log
— they are cold runs.)

## Measurements (directive items 1–12)

1. **Prompt tokens**: 267 (engineering prompt) / 347 (alkaline-context
   prompt) — from the server log `prompt eval time = X ms / N tokens`.
2. **Cold prefill wall**: 11.79–12.15 s (A1–A4).
3. **Cold prefill tok/s**: 22.0–29.2 (corrected path; the old 709 tok/s
   pre-fix measurement is invalid per directive and NOT used).
4. **TTFT (time to first generated token)**: server-side ≈ prefill time ≈
   **11.8–12.4 s cold**; provider-level streaming (SSE) measured
   **11.6 s** first content chunk for the engineering prompt. Client TTFT
   through `openclaw infer model run` ≈ wall (the CLI buffers the response;
   a streaming UI would surface the first token at ≈12 s). Warm-with-reuse:
   **≈1.4 s** (B1/B3, LCP f_sim=1.000, only 4 new tokens evaluated).
5. **Decode tok/s**: **3.0–3.3** (corrected path; old 24 tok/s pre-fix
   invalid per directive).
6. **Total turn wall**: cold 61.8–90.5 s; warm 67.7–86.4 s — dominated by
   decode (a 150–250-token response at ~3 tok/s).
7. **Peak RSS**: server process 6.7–8.4 GB (≈30–35% of 24 GB unified);
   comfortable headroom for the OS and the 4 GiB expert cache.
8. **Expert cache**: zerocopy 4096 MiB, naive stream; per-turn hit rate
   **59.6–61.9 %** (cache lookups 30–52k, misses 12–20k, evictions 12–20k
   per turn — cache churn under the prefill working set).
9. **SSD/expert-read traffic** (already-observable streamer accounting,
   stats.csv `pread_bytes` col5): **60.9–88.8 GB per turn** (≈2.1 GB/s
   effective pread at cold-start; consistent with Phase 8 MB/token range).
   retr.csv occurrence rows 49k–328k/turn.
10. **Prefix/prompt-cache reuse on a subsequent turn**: YES when the new
    prompt's token prefix matches the slot's cached prompt (B1/B3:
    `selected slot by LCP similarity, f_sim_best = 1.000`, prefill collapsed
    to 4 tokens ≈ 1.4 s). NO when the prefix differs (B2/B4: full re-prefill
    ≈ 12 s). Real agent conversations (constant system prompt + appended
    user turn) fall in the reuse case.
11. **Warm-turn prefill + TTFT**: with matching prefix ≈1.4 s prefill /
    ≈1.4 s TTFT; without ≈12 s (same as cold).
12. **OpenClaw timeout/session/tool failures**: none in the measured path —
    all EXIT=0, CLI stderr empty, transport `timeoutMs=3600000` (1 h, no
    timeout risk), no gateway/session errors. Note: `openclaw infer model
    run --gateway` is blocked by `agents.defaults.modelPolicy.allow`
    (kimi-local not listed for agent main/kimi) — an OpenClaw config
    restriction, NOT changed per directive; the local infer path is the
    documented live-runtime verification surface (TOOLS.md) and was used.

## Correctness (mandatory)

All eight responses coherent (verified per run: engineering-review answers
about the `load_expert` data path, no garbage/corruption); server logs show
0 error/assert/NaN/Inf lines across all runs; routed-expert behavior correct
(real router-selected experts on the trace-off path since the sync fix).

## /prefill classification

**A — likely unnecessary** for the measured workload: ordinary corrected-Metal
cold startup TTFT ≈ **12 s** (≤ 60 s threshold), warm turns ≈ 1.4 s with
prefix reuse. The measured prompt approximates the current restricted
toolset Alkaline context (347 tokens); cold prefill scales at 22–29 tok/s, so
a larger real bootstrap (multi-thousand tokens) would scale linearly
(e.g. 5k tokens ≈ 170–230 s → would move the classification toward B/C).
That boundary is recorded; the directive's decision guidance applies to the
measured realistic prompt.

## Deployment readiness

**CONDITIONAL PASS — usable, with one specific operational limitation:**
the corrected runtime is correct, stable, and memory-bounded (all runs
EXIT=0, coherent, RSS 6.7–8.4 GB, TTFT ≈ 12 s cold / 1.4 s warm-reuse), and
serves through the actual OpenClaw provider path. The limitation is **decode
throughput ≈ 3.0–3.3 tok/s** on the corrected Metal path — a substantive
150–250-token response takes ~60–90 s wall, and per-turn SSD expert-read
traffic is heavy (61–89 GB at the 4 GiB cache / ~60 % hit). Neither is a
correctness blocker; both are performance (E3) territory, not begun.

## Problems

- Two multi-run driver invocations were SIGTERM'd mid-flight by the exec host
  (first full run during B1; the combined A3/B3+A4/B4 run during A3). Recovery
  was clean: driver parameterized with a run list, each turn re-run
  individually and retained (A1/B1/A2/B2 from run 1 + resume; A3/B3, A4/B4
  individually). No data loss; all runs have summary.json + srvlog.delta +
  stats/retr deltas.
- B1's prefix-reuse signal was initially confounded (its prompt file header
  comments differ from A's; the reuse shown was against the killed first
  attempt's slot state). B3 (byte-identical prefix follow-up) is the clean
  reuse measurement; B2/B4 show the honest no-reuse case.
- Driver label bug: summary.json `pread_bytes` field summed pread_us (col 6);
  corrected pread_bytes (col 5) values reported here and recomputed from the
  retained stats.delta files.
- Optional /prefill control not run: the classification is determinable from
  the measured cold/warm TTFT directly, and /prefill targets the caveman
  bootstrap per SERVICE-ROADMAP, not the kimi agent's ordinary path; the
  directive permits skipping it when it would not change the classification.

## Decisions

- Promoted fork `a895f6826` to live via the existing freeze + launchd
  mechanism (minimum routine deployment step); recorded before/after commit
  and full launch config.
- No OpenClaw config changes (modelPolicy untouched; gateway restriction
  observed and reported, not modified).
- No kernels, quantization, cache algorithms, synchronization behavior,
  model math, or /prefill modified. E3 not begun.

## Next Phase

- E3 (compute-side optimization) is the identified next step to address the
  deployment limitation (decode ≈ 3 tok/s); not begun per directive.
- If the real kimi agent bootstrap is significantly larger than the tested
  prompt, re-measure cold prefill at that size before relying on the /prefill
  classification (recorded boundary above).

## Reproduction

    # promotion (already done; reproduced with)
    cd llama.cpp && cmake --build build-metal -j10 && cd ..
    tools/freeze_live_runtime.sh          # records fork HEAD into runtime/live/COMMIT
    launchctl kickstart -k gui/$(id -u)/com.openclaw.kimi-llama-server

    # E5 runs (driver retained; run list parameterizable)
    tools/phase11_e5_deployment.sh benchmarks/results/phase-11/e5-deployment A1 B1 A2 B2
    PROMPT_B=benchmarks/prompts/phase-11-e5-engineering-followup2.md \
      tools/phase11_e5_deployment.sh benchmarks/results/phase-11/e5-deployment A3 B3
    PROMPT_A=benchmarks/prompts/phase-11-e5-alkaline-context.md \
    PROMPT_B=benchmarks/prompts/phase-11-e5-alkaline-context-followup.md \
      tools/phase11_e5_deployment.sh benchmarks/results/phase-11/e5-deployment A4 B4

    # client timing helper
    python3 tools/phase11_e5_ttft.py <outdir> <label> <promptfile>

Artifacts: `benchmarks/results/phase-11/e5-deployment/` (per-run dirs A1–B4
with summary.json, srvlog.delta, stats.delta, retr.delta, rss.samples,
client.json, timed.out; driver logs; covariates; before/after-promotion.txt;
server stats/retr/mem/cache_layers capture). Prompts:
`benchmarks/prompts/phase-11-e5-*.md`. Report: this file. ROADMAP updated.
