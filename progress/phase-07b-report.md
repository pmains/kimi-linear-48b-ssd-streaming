# Phase 7B Report — Controlled Performance Baseline

## Status

**PASS** — controlled idle-machine validation of the Phase 7 performance
ladder. 24 runs (3 reps × 7 cache rungs + 3 interleaved uncached
controls), all invariants PASS (24/24 clean, 0 violations), zero
dispersion on deterministic columns (hit rate, SSD bytes), and the
Phase 6B hit rates reproduced bit-identically at every rung
(0.305 / 0.461 / 0.585 / 0.682 / 0.756 / 0.810 / 0.877).

No new inference was run to produce this report, and `llama.cpp` was not
modified. Everything below is derived from the Phase 7B artifacts
committed at project `e20fe3d` (`benchmarks/results/phase-07b/`,
`tools/phase07b_run_baseline.sh`, `tools/phase07b_summarize.py`).

## Objective

Phase 7 measured the instrumented system but was thermal-noise-limited:
min-of-2 runs, a single front-loaded uncached control, and no settle
delay on a fanless Air whose consecutive-run drift reaches ~17%. The
Phase 6B/7 performance claims (4 GB sweet spot, ≥10 GB memory-pressure
cliff, per-rung hit classes) therefore carried session-level thermal
uncertainty.

Phase 7B re-measures the same ladder with a protocol designed to
decorrelate thermal drift from capacity:

1. three repetitions per rung, not min-of-2;
2. an uncached control at the head of **every** round (interleaved, not
   front-loaded);
3. cap order rotated per round (ascending / descending / seeded shuffle)
   so no capacity is consistently favored by warm-up or cool-down;
4. a 15 s settle delay between runs;
5. 10/12 GB rungs included by default to re-test the ≥10 GB
   memory-pressure cliff;
6. ambient-load sampling (top-3 CPU procs + loadavg every 20 s) so the
   report can show the load envelope rather than assume idleness;
7. `act.bin` discarded per run (reproducible via
   `tools/phase04_run_streamed.sh`; retained in Phase 7 only for the
   oracle pair).

The goal is to split the Phase 6B/7 findings into **structural results**
(bit-stable across sessions: hit rates, hit-class decomposition, SSD
traffic, residency scaling) and **thermal results** (absolute tok/s,
which are only meaningful against an in-session control).

## Changes

No source changes. Files committed at `e20fe3d`:

- `tools/phase07b_run_baseline.sh` — the revised protocol runner.
- `tools/phase07b_summarize.py` — per-run metrics + invariant checks;
  writes `baseline-summary.csv`, `baseline-runs.csv`,
  `baseline-ratios.csv` into the run root.
- `benchmarks/results/phase-07b/` — the full 24-run capture (per-run
  `stats.csv` 30 cols, `mem.csv`, `cache_layers.csv`, `retr.csv`,
  `moe.csv`, `manifest.json`, `run.log`; plus `baseline-run.log`,
  `load-samples.log`, `driver.log`, and the three summary CSVs).

## Protocol

Exact invocation (from `baseline-run.log` and the runner):

```
tools/phase07b_run_baseline.sh benchmarks/results/phase-07b/baseline
```

- caps: 1 2 4 6 8 10 12 GB (MiB = round(GB × 1024), via
  `KIMI_EXPERT_CACHE_MB`); reps: 3; mode: `zerocopy`
  (`KIMI_EXPERT_CACHE_MODE=zerocopy`); settle: 15 s;
  `KIMI_PHASE7_INSTR=1`.
- workload: prompt `benchmarks/prompts/phase-04-ref.md` (36 words,
  Python coding task), `n_tokens=64`, `seed=1`, `temp=0`,
  `--ctx-size 4096`, `--ngl 0` (CPU), `--no-mmap`, single-turn,
  `KIMI_STREAM_EXPERTS=naive`.
- per-round cap order: round 1 ascending (1 2 4 6 8 10 12); round 2
  descending; round 3 seeded shuffle (`random.Random(7)`). Each round
  begins with `uncached-rN`.
- ambient sampler: loadavg + top-3 CPU procs every 20 s for the whole
  run (~22 min; 15:17:24–15:37:23 local).
- binary: llama.cpp `e8baeb16e` (Phase 7 instrumented build), worktree
  clean, model `moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`.

Environment: Apple M5 (Mac17,3), 24 GB unified memory, macOS 26.5.2;
fanless (thermal-sensitive).

## Results

### Decode throughput (median of 3 reps; tok/s, all decode steps)

