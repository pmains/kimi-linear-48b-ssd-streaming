# STREAMING_RESULTS.md — Phase 8 Memory Ladder

Authoritative streaming results for
`Kimi-Linear-48B-A3B-Instruct` (Q4_K_M) on the target 24 GB Apple
Silicon Mac (Apple M5, Mac17,3, 24 GB unified memory, macOS 26.5.2,
fanless). Protocol and per-run telemetry: `benchmarks/results/phase-08/`
(driver.log, per-workload baseline-summary/runs/ratios CSVs, per-run
stats/mem/cache_layers/retr/moe CSVs). All runs: seed 1, temp 0,
ctx 4096, `-ngl 0` CPU, `--no-mmap`, 3 reps, interleaved uncached
controls, rotated cap order, 15 s settle, zerocopy cache mode.
llama.cpp `e8baeb16e` (worktree clean).

Three workloads:

| label | prompt | n_tokens | character |
|---|---|---|---|
| ref | `phase-04-ref.md` (36 words) | 64 | short Python function |
| coding | `phase-03-coding-lru.md` (182 words) | 128 | Rust concurrent LRU cache, full impl |
| reasoning | `phase-03-reasoning-pumps.md` (180 words) | 128 | multi-step math problem + generalization |

Deterministic columns (hit rate, SSD MB/token) have **zero dispersion**
across reps in all workloads; every run passed the invariant checks
(48/48 clean).

---

## Acceptance table — decode tok/s (median), in-session uncached control per workload

| Cache | ref | coding | reasoning |
|---:|---:|---:|---:|
| uncached | 2.68 | 2.61 | 2.61 |
| 1 GB | 3.75 | 3.78 | 3.48 |
| 2 GB | 4.43 | 4.57 | 4.10 |
| **4 GB** | **4.91** | **5.39** | 4.58 |
| 6 GB | 4.52 | 5.09 | 4.58 |
| 8 GB | 4.71 | 5.18 | **4.96** |
| 10 GB | 4.89 | 4.82 | 4.51 |
| 12 GB | 4.36 | 4.10 | 3.82 |

Speedup vs in-session uncached control (median ratio):

| rung | ref | coding | reasoning |
|---:|---:|---:|---:|
| 1 GB | 1.44 | 1.52 | 1.31 |
| 2 GB | 1.74 | 1.84 | 1.59 |
| 4 GB | 1.84 | 2.11 | 1.78 |
| 6 GB | 1.91 | 1.95 | 1.91 |
| 8 GB | 1.84 | 2.10 | 1.99 |
| 10 GB | 1.85 | 1.92 | 1.71 |
| 12 GB | 1.70 | 1.69 | 1.53 |

Every cache rung beats its in-session uncached control in every
workload. **The peak is workload-dependent**: ref and coding peak at
4 GB (4.91 / 5.39 tok/s); reasoning peaks at 8 GB (4.96 tok/s). The
Phase 6B/7B "4 GB sweet spot" reproduces for code-generation workloads
but is not universal — the practical range is a 4–8 GB plateau
(4.5–5.4 tok/s).

## Hit rate (decode steady, median; zero dispersion)

| rung | ref | coding | reasoning |
|---:|---:|---:|---:|
| 1 GB | 0.305 | 0.331 | 0.264 |
| 2 GB | 0.461 | 0.507 | 0.428 |
| 4 GB | 0.585 | 0.664 | 0.564 |
| 6 GB | 0.682 | 0.746 | 0.685 |
| 8 GB | 0.756 | 0.793 | 0.769 |
| 10 GB | 0.810 | 0.825 | 0.819 |
| 12 GB | 0.877 | 0.851 | 0.854 |

All hits are zero-copy hits (`zc_hit_rate == hit_rate`, `ph_hit_rate
== 0`). **Coding has the best expert locality** (higher hit rate at
every rung); reasoning has the worst at small rungs but converges by
8–12 GB. The ref workload's hit rates are bit-identical to Phase 6B/7B.

## SSD traffic per generated token (MB, median)

| rung | ref | coding | reasoning |
|---:|---:|---:|---:|
| uncached | 850.1 | 850.1 | 850.1 |
| 1 GB | 591.8 | 569.5 | 627.0 |
| 2 GB | 459.7 | 420.0 | 488.3 |
| 4 GB | 354.5 | 286.3 | 372.4 |
| 6 GB | 272.0 | 217.0 | 269.2 |
| 8 GB | 209.6 | 176.9 | 197.4 |
| 10 GB | 163.1 | 149.2 | 154.6 |
| 12 GB | 106.1 | 127.4 | 124.5 |

