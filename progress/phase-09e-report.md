# Phase 9E Report — Pipelined Repack Feasibility Gate

## Status

**PASS (selection gate) — GO for pipelined repack at the recommended worker
counts.** Trace-grounded simulation of the 9D pool schedule (FIFO job grab,
per-pread latencies from the 9D `.preads` files, calibrated to the measured
read wall) predicts **+11.4–11.8% decode tok/s at W=4** (coding @ 4 GB:
+20.4 ms/step; reasoning @ 8 GB: +19.0 ms/step) and **+15–17% at W=2**
(+27.5–29.7 ms/step). Per the decision rule (>10% = GO), this is a **GO**.
Analysis only — nothing implemented. No MXFP4, storage-layout, cache-policy,
prefetch, or other optimization work was started.

Why the gain exists: kind-0 (up) jobs are issued first in the kind-major
batch, are grabbed first by the pool, and complete ~1 ms/layer before the
batch wall (R0 = 1.02 ms vs wall = 1.97 ms/layer at W=4, coding). The
gate+down read tail is therefore ~0.95 ms/layer (~25 ms/step) during which
the up-kind repack+placement can run on the main thread while the pool
threads are still blocked in pread. The current 9D implementation instead
waits for the whole batch before any repack.

## Objective

Determine whether repack can begin for completed expert reads while other
expert reads are still in flight, without changing model semantics or
cache/LRU behavior; estimate the realistic decode gain from the 9D traces;
identify the smallest implementation and its risks. Stop before
implementation.

## Method

- Read the 9D implementation (`zc_place` Phase D, `pread_batch_issue`, the
  worker pool, `load_layer` staging) in `src/llama-expert-stream.{h,cpp}`.
- Replayed the 9D ladder traces
  (`benchmarks/results/phase-09d/ladder/{coding-cap4,reasoning-cap8,uncached}/w{2,4,8}`):
  per-pread latency + bytes from `p9c-layer.csv.preads`, per-layer timings
  from `p9c-layer.csv`. Joined by execution order (the `.preads` first
  column is the global pread sequence number, not the decode step); the
  prefill block is skipped and each decode moe row consumes 3×(misses) for
  the zc path / 3×(n_slots) for the naive path. **All joins exhaust the
  file exactly** (verified per config).
- Model: W workers, FIFO grab, job duration = measured syscall latency →
  per-kind completion times R_up/R_gate/R_down and the batch wall.
  Sequential (current 9D): `load = wall + Σ stage_k`. Pipelined:
  `t = max(R_k, t) + stage_k` per kind in order, with stage_k = repack+place
  for kind k, split by read bytes (up/gate/down ≈ 31/31/38%).
- Calibration: simulated wall / measured `pread_wall_us` = 0.99–1.00
  (median) at W=2/4 on the zc configs — the FIFO model reproduces the
  measured read wall.

Tool: `tools/phase09e_repack_overlap.py`; results:
`benchmarks/results/phase-09e-overlap-sim.json`.

## Results

### Predicted decode gain (from 9D ladder traces)

| config | W | gain ms/step (mean / median / p75) | predicted tok/s gain |
|---|---:|---:|---:|
| coding-cap4 | 2 | 29.7 / 28.7 / 36.9 | **+16.6%** |
| coding-cap4 | 4 | 20.4 / 19.0 / 26.0 | **+11.8%** |
| coding-cap4 | 8 | 10.7 / 8.2 / — | +6.2% |
| reasoning-cap8 | 2 | 27.5 / 25.8 / — | **+15.5%** |
| reasoning-cap8 | 4 | 19.0 / 15.8 / — | **+11.4%** |
| reasoning-cap8 | 8 | 9.6 / 5.7 / — | +5.4% |
| uncached (naive) | 2–8 | 39–59 / 20–38 / — | +11.6–17.1% (not an operating point) |

Per-layer structure at W=4 coding: read wall 1.97 ms, R_up 1.02, R_gate
1.42, R_down 1.92; sequential load 5.26 ms → pipelined 4.40 ms/layer
(−16%). The gain is tail-limited (up stage ≈ 1.5 ms vs tail ≈ 0.95 ms), not
stage-limited, and is robust (median ≈ mean, i.e. not straggler-driven).

At the tight-A/B environment (6.20 tok/s, 161.3 ms/step at W=4), the same
+20.4 ms/step corresponds to ≈ **+12.7%**, i.e. ≈ 7.0 tok/s. Cross-check
vs 9C's earlier "+5–10 ms/step" companion estimate: that was a pre-9D
conservative guess; the trace-based model says ~20–30 ms/step at W=2/4.

## Problems

- **The gain is an I/O-scheduling-structural effect, not a cache effect.**
  It depends on the pool grabbing kind-0 jobs first (kind-major job
  construction + FIFO grab) so up reads complete early. If job issue order
  or grab order ever changes, R0 moves toward the wall and the gain
  collapses. The implementation must preserve kind-major job order.
- **W=8 largely kills the lever** (5–6%: "implement only if simple" band)
  because all jobs start in one wave and per-kind completion clusters at the
  wall. W=8 is already not recommended (9D), so this does not change the
  recommendation.
- **9C's prior estimate is stale** (pre-9D, unmeasured); this gate replaces
  it with the trace-grounded model. The model is calibrated but still a
  model — the gate's GO should be confirmed by a correctness-first A/B
  (byte-identity, bracketed baselines) before any performance claim.

