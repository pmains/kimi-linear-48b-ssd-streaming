# Phase 11 E3E Promotion Report — Live Kimi Runtime Operating-Point Change

## Status

**PASS — the E3E operating point (KIMI_EXPERT_READ_WORKERS=4,
KIMI_EXPERT_CACHE_MB=8192) is promoted to the live Kimi runtime and verified
through the actual OpenClaw path.** All four deployment-qualification turns
(2 cold + 2 warm, E5 protocol) completed with rc=0, zero server
errors/asserts/NaN, and coherent responses. Live decode through OpenClaw:
**9.0 / 10.8 / 11.0 / 11.1 tok/s** (vs the frozen E5 baseline 3.0–3.3),
hit rate 76.3–81.2% (vs ~60), SSD traffic 140–170 MB/token (vs ~300), RSS
11.3–11.9 GB (vs 6.7–8.2) — well inside the 24 GB envelope. The promotion is
**env-config only** (launchd wrapper + plist); no source changes, no
eviction-policy or runtime-behavior changes. New deployment baseline
recorded (before/after artifacts). Stopped for review per directive. No
further optimization begun.

## Objective

Pete's directive (2026-09-01): promote the validated E3E operating point
(`KIMI_EXPERT_READ_WORKERS=4`, `KIMI_EXPERT_CACHE_MB=8192`) to the live
Kimi runtime; verify the live OpenClaw path remains correct and stable;
record the new deployment baseline; stop for review. Do not begin further
optimization.

## Promotion (recorded before/after)

- **Before** (E5 baseline, frozen): wrapper defaults `KIMI_EXPERT_CACHE_MB`
  default 4096, no `KIMI_EXPERT_READ_WORKERS` (workers=1), stats capture →
  `benchmarks/results/phase-11/e5-deployment/`. Live decode through OpenClaw
  3.0–3.3 tok/s, hit ~60%, RSS 6.7–8.2 GB (E5 report).
- **After** (E3E operating point): wrapper defaults
  `KIMI_EXPERT_CACHE_MB=8192`, `KIMI_EXPERT_READ_WORKERS=4`; stats/retr/mem/
  cache_layers capture → `benchmarks/results/phase-11/e3e-promotion/`.
  Binary unchanged: `runtime/live/COMMIT` = `a895f6826` (E4-fix fork).
- **Mechanism**: `tools/serve_kimi_local.launchd.sh` (authoritative live
  lifecycle) + `tools/com.openclaw.kimi-llama-server.plist` updated;
  launchd reloaded (`bootout` + `bootstrap`); server restarted and healthy.
  Server startup log confirms the new env took effect:
  `expert cache enabled: 8192 MiB zero-copy mode (per-layer capacity 87..88
  slots)` and `parallel expert-read enabled: 4 workers`.
- Full records: `benchmarks/results/phase-11/e3e-promotion/before-promotion.
  txt`, `after-promotion.txt`.

## Method

Same deployment-qualification protocol as E5: `tools/phase11_e5_deployment.sh`
(orchestration; cold runs preceded by a fresh server restart via launchctl
kickstart) + `tools/phase11_e5_ttft.py` (client-side timed runner through
`openclaw infer model run`, provider `kimi-local` → live llama-server on
127.0.0.1:18080). Runs: A1 (cold), B1 (warm follow-up), A2 (cold repeat),
B2 (warm repeat). Prompts: `benchmarks/prompts/phase-11-e5-engineering.md`
(A*) and `-engineering-followup.md` (B*). Per run: client rc/wall/TTFT,
server prompt-eval + eval timing, stats.csv/retr.csv deltas (cache
lookups/hits/misses/evictions, pread bytes), RSS peak sampling.

## Results

### Live OpenClaw verification (E3E-promoted operating point)

| run | cold | wall s | client rc | prefill | decode tok/s | hit % | miss/tok | SSD MB/tok | RSS peak GB |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A1 | yes | 40.9 | 0 | 8595ms/267tok (31.1 t/s) | **9.0** | 76.3 | 49.8 | 170.2 | 11.43 |
| B1 | no | 703.7 | 0 | 8922ms/289tok | **11.0** | 81.2 | 39.0 | 139.7 | 11.94 |
| A2 | yes | 30.3 | 0 | 7553ms/267tok (35.4 t/s) | **10.8** | 77.2 | 47.8 | 162.7 | 11.33 |
| B2 | no | 27.5 | 0 | 7433ms/289tok (38.9 t/s) | **11.1** | 76.8 | 48.8 | 164.6 | 11.88 |