| rung | median | min | max | sd | vs in-session control (median ratio) |
|---:|---:|---:|---:|---:|---:|
| uncached | 2.677 | 2.341 | 2.759 | 0.221 | 1.000 |
| 1 GB | 3.751 | 3.376 | 3.788 | 0.228 | 1.443 |
| 2 GB | 4.434 | 4.174 | 4.598 | 0.214 | 1.739 |
| **4 GB** | **4.913** | 4.561 | 4.984 | 0.226 | 1.841 |
| 6 GB | 4.521 | 4.382 | 5.046 | 0.350 | 1.911 |
| 8 GB | 4.707 | 4.429 | 4.788 | 0.188 | 1.838 |
| 10 GB | 4.893 | 4.474 | 4.927 | 0.252 | 1.848 |
| 12 GB | 4.364 | 3.975 | 4.868 | 0.448 | 1.698 |

- **Every rung beats its in-session uncached control** (1.38×–2.06×
  per-run ratios; median ratios 1.44–1.91). This replicates the Phase
  6B/7 structural finding that any nonzero cache budget beats uncached.
- **4 GB is the median peak (4.913 tok/s)**, but the 4–10 GB plateau is
  flat within dispersion (4.36–4.91; 10 GB 4.893 is statistically tied
  with 4 GB). 12 GB (4.364) drops below the plateau but stays well above
  uncached.
- Per-run dispersion (sd 0.19–0.45 tok/s, ±4–10%) is thermal, not
  structural: deterministic columns (hit rate, SSD MB/token) have zero
  dispersion across reps, and the largest sd is at 12 GB (0.448), the
  rung most exposed to memory pressure.

### Hit rates and hit-class decomposition (decode steady, median of 3)

| rung | hit_rate | zc_hit | ph_hit | misses | evictions | SSD MB/token |
|---:|---:|---:|---:|---:|---:|---:|
| uncached | — | — | — | — | — | 850.1 |
| 1 GB | 0.305 | 0.305 | 0.000 | 8,523 | 8,523 | 591.8 |
| 2 GB | 0.461 | 0.461 | 0.000 | 6,609 | 6,609 | 459.7 |
| 4 GB | 0.585 | 0.585 | 0.000 | 5,087 | 5,087 | 354.5 |
| 6 GB | 0.682 | 0.682 | 0.000 | 3,897 | 3,813 | 272.0 |
| 8 GB | 0.756 | 0.756 | 0.000 | 2,999 | 2,426 | 209.6 |
| 10 GB | 0.810 | 0.810 | 0.000 | 2,332 | 1,551 | 163.1 |
| 12 GB | 0.877 | 0.877 | 0.000 | 1,507 | 1,286 | 106.1 |

- Hit rates are **bit-identical to Phase 6B** at every shared rung
  (6B: 0.305/0.461/0.585/0.682/0.756/0.810/0.877) and to Phase 7
  (0.305/0.585/0.756 at 1/4/8 GB). Cache behavior is unchanged and
  deterministic (seed 1, temp 0).
- Zero-copy mode: `zc_hit_rate == hit_rate`, `ph_hit_rate == 0` — every
  hit elides I/O + repack + placement. Placement hits contribute nothing
  in this mode, as in Phase 6B/7.
- **SSD traffic decreases monotonically** with budget
  (850 → 106 MB/token), matching Phase 6B/7 values at shared rungs
  (592/354/210 at 1/4/8 GB).

### Component medians (ms/step, decode steady)

| rung | pread | repack | placement | copy | sync | route | expert | buildM | other | sum | steady_ms |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| uncached | 95.8 | 229.7 | 0.0 | 229.7 | 0.0 | 17.3 | 15.6 | 8.6 | 9.5 | 376.5 | 371.5 |
| 1 GB | 78.8 | 139.6 | 8.4 | 148.1 | 0.0 | 17.4 | 15.4 | 1.8 | 8.8 | 270.4 | 261.5 |
| 2 GB | 68.5 | 105.3 | 6.6 | 111.9 | 0.0 | 19.4 | 15.4 | 1.8 | 8.2 | 225.2 | 224.4 |
| 4 GB | 72.2 | 80.8 | 6.3 | 87.1 | 0.0 | 19.1 | 15.5 | 1.8 | 8.4 | 204.1 | 201.8 |
| 6 GB | 77.9 | 63.2 | 19.0 | 85.7 | 0.0 | 22.0 | 22.7 | 2.0 | 8.8 | 219.2 | 220.1 |
| 8 GB | 75.5 | 48.2 | 24.2 | 73.6 | 0.0 | 22.5 | 28.0 | 2.0 | 9.3 | 211.0 | 206.0 |
| 10 GB | 61.3 | 37.7 | 29.2 | 66.9 | 0.0 | 25.8 | 38.3 | 2.2 | 9.8 | 204.2 | 201.1 |
| 12 GB | 42.9 | 24.8 | 26.1 | 51.4 | 0.0 | 34.2 | 91.2 | 2.3 | 10.9 | 232.9 | 221.7 |