SSD traffic decreases monotonically with budget in all workloads
(uncached is identical by construction: every slice is a pread). At
4 GB a cached token reads ~0.29–0.37 GB from SSD vs ~0.85 GB uncached
(~55–66% reduction).

## Resident RAM (decode steady, phys MB, median)

| rung | ref | coding | reasoning |
|---:|---:|---:|---:|
| uncached | 1,413 | 1,486 | 1,464 |
| 1 GB | 2,441 | 2,507 | 2,491 |
| 2 GB | 3,467 | 3,527 | 3,528 |
| 4 GB | 5,514 | 5,588 | 5,589 |
| 6 GB | 7,580 | 7,631 | 7,636 |
| 8 GB | 9,500 | 9,610 | 9,641 |
| 10 GB | 11,272 | 11,416 | 11,532 |
| 12 GB | 13,585 | 13,029 | 13,249 |

Decode residency = baseline (≈1.4–1.5 GB) + cache budget, exactly.
`cache_bytes_used == budget` at every rung in every workload. Prefill
peak is workload-dependent at the top rung (ref 13.1 GB vs 7.2 GB for
the realistic workloads at 12 GB — a prefill-batching transient; decode
residency is the consistent metric).

## What limits performance (component ms/step, decode steady, median)

| rung | workload | pread | repack | placement | route | expert | buildM | other |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| uncached | ref | 95.8 | 229.7 | 0.0 | 17.3 | 15.6 | 8.6 | 9.5 |
| 1 GB | coding | 75.5 | 132.2 | 8.2 | 19.3 | 15.8 | 1.9 | 8.9 |
| 4 GB | coding | 66.2 | 65.6 | 5.7 | 20.1 | 15.9 | 1.9 | 8.7 |
| 8 GB | coding | 64.1 | 41.4 | 24.1 | 26.1 | 25.6 | 2.2 | 9.6 |
| 12 GB | coding | 53.7 | 31.5 | 31.7 | 34.9 | **77.7** | 2.6 | 12.1 |
| 12 GB | reasoning | 52.0 | 31.4 | 28.1 | 35.4 | **97.5** | 2.6 | 13.2 |

- ≤ 4 GB: pread + repack (the miss path) dominate — the win from
  caching is elided I/O + elided hit repack.
- 6–10 GB: diminishing I/O returns; route/expert compute starts rising
  with residency (memory pressure).
- ≥ 10 GB: expert compute inflates sharply (15.9 → 77.7–97.5 ms at
  12 GB) — the Phase 6B memory-pressure effect reproduces at the
  component level in all workloads. It degrades but does not collapse
  throughput below uncached.
- Placement and build are negligible (zero-copy path works).

## Answers to the Phase 8 acceptance questions

1. **Minimum practical resident footprint**: ~1.4 GB baseline + a 1 GB
   cache (≈2.5 GB total) already yields 1.3–1.5× over uncached
   (3.5–3.8 tok/s). The uncached mode itself (1.4 GB) is usable but
   slow (2.6 tok/s, ~385 ms/token).
2. **Cache size with useful locality**: hit rate 0.26–0.33 at 1 GB,
   0.56–0.66 at 4 GB, 0.85+ at 12 GB; coding benefits most (0.66 at
   4 GB). Diminishing returns after 8 GB.
3. **SSD traffic per token**: 850 MB uncached → 106–127 MB at 12 GB;
   ~0.29–0.37 GB at the recommended 4 GB budget.
4. **Throughput at each budget**: 2.6 uncached → 3.5–5.4 tok/s across
   the ladder; 4–8 GB gives 4.5–5.4 tok/s.
5. **Usable for interactive coding?**: yes, marginally — 4–8 GB gives
   4.5–5.4 tok/s (~185–220 ms/token) with sensible output (the coding
   workload produces a real, coherent Rust LRU implementation). Fast
   enough for streaming completions with patience; not snappy. Uncached
   is too slow for interactive use.
6. **What limits performance**: miss-path I/O (pread+repack) at small
   budgets; memory-pressure compute inflation (expert) at ≥ 10 GB;
   absolute tok/s is thermally bounded on this fanless machine.
   Placement/build are not bottlenecks.

## Bottom line

Expert streaming is **practically useful** on the 24 GB Air: a 4–8 GB
expert cache (≈5.5–9.6 GB resident) delivers 1.8–2.1× uncached
throughput, 4.5–5.4 tok/s, with bounded residency and monotonic SSD
savings. 4 GB is the best single default for code workloads; reasoning
workloads favor 8 GB. Above 10 GB, memory pressure eats the gains.
