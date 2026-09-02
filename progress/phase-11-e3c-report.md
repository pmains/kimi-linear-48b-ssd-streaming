# Phase 11 E3C Report — Parallel/Overlapped Expert Reads on Corrected Metal

## Status

**PASS — bounded parallel expert reads materially improve corrected Metal
decode, using the existing env-gated machinery, with zero correctness or
quality cost.** `KIMI_EXPERT_READ_WORKERS` 2 and 4 (Phase 9D/9F pool, default
1 = frozen) raise decode from **6.41 → 8.84 → 9.47 tok/s** (+38% / +48%)
on the corrected Metal baseline, cut the read-phase wall from 86.0 → 57.3 →
48.8 ms/token, and recover most of the E3B-identified read/compute
interleaving gap (live effective read bandwidth 3.50 → 5.25 → 6.17 GB/s,
approaching the E3B standalone-replay ceiling of 4.10 @1 / 6.68 @2 /
8.01 @4 GB/s). Correctness: EXIT=0 with zero asserts/MISMATCH, 0/173,670
retrieval mismatches in every arm, byte-identical generated output (W1 vs
W4, pre-banner), identical cache behavior (208 lookups, 61.5% hit, 80
misses/evictions per step), and bounded E4 PPL **7.2601 at both W1 and W4**
(exact preservation of the idsync-fix Metal quality reference). No
production behavior changed (env-gated, default 1 = frozen). Stopped for
review. Broader compute/kernel optimization NOT started.

## Objective

E3B showed the SSD/read pattern is not intrinsically slow: live inference
~2.9 GB/s, standalone serial replay ~4.1 GB/s, bounded concurrency ~6.7–8.0
GB/s at 2–4 workers. E3C (authorized): test whether bounded
parallel/overlapped expert reads can reduce decode pread_us in the corrected
Metal runtime, starting from the existing Phase 9C/9D parallel-read
machinery, comparing workers 1/2/4 against the frozen baseline. Preserve
routing semantics, model math, E4 quality, and Phase 7 invariants; keep
changes env-gated/default-off. Measure decode tok/s, pread ms/token,
effective read bandwidth, cache behavior, SSD bytes/token, correctness.
Stop for review; do not proceed into broader compute/kernel optimization.

## Method

No code changes required — the Phase 9D/9F bounded parallel-read pool
(`KIMI_EXPERT_READ_WORKERS=N`, default 1 = frozen sequential path) already
exists and is wired into the naive streamed load path
(`pread_batch_issue` → pool workers for N>1; sequential pread for N=1).
Arms (identical deterministic config, only `KIMI_EXPERT_READ_WORKERS`
differs):

- binary: fork `a895f6826` (corrected Metal baseline), MXFP4 model,
  `-c 4096`, `-n 48`, `--temp 0 --seed 7`, `-ngl 999`,
  `KIMI_STREAM_METAL_STAGE=1`, `KIMI_STREAM_E2_DIRECT_PLACE=1`, naive
  stream, zerocopy expert cache 4096 MiB; prompt
  `benchmarks/prompts/phase-11-e5-engineering.md`.
- capture per arm: `KIMI_STREAM_STATS_FILE`, `KIMI_STREAM_RETR_FILE`,
  `KIMI_STREAM_CACHE_LAYERS_FILE`; plus bounded E4 perplexity (8×512)
  at workers=1 and workers=4 via `tools/phase11_e4_perplexity.sh`.

## Results

### Decode-step decomposition (1 tok/step), corrected Metal

| workers | decode tok/s | step total ms | pread_wall ms | pread sum ms | wall GB/s | MB/token | calls | lookups | hit % | misses | evicts |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 (frozen) | 6.41 | 156.0 | 86.0 | 86.0 | 3.50 | 301 | 240 | 208 | 61.5 | 80 | 80 |
| 2 | 8.84 | 113.2 | **57.3** | 109.3 | **5.25** | 301 | 240 | 208 | 61.5 | 80 | 80 |
| 4 | 9.47 | 105.6 | **48.8** | 174.1 | **6.17** | 301 | 240 | 208 | 61.5 | 80 | 80 |

- decode tok/s: **+38% (W2), +48% (W4)** over frozen W1; llama-cli's own
  Generation report corroborates (6.4 → 8.8 → 9.4 t/s).
- read-phase wall (pread_wall_us — the honest overlap metric): 86.0 →
  57.3 → 48.8 ms/token (−33% / −43%). The per-syscall sum (pread_us)
  inflates with workers (86 → 109 → 174 ms) precisely because reads
  overlap — expected for parallel issue; it is NOT the wall metric.
- effective read bandwidth by wall: 3.50 → 5.25 → 6.17 GB/s, recovering
  most of E3B's interleaving gap (standalone ceiling 4.10 @1 / 6.68 @2 /
  8.01 @4 GB/s).
- SSD bytes/token and cache behavior are IDENTICAL across arms (301 MB/tok,
  208 lookups, 61.5% hit, 80 misses, 80 evictions) — parallel reads change
  only arrival timing, not routing, cache policy, or traffic.

### Correctness

