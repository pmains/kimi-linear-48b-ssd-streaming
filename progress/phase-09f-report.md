# Phase 9F Report — Per-Kind Pipelined Repack

## Status

**PARTIAL — implementation complete, correctness proven, mechanism
measured, 9E end-to-end prediction falsified.** The smallest per-kind
pipelined repack design from the 9E gate is implemented and byte-identical
at workers=1 vs frozen Phase 8 and at workers=4 vs workers=1 (moe.csv md5
matches the frozen baseline, retr.csv/act.bin/cache_layers.csv
byte-identical, 0 retrieval mismatches, phase07 invariants PASS on all 27
ladder runs). The measured read/repack overlap is **40–45 ms/step at W=2/4**
(> the 9E prediction of 20–30), and the load wall drops ~54 ms/step vs the
same-session frozen path. **However, end-to-end decode tok/s does not beat
the archived Phase 9D numbers at the recommended operating point (coding @
4 GB, W=4: 0.99×). Per requirement 6, the 9E model's end-to-end prediction
is treated as falsified/overoptimistic; the implementation is NOT further
complicated to chase it.** Stopped before promotion to the live runtime.

Root cause of the falsification (evidence-backed, not hand-waving): the 9F
session's cached-config reads were ~55% slower than the 9D session on the
IDENTICAL frozen workers=1 path (read wall 75.9–80.7 ms/step vs ~50 ms in
the 9D ladder; same binary path, bracketed within-session). The pipelining
hid ~40 ms/step of repack exactly as designed, but the slower I/O
environment offset the visible gain in the cross-session comparison. Within
its own session, pipelined W=4 cuts the load wall to 110.5 ms/step vs
~165 ms for the frozen path (−33%) — the mechanism and the 9E load-wall
model hold; only the absolute-vs-9D tok/s expectation failed.

## Objective

Implement the smallest per-kind pipelined repack design from the 9E gate:
with KIMI_EXPERT_READ_WORKERS>1, start the layer's kind-major pread batch
once and interleave per-kind waits with each kind's repack/placement stage,
so kind k's stage overlaps the in-flight reads of kinds > k. Preserve
workers=1 as the frozen Phase 8 byte-identical control. Fix the
instrumentation for overlap before benchmarking. Benchmark W=2/4/8 with
bracketed baselines; compare vs frozen Phase 8, Phase 9D, and the 9E
prediction; select the worker count by end-to-end tok/s. No other
optimization.

## Changes

### Dev tree (`llama.cpp`, worktree on `5472cc2e5` + 9C/9D, commit `caea707b7`)

- `src/llama-expert-stream.h` / `.cpp`:
  - Pool: workers notify the completion CV on EVERY job completion;
    waiters now track the longest fully-completed **prefix** of the
    kind-major job array (`pool_done_prefix_` + per-job completion flags),
    not a raw completion count. **This is the fix for a real correctness
    bug found by the 9F correctness pair**: jobs complete out of order, so
    a count threshold (N0) can be reached with a kind-1 job completed while
    a kind-0 job is still in flight — the caller then repacks unread
    staging (short-pread MISMATCHes, corrupted weights, divergent routing).
    The full-batch wait used by 9D was immune (it requires ALL jobs); only
    the new per-kind thresholds exposed it. The bug was caught by the
    correctness-first protocol before any performance claim (see Problems).
  - New `pread_batch_start()` / `pread_batch_wait(n_done, wall)`; the old
    `pread_batch_issue()` = start + wait(njobs) (identical semantics for
    the legacy/naive paths and workers=1).
  - `zc_place` Phase D: workers<=1 (or ≤1 job) keeps the frozen Phase 8/9D
    sequence verbatim (issue all → rows all → repack all). Workers>1: start
    the one kind-major batch; per kind 0..2 — wait(prefix), emit that kind's
    rows (same job order), run that kind's existing repack+placement.
    Row order (moe/retr/pread trace) stays kind-major/slot in both paths.
    Temp-failure path drains the in-flight batch before the legacy fallback.