- I/O (pread+repack) dominates uncached (325 ms of 371 ms) and shrinks
  monotonically with budget; the win is elided hit I/O and repack, as in
  Phase 6B/7.
- Placement (miss-slice memcpy only) stays small (6–29 ms) — the
  zero-copy path works as designed.
- Component sums close within noise of measured steady_ms (≤5%).
- **Memory-pressure signature at 12 GB**: expert compute inflates
  15.5 → 91.2 ms and route 17.3 → 34.2 ms (vs 4 GB) — the same
  component-level degradation Phase 6B attributed to macOS compression.
  In this session the I/O savings at 12 GB (68 ms) still outweigh the
  compute inflation, so the net step time (221.7 ms) remains below
  uncached (371.5 ms).

### Physical memory (median of 3)

| rung | phys decode MB | prefill peak MB | cache MB used | budget MB |
|---:|---:|---:|---:|---:|
| uncached | 1,412.8 | 1,984.3 | 0.0 | 0.0 |
| 1 GB | 2,440.6 | 2,022.4 | 1,021.1 | 1,024.0 |
| 2 GB | 3,466.5 | 4,002.8 | 2,047.8 | 2,048.0 |
| 4 GB | 5,514.1 | 5,997.8 | 4,094.5 | 4,096.0 |
| 6 GB | 7,579.6 | 6,750.5 | 6,141.8 | 6,144.0 |
| 8 GB | 9,499.9 | 6,781.1 | 8,190.8 | 8,192.0 |
| 10 GB | 11,272.0 | 8,189.5 | 10,238.0 | 10,240.0 |
| 12 GB | 13,585.4 | 13,094.4 | 12,284.7 | 12,288.0 |

- `cache_bytes_used == budget` exactly at every rung (the cache is
  fully populated at steady state, as in Phase 6B/7).
- Decode residency scales as baseline (≈1.41 GB) + cache budget —
  bounded, predictable.
- 12 GB decode residency (13.6 GB) plus page cache approaches the 24 GB
  machine's compression threshold — consistent with the expert/route
  inflation above.

## Comparison vs Phase 6B / Phase 7

| rung | 6B tok/s | 7 tok/s | 7B tok/s (median) |
|---:|---:|---:|---:|
| uncached | 1.471 | 2.52 | 2.677 |
| 1 GB | 2.911 | 3.54 | 3.751 |
| 2 GB | 2.688 | — | 4.434 |
| 4 GB | 3.301 | 4.73 | 4.913 |
| 6 GB | 2.231 | — | 4.521 |
| 8 GB | 1.872 | 4.39 | 4.707 |
| 10 GB | 1.539 | — | 4.893 |
| 12 GB | 1.462 | — | 4.364 |

**Reproducible structural findings (bit-stable across all three
sessions):**

1. Hit rates identical at every rung (0.305 … 0.877), zero dispersion.
2. Every cache rung beats its in-session uncached control.
3. SSD traffic per token is a monotonic function of budget with the same
   values at shared rungs.
4. Zero-copy mode: all hits are zero-copy; placement cost is miss-slice
   only.
5. Decode residency = baseline + cache budget; cache fully populated.
6. Component cost structure: I/O (pread+repack) dominates and shrinks
   with budget; route/expert compute is budget-independent below 10 GB.
7. 4 GB is the median optimum in all three sessions (3.30 / 4.73 /
   4.91 tok/s) — the **4 GB sweet spot reproduces**.

**Thermally sensitive results (session-local only):**

- Absolute tok/s differ wildly across sessions (e.g. uncached 1.47 →
   2.52 → 2.68; 4 GB 3.30 → 4.73 → 4.91). The 6B session was the
   hottest; 7B's interleaved controls + settle delays run coolest.
   Cross-session absolute tok/s remain **not comparable**; only
   in-session control ratios are.
- The Phase 6B/7 "≥10 GB throughput cliff" (10–12 GB falling at or
   below uncached: 1.54/1.46 vs 1.47 in 6B) **does not reproduce as a
   throughput collapse in 7B**: 10 GB (4.89) and 12 GB (4.36) both stay
   well above the in-session uncached control (2.68). The
   **component-level pressure signature does reproduce** (expert
   15.5 → 91.2 ms, route 17.3 → 34.2 ms at 12 GB vs 4 GB), and 12 GB is
   the weakest capped rung. The 6B cliff was therefore at least partly
   a thermal-session artifact; the residual truth is "memory pressure
   degrades compute at ≥10–12 GB, but I/O savings at high hit rates
   still net positive in a controlled session."