- EXIT=0 all arms; 0 error/assert/MISMATCH lines in any arm output.
- Retrieval equivalence: **0/173,670 non-ok rows** in every arm
  (retr.csv bytes_match_tensor=mode ok) — exact GGUF slices loaded.
- Generated output byte-identical between W1 and W4 (pre-banner text,
  `--simple-io` clean comparison) — same seed/temp, same tokens.
- Bounded E4 (8×512, Metal): **PPL 7.2601 at workers=1 AND workers=4** —
  exact preservation of the idsync-fix Metal quality reference; routing
  semantics and model math untouched (env-gated scheduling only).
- Phase 7 invariants: stats accounting consistent across arms
  (identical cache columns; pread_wall == pread_us at W1 as required;
  parallel arms show expected overlap inflation, invariant-preserving).

## Findings

1. **Bounded parallel expert reads materially improve corrected Metal
   decode** using existing machinery: +38% (W2) / +48% (W4) tok/s, read
   wall −33% / −43%.
2. **Diminishing returns beyond W2**: W2 captures most of the gain
   (+1.38×), W4 adds +0.10× more; consistent with E3B's concurrency curve
   (6.68 → 8.01 GB/s at 2 → 4 workers).
3. **Zero cost to correctness/quality**: byte-identical output, 0 retr
   mismatches, E4 PPL exactly preserved, cache/routing behavior unchanged.
4. **The E3B interleaving gap is largely closed**: live wall bandwidth
   rose 3.50 → 6.17 GB/s at W4, approaching the standalone ceiling — the
   remaining gap (6.17 vs 8.01) is residual serialization, not read
   throughput.

## Classification

**Material improvement confirmed (schedule-only change, env-gated).**
Parallel/overlapped expert reads reduce decode pread wall on the corrected
Metal baseline: decode 6.41 → 9.47 tok/s (+48% at workers=4), read wall
86.0 → 48.8 ms/token, effective bandwidth 3.50 → 6.17 GB/s, with exact
correctness (byte-identical output, 0 retr mismatches) and exact E4 quality
(PPL 7.2601 both arms). Recommended operating point from this evidence:
**workers=2** (best gain-per-worker, +38%, leaves headroom for the live
server's other traffic); workers=4 if decode latency is the priority
(+48%).

## Problems / limitations

- Workers>1 inflates the per-syscall pread_us sum (reads overlap); the
  report uses pread_wall_us as the wall metric and states both. Any future
  summary tooling should prefer pread_wall for parallel arms.
- The bounded arms use llama-cli `-n 48` (31 decode steps) for speed; the
  E4 8×512 gate confirms quality at scale. Live-server behavior at W4 was
  not re-measured in this phase (E5 used the frozen default); the E5
  server config remains workers=1 (unchanged) until promotion is
  authorized.

## Decisions

- No source changes; the existing env-gated machinery was exercised
  (default 1 = frozen path unchanged).
- Measured improvement recorded; recommended operating point workers=2–4
  pending a live-server validation (not run here).
- Broader compute/kernel optimization NOT started (directive).

## Next Phase

- Optional: promote `KIMI_EXPERT_READ_WORKERS=2` (or 4) into the live
  server config and re-run the E5 deployment turn measurement (expected
  decode 6.4 → ~8.8–9.5 tok/s equivalent) — pending authorization.
- E3D candidates (not started): async read/compute overlap beyond the
  pool, prefetch; per-step Metal graph-build/launch overhead (E3A
  secondary finding, 63.8 ms/step vs CPU).

## Reproduction

    # arms (only KIMI_EXPERT_READ_WORKERS differs)
    for W in 1 2 4; do
      env KIMI_EXPERT_READ_WORKERS=$W \
          KIMI_STREAM_STATS_FILE=benchmarks/results/phase-11/e3c/w$W-stats.csv \
          KIMI_STREAM_RETR_FILE=benchmarks/results/phase-11/e3c/w$W-retr.csv \
          KIMI_STREAM_CACHE_LAYERS_FILE=benchmarks/results/phase-11/e3c/w$W-layers.csv \
          KIMI_STREAM_EXPERTS=naive KIMI_EXPERT_CACHE_MODE=zerocopy \
          KIMI_EXPERT_CACHE_MB=4096 KIMI_STREAM_METAL_STAGE=1 \
          KIMI_STREAM_E2_DIRECT_PLACE=1 \
          llama.cpp/build-metal/bin/llama-cli -m models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
          -c 4096 -f benchmarks/prompts/phase-11-e5-engineering.md -n 48 \
          --temp 0 --seed 7 --no-display-prompt --no-conversation --single-turn -ngl 999 < /dev/null
    done

    # E4 quality gate (8x512) at workers=1 and workers=4
    tools/phase11_e4_perplexity.sh metal benchmarks/results/phase-11/e3c/e4-w1 8 512
    KIMI_EXPERT_READ_WORKERS=4 tools/phase11_e4_perplexity.sh metal benchmarks/results/phase-11/e3c/e4-w4 8 512
    # -> PPL 7.2601 both arms

Artifacts: `benchmarks/results/phase-11/e3c/` (w1/w2/w4 stats+retr+layers
CSVs, arm outputs, e4-w1/e4-w4 perplexity results). Report: this file.
ROADMAP updated. Stopped for review.