- `src/llama-expert-stream-exec.cpp`:
  - `p9c_row` gains a `load_hidden_us` column (after `load_other_us`).
  - Per-layer: `ld_hidden = max(0, pread_wall+repack+place+sync − load_wall)`;
    `ld_other` clamped at 0 — overlap is measured explicitly, never a
    negative residual.
  - stats.csv gains trailing column 31 `hidden_us` (per-step overlap,
    same max(0, …) identity); `build_us`/`other_us` clamped; header comment
    documents the Phase 9F accounting.
- `tools/phase07_summarize.py`: reads the optional `hidden_us` column;
  invariant updated to the overlap identity
  `components + other == total + hidden` (both ≥ 0), plus a guard that
  flags hidden > 2 ms when reads are sequential (workers=1). Verified
  backward-compatible on the 9D ladder artifacts.
- New `tools/phase09f_run_ladder.sh` (same bracketed protocol as 9D) and
  `tools/phase09f_analyze.py` (adds hidden/overlap metrics, 9D
  side-by-side, 9E-prediction comparison).

Scope honored: no prefetch, no cache-policy change, no MXFP4, no
storage-layout change, no coalescing, no unrelated refactoring. The legacy
cached-miss and naive paths are untouched (uncached control therefore shows
no overlap — used as the unpipelined control).

## Results

### Correctness (established before any performance claim)

| check | w1 (workers=1) | w4 (workers=4, pipelined) |
|---|---|---|
| moe.csv md5 vs frozen Phase 8 | `da45ab…e5c` ✓ | `da45ab…e5c` ✓ |
| retr.csv vs w1 | — | byte-identical ✓ |
| act.bin (generated output) vs w1 | — | byte-identical ✓ |
| cache_layers.csv vs w1 | — | byte-identical ✓ |
| retrieval MISMATCH rows | 0 | 0 |
| phase07 invariants | PASS (0 violations) | PASS (0 violations) |

Correctness pair: coding @ 4 GB, ctx 4096, seed 1, temp 0, zerocopy mode,
128 tokens, `benchmarks/results/phase-09f/correctness/{w1,w4}`. All 27
ladder runs also pass phase07 invariants; coding/reasoning ladder runs all
match the frozen moe.md5s and the bracketed retr.csv.

### Ladder (bracketed contemporaneous baselines; N_TOKENS=128, seed 1)

Decode steady (after 8-step warmup), per (config, W):

| config | W | tok/s | base tok/s | vs frozen | ms/step | read wall | repack | place | **hidden** | load wall | MB/tok | CPU% | mem MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| coding-cap4 | 2 | 5.89 | 4.37 | 1.35× | 169.7 | 76.5 | 80.7 | 8.2 | **43.1** | 112.1 | 284.7 | 84 | 5585 |
| coding-cap4 | 4 | 5.93 | 4.53 | 1.31× | 168.6 | 77.4 | 72.4 | 10.3 | **40.0** | 110.5 | 284.7 | 88 | 5585 |
| coding-cap4 | 8 | 5.51 | 4.66 | 1.18× | 181.5 | 88.5 | 80.0 | 7.8 | **42.7** | 123.8 | 284.7 | 91 | 5585 |
| reasoning-cap8 | 2 | 5.94 | 4.57 | 1.30× | 168.2 | 75.1 | 59.0 | 27.4 | **44.8** | 107.9 | 192.8 | 84 | 9677 |
| reasoning-cap8 | 4 | 5.62 | 4.88 | 1.15× | 178.0 | 80.8 | 55.8 | 29.6 | **44.6** | 112.9 | 192.8 | 84 | 9677 |
| reasoning-cap8 | 8 | 6.08 | 4.72 | 1.29× | 164.4 | 77.7 | 48.3 | 22.7 | **34.9** | 105.6 | 192.8 | 89 | 9677 |
| uncached | 2 | 2.69 | 2.46 | 1.09× | 372.0 | 64.2 | 252.7 | 0 | **0.0** | 319.6 | 850.1 | 91 | 1485 |
| uncached | 4 | 2.52 | 2.52 | 1.00× | 396.6 | 52.9 | 287.4 | 0 | **0.0** | 343.3 | 850.1 | 99 | 1485 |
| uncached | 8 | 2.74 | 2.50 | 1.10× | 364.7 | 47.1 | 261.1 | 0 | **0.0** | 311.2 | 850.1 | 111 | 1485 |