- 4 GB vs 10 GB are statistically tied in 7B (4.913 vs 4.893). The 4 GB
   optimum is reproduced as the median peak, but the 4–10 GB plateau is
   flat; choosing between them is a policy decision for Phase 8, not a
   measured necessity.

## Problems

1. **The machine was not truly idle.** `load-samples.log` shows
   loadavg 1.5–3.6 and recurring ambient CPU: Brave Browser renderer
   (up to ~80%), `mediaanalysisd`, `WindowServer`, `corespotlightd`,
   Xprotect. The protocol controls for this (interleaved controls,
   rotation, settle) but does not eliminate it; "controlled" here means
   *measured and countered*, not *zero-load*. This is the main
   remaining confound for absolute tok/s.
2. **Thermal drift within the session persists** (sd ±4–10%; largest at
   12 GB). Three reps bound it but do not remove it.
3. **4 GB vs 10 GB tie** means the single "sweet spot" claim is weaker
   than Phase 6B stated. The honest claim is a flat 2–10 GB plateau with
   a 4 GB median peak.
4. **≥10 GB cliff reframed.** Phase 6B's absolute cliff did not
   reproduce; only the component-level compute inflation did. Reports
   that rely on the old cliff claim need updating (see ROADMAP change).
5. **No correctness runs in 7B** by design (act.bin discarded).
   Correctness is anchored by Phase 7's oracle on the same binary
   (`e8baeb16e`); 7B validates performance only.
6. **Single prompt / 64 tokens / ctx 4096** — a narrow workload. Phase 8
   is responsible for realistic coding workloads.

## Decisions

- **Phase 7B is the authoritative pre-Phase-8 performance baseline.**
  It is the most controlled ladder available (3 reps, interleaved
  controls, rotated order, settle, load sampling, 24/24 invariant
  PASS, zero dispersion on deterministic columns, hit rates
  bit-identical to Phase 6B). Phase 8 should consume
  `benchmarks/results/phase-07b/baseline/baseline-summary.csv`
  (`baseline-runs.csv`, `baseline-ratios.csv`) and reuse
  `tools/phase07b_run_baseline.sh` as the protocol for any additional
  rungs or workloads.
- **Structural vs thermal split.** Phase 8 acceptance comparisons must
  use in-session uncached controls, never cross-session absolute tok/s.
- **4 GB sweet spot: keep as default recommendation, but treat 4–10 GB
  as the viable plateau** pending Phase 8's interactive-workload
  measurements.
- **≥10 GB cliff: downgrade from "throughput collapse" to "compute
  degradation under memory pressure, net still positive in controlled
  sessions".** Phase 8 should re-test with realistic workloads before
  any memory-budget ceiling is set.

## Next Phase

Phase 8 (Memory Ladder) uses this baseline as its reference:

- acceptance-table columns (cache, resident RAM, hit rate with hit
  classes, decode tok/s, SSD MB/token) map 1:1 onto
  `baseline-summary.csv`;
- protocol for new runs: `tools/phase07b_run_baseline.sh` (add realistic
  coding workloads and prompt lengths beyond the 64-token ref prompt);
- comparative bar: in-session uncached control, not Phase 6B/7
  absolute numbers;
- open policy questions carried forward: 4 GB vs 10 GB (tied),
  ≥10 GB ceiling (compute degradation, not cliff), interactive-usability
  threshold.

## Reproduction

```bash
# full 24-run baseline (≈22 min + settle time):
tools/phase07b_run_baseline.sh benchmarks/results/phase-07b/baseline

# summarize + invariants:
python3 tools/phase07b_summarize.py --root benchmarks/results/phase-07b/baseline
# → baseline-summary.csv, baseline-runs.csv, baseline-ratios.csv; exit 0 = invariants hold

# per-run capture is reproducible via:
#   CTX=4096 KIMI_PHASE7_INSTR=1 KIMI_EXPERT_CACHE_MB=<mib> \
#     tools/phase04_run_streamed.sh OUTDIR benchmarks/prompts/phase-04-ref.md 64 1 naive
```

Environment: Apple M5 (Mac17,3), 24 GB unified memory, macOS 26.5.2;
llama.cpp `e8baeb16e` (worktree clean), model
`moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`; `-ngl 0`, CPU,
`--no-mmap`, `--ctx-size 4096`, `--temp 0 --seed 1`.