## Decisions

- **GO** (decision rule: >10% predicted gain at the recommended W=2/4).
- Keep the per-kind granularity (stage k starts when *all* of kind k's reads
  complete) — matches the existing per-kind pipeline and the 9C constraint
  "do NOT reorder repack before its kind's reads complete". Per-slot
  pipelining would hide more but is a larger change; not authorized here.
- Scope (if/when implemented): `zc_place` Phase D only, plus the legacy
  cached-miss/naive loops only if they share the mechanism trivially;
  prefill (`zc_warm_tail`) untouched; no graph, no numerics, no policy, no
  layout, no prefetch.

## Next Phase

If Pete approves implementation, build it as Phase 9F with the 9D protocol:
correctness pair first (byte-identical moe.md5 vs frozen Phase 8, retr.csv
byte-identity, phase07 invariants with the overlap-aware accounting), then
bracketed A/B at W ∈ {2,4}. Expected: ~+11–13% at W=4, ~+15–17% at W=2.
The residual un-overlapped repack (down-kind stage ≈ 22–24 ms/step at W=4)
remains the next bottleneck after that.

## Q&A (as requested)

1. **What dependencies currently force read completion before repack?**
   One real dependency and one artificial one. Real: repack of kind k reads
   `k.packed` staging, filled only by kind k's preads — kind k's reads must
   complete before kind k's repack. Artificial: `pread_batch_issue` blocks
   on `pool_done_ == pool_njobs_` (all-or-nothing barrier), and the whole
   accounting+repack loop runs after it on the main thread. There is no
   cross-kind data dependency: staging regions are disjoint (single
   `stage_get` allocation, kind-major carve), temps are per-kind, placement
   writes only kind-k persistent slot regions, and reads never touch slots.
   The compute graph is built only after `load_layer` returns.

2. **How much read/repack overlap is possible with the 9D architecture?**
   All reads stay in one batch (unchanged concurrency); only the *waits*
   become per-kind. Up repack+placement (~1.5 ms/layer) hides behind the
   gate+down read tail (~0.95 ms/layer at W=4; ~1.4 ms at W=2), and a small
   part of the gate stage hides behind the down tail. Bound: wall − R_up ≈
   24–37 ms/step (W=4→W=2); the fully overlapped ideal (only the last
   kind's stage exposed) is ~44–47 ms/step at W=4 and is not reachable at
   per-kind granularity.

3. **Realistic ms/step and tok/s improvement (from 9D traces):**
   ~20 ms/step (W=4) / ~30 ms/step (W=2) → ~+11–13% / ~+15–17% decode
   tok/s at the operating points. See Results table.

4. **Smallest implementation:**
   ~60–100 lines in `llama-expert-stream.cpp`:
   - Pool: notify the completion CV on *every* job completion (currently
     only on the last); add `pread_batch_start(jobs)` +
     `pread_batch_wait(n_done)` (wait `pool_done_ >= n_done`), keep
     `pread_batch_issue` as start+wait(njobs) for workers≤1/legacy.
   - `zc_place` Phase D: submit one kind-major batch; loop kinds 0..2 —
     wait(cumulative jobs of kinds ≤ k), emit that kind's accounting rows
     (same job order), run that kind's existing repack+placement loop.
   - `llama-expert-stream-exec.cpp` + `tools/phase07_summarize.py`:
     overlap-aware residual (`ld_other`/`other_us` must not go negative;
     add a hidden-repack metric). Workers=1 keeps the exact frozen path.

5. **Risks:**
   - *Synchronization:* per-kind completion needs the cv change above; the
     known 9D livelock pattern (generation counter) must be preserved.
     LOW-MEDIUM.
   - *Ordering/determinism:* row order (moe.csv routing, retr.csv retrieval,
     pread trace) stays kind-major/slot — byte-identity preserved by
     construction if each kind's rows are emitted before its stage in kind
     order (same relative order as today). Must be verified with the 9D
     md5/byte-identity protocol.
   - *Instrumentation/invariants:* `ld_other = ld_wall − pread_wall − repack
     − place − sync` goes negative when overlap is real (verified at
     `llama-expert-stream-exec.cpp:587`; same for stats.csv `other_us`).
     Phase 7 invariant suite and `phase07_summarize.py` must be adapted
     BEFORE benchmarking, or the suite false-flags. This is instrumentation
     only — no semantic change.
   - *Memory:* none new — same single staging allocation, same temps, same
     pool; peak unchanged.
   - *I/O:* unchanged concurrency (same pool, same jobs); repack is CPU
     work on the main thread while pool threads are syscall-blocked. No new
     SSD pressure; memory-bandwidth contention negligible (~10 GB/s vs
     ~100 GB/s available).
   - *Edge cases:* all-hit layers (0 jobs) must not deadlock the per-kind
     waits; short-pread failure logging must keep its order; pool teardown
     (join) unchanged.

## Reproduction

```bash
# Re-run the gate analysis (no build, no run):
python3 tools/phase09e_repack_overlap.py benchmarks/results/phase-09d/ladder
# → prints the table; writes benchmarks/results/phase-09e-overlap-sim.json
```

Inputs: 9D ladder traces under `benchmarks/results/phase-09d/ladder/`
(captured 2026-08-28, llama.cpp `dd4244304` + 9D changes, worktree dirty,
read_workers ∈ {2,4,8}, seed 1, ctx 4096, zerocopy mode for
coding/reasoning, naive for uncached).