(hidden = stats.csv col 31, per-step max(0, pread_wall+copy+sync − total);
load wall = Σ per-layer load_wall_us from the p9c trace; all values decode
steady. Correctness: moe==frozen YES on all coding/reasoning cells, retr==w1
YES on all cells.)

### Comparison vs frozen Phase 8 / Phase 9D / 9E prediction

| config | W | 9F vs frozen (same session) | 9D vs frozen (9D session) | 9F vs 9D (archived, cross-session) | 9E predicted hidden ms/step | measured hidden |
|---|---:|---:|---:|---:|---:|---:|
| coding-cap4 | 2 | **1.35×** | 1.20× | 1.04× | 29.7 | 43.1 |
| coding-cap4 | 4 | **1.31×** | 1.15× | 0.99× | 20.4 | 40.0 |
| coding-cap4 | 8 | 1.18× | 1.16× | 0.96× | 10.7 | 42.7 |
| reasoning-cap8 | 2 | **1.30×** | 1.17× | 1.06× | 27.5 | 44.8 |
| reasoning-cap8 | 4 | 1.15× | 1.23× | 0.94× | 19.0 | 44.6 |
| reasoning-cap8 | 8 | 1.29× | 1.22× | 1.04× | 9.6 | 34.9 |
| uncached | 2–8 | 1.00–1.10× | 1.09–1.12× | 0.85–0.95× | (n/a: naive path not pipelined) | 0.0 |

Within-session, pipelined W=2/4 beats the frozen path by more than 9D's
W=2/4 did in its session (coding 1.31–1.35× vs 1.15–1.20×), and the
measured overlap (40–45 ms/step) exceeds the 9E prediction (20–30) at every
worker count. But the archived cross-session comparison — the "over Phase
9D" bar in requirement 6 — is 0.94–1.06×, with the recommended operating
point (coding W=4) at 0.99×. **The ≥10% end-to-end bar over 9D is not met.**