(B1 was a long sustained generation — 7,555 decode tokens at 11.0 tok/s over
~690 s server-eval — which doubles as a stability soak; repetition ratio
0.16, coherent.)

### New deployment baseline vs frozen E5 baseline

| metric | E5 frozen (4096 MiB, W1) | **E3E live (8192 MiB, W4)** |
|---|---:|---:|
| live decode (OpenClaw) | 3.0–3.3 tok/s | **9.0–11.1 tok/s** |
| cache hit rate | 59.6–61.9% | **76.3–81.2%** |
| misses/token | ~80 | **39–50** |
| SSD traffic | ~300 MB/tok | **140–170 MB/tok** |
| RSS peak | 6.7–8.2 GB | **11.3–11.9 GB** |
| cold prefill | 11.8–12.2 s / 22–29 t/s | **7.4–8.6 s / 31–39 t/s** |

### Correctness / stability

- All four turns rc=0; 0 error/assert/GGML_ASSERT/NaN/Inf lines in the
  per-run server-log deltas.
- Responses coherent (code-review-style content, no repetition; B1's
  7,555-token sustained run completed cleanly at steady 11.0 tok/s).
- Server healthy after the full sequence (`/health` ok); launchd KeepAlive
  lifecycle log clean.
- No source changes, no eviction-policy or runtime-behavior changes —
  promotion is env-config only (E3E-validated operating point).

## Findings

1. **The promoted point works live and is a large, safe improvement**:
   live decode 3.0–3.3 → 9.0–11.1 tok/s through actual OpenClaw (3×), hit
   ~60 → 76–81%, SSD traffic ~300 → 140–170 MB/tok, cold prefill ~2× faster.
2. **RSS grows as E3E predicted** (~11.3–11.9 GB live peak vs 10.5 GB in the
   E3E llama-cli arms; the live server's slot-save-path and server overhead
   add ~1 GB). Still ~12 GB of the 24 GB envelope remains — no pressure
   concern.
3. **B1 doubled as a stability soak**: 7,555 tokens at a steady 11.0 tok/s
   with no errors — the new point is stable under sustained generation.
4. **The E5 live-path gap is closed**: E5's unexplained live-vs-benchmark
   decode deficit (3.0–3.3 vs llama-cli 6.4) is gone; live decode now sits
   at/above the E3C/E3E benchmark levels (9.47–11.45 tok/s).

## Problems / limitations

- B1's wall time (703.7 s) is much longer than the other runs because the
  follow-up prompt produced a very long generation (7,555 tokens); this is
  prompt-driven, not a defect, and it conveniently provided a sustained-run
  stability test.
- TTFT reported ≈ wall for all runs (client buffers the streamed response);
  server-side eval timings are the authoritative decode numbers and are
  reported here.
- One-session verification (4 turns). The E5 8-turn protocol was not
  re-run in full; A1/A2 cold + B1/B2 warm cover both cold and warm paths
  with repeats. If further confidence is wanted, the remaining E5 runs
  (A3/A4/B3/B4) can be appended with the same driver.

## Decisions

- Promoted `KIMI_EXPERT_READ_WORKERS=4` + `KIMI_EXPERT_CACHE_MB=8192` into
  the live launchd wrapper + plist (env-config only; binary/COMMIT
  unchanged).
- Stats/retr/mem/cache_layers capture for the live server now points at
  `benchmarks/results/phase-11/e3e-promotion/` (new baseline dir).
- Recorded new deployment baseline (before/after artifacts); no further
  optimization begun (directive).

## Next Phase

- None started. If authorized: full 8-turn E5-style re-qualification
  (A3/A4/B3/B4) for extra confidence, or the E3F candidates from the E3E
  report (residual read latency, live long-run soak, larger budgets).

## Reproduction

    # promotion mechanism (already applied)
    #   edit tools/serve_kimi_local.launchd.sh + tools/com.openclaw.kimi-llama-server.plist
    launchctl bootout "gui/$(id -u)/com.openclaw.kimi-llama-server"
    launchctl bootstrap "gui/$(id -u)" tools/com.openclaw.kimi-llama-server.plist

    # live verification (E5 protocol)
    tools/phase11_e5_deployment.sh benchmarks/results/phase-11/e3e-promotion A1 B1 A2 B2

Artifacts: `benchmarks/results/phase-11/e3e-promotion/` (before/after-
promotion.txt, cov-before/after.txt, A1/B1/A2/B2 per-run dirs with
summary.json / stats.delta / retr.delta / srvlog.delta / rss.samples /
client json / timed out, stats.csv/retr.csv/mem.csv/cache_layers.csv).
Report: this file. ROADMAP updated. Stopped for review.