The session confound is directly evidenced: the 9F session's frozen
workers=1 runs (identical code path to 9D's w1) show read walls of
75.9–80.7 ms/step vs ~50 ms in the 9D session, and repack ~10–20% higher —
the whole session ran slower on I/O and CPU. The unpipelined uncached
control confirms the mechanism adds no I/O cost: its read walls (47–64 ms)
match 9D's session and its within-session speedups (1.00–1.10×) match 9D's
(1.09–1.12×).

### Worker-count selection (by end-to-end decode tok/s)

coding @ 4 GB: **W=4 (5.93) ≈ W=2 (5.89) > W=8 (5.51)**. reasoning @ 8 GB:
W=8 (6.08) > W=2 (5.94) > W=4 (5.62) — noisy cell (its bracketed base was
anomalously high at 4.88–4.91 vs 4.47–4.72 for the other brackets; the
absolute W=4 run at 5.62 sits below its neighbors in both sessions).
Recommendation: **keep W=4** (best coding throughput, plateau point,
matches 9D's recommendation; W=2 is equivalent and cheaper on threads;
W=8 is not justified).

## Problems

- **9F ordering bug (found by the correctness pair, fixed before any
  benchmark):** per-kind waits on a raw completion count returned while a
  kind-0 job was still in flight (jobs complete out of order: gen-11 trace
  showed the N0=15 wait satisfied by {0–13, 15} with job 14 unfinished),
  so the kind-0 repack read unread staging → short-pread MISMATCH rows,
  garbage weights, and divergent routing from decode step 1 (moe.csv
  mismatch, act.bin mismatch). Fixed with the prefix-completion
  accounting; correctness pair then passed byte-identical. Recorded here
  rather than erased, per project rules.
- **9E model falsified (end-to-end).** The 9E gate predicted +11.8% tok/s
  over 9D at coding W=4. Measured: 0.99×. The overlap mechanism delivered
  MORE than predicted (40 vs 20 ms/step hidden), and the pipelined load
  wall (110.5) matches the 9E model's pipe-load prediction (114.4), but the
  model implicitly assumed the read wall stays at the 9D session's ~50
  ms/step; this session's reads ran at ~77 ms/step on the identical frozen
  path, offsetting the gain in the cross-session comparison. The model's
  load-wall mechanics are confirmed; its absolute tok/s expectation was
  overoptimistic for this environment.
- **Cross-session noise (inherited):** absolute tok/s varies with ambient
  I/O/CPU state; the bracketed protocol controls within-session drift only.
  The 9F-vs-9D comparison should be read as ±5% noise except where the
  within-session ratios agree (they do).
- **reasoning W=4 cell is noisy** (see worker-count section); no conclusion
  is drawn from it alone.

## Decisions

- **Implement exactly the 9E-recommended mechanism**, with the prefix-
  completion fix for the out-of-order-completion hazard. workers=1 is
  byte-identical to frozen Phase 8/9D (requirement 1: preserved).
- **Instrumentation fixed before benchmarking** (requirement 3): overlap is
  measured explicitly (hidden_us per step and per layer), residuals clamped,
  invariants updated — no false negative-residual failures (all 27 runs
  PASS).
- **Requirement 6 applied: the 9E end-to-end prediction is falsified at the
  recommended operating point (0.99× vs 9D).** The implementation is NOT
  complicated further to chase it (no extra pipelining depth, no policy
  changes, no I/O tuning). The as-built change is kept: it is correct,
  minimal, adds the measured overlap, and does not regress the frozen or
  unpipelined paths.
- **Worker count: 4** (end-to-end tok/s; W=2 equivalent; W=8 not
  justified). Do not ship 8.
- **Do not promote to the live runtime** (per instruction — stop before
  promotion). The live server continues on the frozen bundle.

## Next Phase

Phase 9 is parked. The measured state: parallel read + pipelined repack
hide the read wall and most of the repack; the residual bottleneck is the
uncovered down-kind stage plus the read wall itself under slow-I/O
conditions. The 9E load-wall model is confirmed, so the next bottleneck
gate (if any) should target either (a) fewer miss bytes (MXFP4/layout —
later roadmap phases, not authorized now) or (b) reducing the read wall
under load. Any further work requires a gate decision; the falsified
end-to-end prediction argues for re-baselining predictions against
same-session controls before committing to another optimization.

## Reproduction

```bash
# Correctness pair (must match frozen Phase 8 moe md5 da45ab…e5c):
CTX=4096 KIMI_PHASE7_INSTR=1 KIMI_EXPERT_CACHE_MODE=zerocopy \
  KIMI_EXPERT_CACHE_MB=4096 KIMI_EXPERT_READ_WORKERS=1 \
  KIMI_PHASE9C_TRACE=benchmarks/results/phase-09f/correctness/w1-layer.csv \
  tools/phase04_run_streamed.sh benchmarks/results/phase-09f/correctness/w1 \
  benchmarks/prompts/phase-03-coding-lru.md 128 1 naive
# same with KIMI_EXPERT_READ_WORKERS=4 and w4; then:
md5 -q benchmarks/results/phase-09f/correctness/w{1,4}/moe.csv   # both da45ab…e5c
diff benchmarks/results/phase-09f/correctness/w1/retr.csv \
     benchmarks/results/phase-09f/correctness/w4/retr.csv        # identical
python3 tools/phase07_summarize.py benchmarks/results/phase-09f/correctness/w4

# Ladder (bracketed contemporaneous baselines):
tools/phase09f_run_ladder.sh benchmarks/results/phase-09f/ladder

# Analysis (vs frozen, 9D archive, 9E predictions):
python3 tools/phase09f_analyze.py benchmarks/results/phase-09f/ladder \
    --correctness benchmarks/results/phase-09f/correctness
python3 tools/phase07_summarize.py --ladder benchmarks/results/phase-09f/ladder
```

Environment: llama.cpp dev tree, commit `caea707b7` (9F on `5472cc2e5` +
9C/9D work), Release build, CPU repack + Metal, `-ngl 0`, ctx 4096, seed 1,
temp 0, zerocopy cache mode, `KIMI_EXPERT_READ_WORKERS ∈ {1,2,4,8}` (1 =
frozen path). Model `models/kimi-linear/…-Q4_K_M.gguf` (unchanged). Live
runtime untouched.
