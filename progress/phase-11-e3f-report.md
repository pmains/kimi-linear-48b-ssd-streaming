# Phase 11 E3F Report — Steady-State Decode Profile of the Promoted W4/8 GiB Live Baseline

> **CORRECTION (2026-09-01, E3G): the "~2.8× the same-quantization CPU
> reference" framing in this report is INVALID.** The E3A CPU reference
> it cites was Q4_K_M, not MXFP4 (cross-quantization); the retained true
> MXFP4 CPU reference (e1-cpu-ref: route 26.09 + expert 18.32 =
> 44.41 ms/token) is at **parity** with this report's promoted Metal
> compute (43.82 ms/token). The E3F step attribution itself (all
> columns below, including the 43.82 ms compute bucket as *measured
> Metal wall time*) is unaffected and remains the authoritative live
> baseline profile; what is withdrawn is the *interpretation* that the
> compute bucket is a Metal-vs-CPU deficit and the resulting
> next-phase candidate #1 (Metal compute efficiency via the 2.8× gap).
> See progress/phase-11-e3g-report.md and
> benchmarks/results/phase-11/e3g/quant-reference-correction.txt.

## Status

**PASS — steady-state decode latency is fully attributed at the promoted
W4/8 GiB production point (90.8 ms/step ≈ 11.0 tok/s through the live
OpenClaw path).** The largest single component remains **expert reads
(pread wall): 33.04 ms = 36.4%**, but it is now per-miss-latency-dominated
(781 us/miss × 39 misses/step; fixed overhead only 7.9%), not
bandwidth-dominated. Combined Metal compute (route/trunk 23.60 ms + expert
20.22 ms = 43.82 ms = **48.3%**) is the largest *bucket*. The third target is
the "other" overhead bucket (8.15 ms = 9.0%), dominated by per-layer routing
ids readback (~5.4 ms/step). Reads remain the single biggest lever, but the
largest opportunity by category is Metal compute — the deferred
compute-side/kernel territory E3A flagged (Metal per-step compute ~2.8× the
CPU reference). [E3G correction 2026-09-01: the ~2.8× claim is invalid —
E3A's CPU reference was Q4_K_M, not MXFP4; same-quantization compute is at
parity (MXFP4 CPU 44.41 vs Metal 43.82 ms/token). The compute bucket is real
Metal wall time but NOT a Metal-vs-CPU deficit; the next-target ranking is
updated below.] No optimization performed (directive). Report + ROADMAP
updated. Stopped for review.

## Objective

E3F (authorized): profile the new promoted W4/8 GiB live baseline. Attribute
steady-state decode latency across routing/trunk compute, expert compute,
expert reads, placement/copy, graph build/dispatch, synchronization, and
other measurable overhead. Identify and quantify the largest remaining
optimization target at the ~9–11 tok/s production operating point. Do not
optimize yet. Reuse existing instrumentation where possible, retain results,
update report/ROADMAP, stop for review.

## Method

**No new runs were needed for the primary attribution** — the promoted live
server already writes the full per-step component columns
(`KIMI_STREAM_STATS_FILE`, Phase 7 observability, enabled in the launchd
wrapper). The **B1 soak run from the E3E promotion verification** (7,554
decode steps through the actual live server at W4/8 GiB, a 7,555-token
sustained generation at steady 11.0 tok/s) is the authoritative steady-state
sample. Cross-checked against A2 (cold, 190 decode steps). A supplementary
`KIMI_PHASE9C_TRACE` llama-cli arm at the promoted config was attempted for
per-layer structure but **failed with a Metal OOM** (the live server already
holds the 8 GiB cache + model on the 24 GB machine; a second 8 GiB instance
cannot fit) — the garbage artifacts were discarded and the live soak data
used instead, which is the correct source for a *live baseline* profile
anyway.

Columns (per decode step): `route_compute_us` (dense trunk
attention/norm/KDA + routing), `expert_compute_us` (expert FFN),
`pread_wall_us` (read-phase wall — the honest overlap metric),
`placement_us` + `repack_us` (placement/copy; E2 direct place → repack = 0),
`build_us_measured` (graph build+alloc/dispatch),
`sync_us`, `other_us` (ids readback + input setup + trace dumps).

## Results

### Steady-state decode step (B1 soak, live W4/8 GiB, n = 7,554)

| component | ms/step | % of step |
|---|---:|---:|
| expert reads (pread wall) | **33.04** | **36.4%** |
| route/trunk compute | **23.60** | **26.0%** |
| expert compute | **20.22** | **22.3%** |
| other (ids readback/setup/trace) | 8.15 | 9.0% |
| placement | 3.58 | 3.9% |
| graph build/dispatch | 2.21 | 2.4% |
| repack (E2 direct place) | 0.00 | 0.0% |
| sync | 0.00 | 0.0% |
| **TOTAL** | **90.80** | 100.0% |

- Components sum to 90.81 ms (residual −0.01 ms — exact attribution).
- SSD: 139.7 MiB/token, effective read bandwidth 4.23 GB/s (wall).
- **Read-wall structure**: `read_wall = 2.62 ms fixed + 0.781 ms/miss`
  (R² via LSQ; fixed = only 7.9% of the read wall) → the 33 ms read wall is
  ~30.5 ms of per-miss latency (39 misses/step), not fixed overhead.
- decode = 1 / 0.0908 s ≈ 11.0 tok/s (matches the server's own 11.0).

### Cross-check: A2 cold (live W4/8 GiB, n = 190)

| component | ms/step | % |
|---|---:|---:|
| expert reads (pread wall) | 36.94 | 40.0% |
| route/trunk compute | 21.18 | 23.0% |
| expert compute | 19.71 | 21.4% |
| other | 8.53 | 9.3% |
| placement | 3.66 | 4.0% |
| build | 2.23 | 2.4% |
| **TOTAL** | **92.26** | 100.0% |

Same ordering; cold-start misses push reads to 40%. Structure is stable.

### Attribution summary (vs the E3C W4 4 GiB baseline)

| component | E3C W4 (4 GiB) | **E3F live (8 GiB)** | Δ |
|---|---:|---:|---:|
| reads (wall) | 48.85 ms (46.3%) | **33.04 ms (36.4%)** | −15.8 ms |
| route/trunk | 22.14 ms (21.0%) | **23.60 ms (26.0%)** | +1.5 ms |
| expert | 18.88 ms (17.9%) | **20.22 ms (22.3%)** | +1.3 ms |
| placement | 6.50 ms (6.2%) | **3.58 ms (3.9%)** | −2.9 ms |
| other | 6.99 ms (6.6%) | **8.15 ms (9.0%)** | +1.2 ms |
| total | 105.6 ms | **90.8 ms** | −14.8 ms |

The 8 GiB cache bought −15.8 ms of read wall; the compute and "other"
overheads are essentially unchanged (route/trunk + expert compute are
budget-invariant, as required).

## Findings

1. **Largest single target: expert reads, 33.04 ms (36.4%).** But its
   character changed with the 8 GiB cache: per-miss latency (0.78 ms/miss)
   now dominates — the read wall is *latency-limited*, not
   bandwidth-limited (effective BW only 4.23 GB/s vs the E3B standalone
   ceiling ~8.01 GB/s @4 workers). Reducing per-miss latency (or further
   cutting misses) is the read-side lever; E3E already showed the
   miss-count curve flattens past ~6 GiB.
2. **Largest bucket: Metal compute, 43.82 ms (48.3%)** — route/trunk 23.60
   (26.0%) + expert 20.22 (22.3%). Budget-invariant and now the majority of
   the step. **[E3G correction 2026-09-01: the "~2.8× same-quantization
   CPU reference" sentence previously here is invalid — E3A's CPU
   reference was Q4_K_M, not MXFP4; retained MXFP4 CPU compute (44.41
   ms/token canonical, e1-cpu-ref) is at parity with this bucket. This
   bucket is real Metal wall time but parity work, not a Metal-vs-CPU
   deficit; see progress/phase-11-e3g-report.md.]**
3. **Third: "other" overhead, 8.15 ms (9.0%)** — dominated by the per-layer
   routing-ids readback + synchronization (26 layers × ~0.2 ms from the E3D
   p9c trace ≈ 5.4 ms/step) plus input setup/trace. A real, non-trivial
   overhead that is not I/O and not compute.
4. Placement (3.58 ms), build (2.21 ms), sync/repack (≈0) are minor.
5. The promoted point's profile is stable across cold (A2) and warm/soak
   (B1) runs — same ordering, reads 36–40%, compute 44–48%.

## Classification

**Profile complete; largest remaining target identified and quantified.**
Ranking by optimization opportunity at the ~11 tok/s production point:

1. **Expert reads (33.04 ms, 36.4%)** — latency-limited per-miss cost;
   lever = per-miss latency / miss count. (Read-side, E3C/E3E territory;
   the 8 GiB cache already captured the bulk of the capacity gain.)
2. **Metal compute combined (43.82 ms, 48.3%)** — the largest *bucket*
   but, per the E3G correction (2026-09-01), **parity work**: retained
   MXFP4 CPU compute (44.41 ms/token canonical) matches it, so there is
   no Metal-vs-CPU deficit to fix. Any future compute-side optimization
   would be baseline-wide (kernels/fusion/quantization), requiring
   separate roadmap authorization, not a Metal-deficit repair. Withdrawn
   as the "compute efficiency via 2.8×" candidate of the original E3F
   next-phase list.
3. **"Other" (8.15 ms, 9.0%)** — per-layer ids readback + sync;
   lever = reduce 26 host readback/sync points per step.

No optimization performed (directive: "Do not optimize yet").

## Problems / limitations

- The supplementary per-layer p9c trace at the promoted config OOM'd (live
  server holds the 8 GiB cache; no room for a second instance on 24 GB).
  The live soak data is the correct authoritative source for a live
  baseline profile, so this is a limitation of *granularity* (per-step vs
  per-layer), not of validity. Per-layer readback estimate (0.206 ms/layer)
  comes from the E3D 4 GiB trace.
- The live server's stats capture does not include
  `KIMI_PHASE9C_TRACE`-style per-layer columns; enabling it would require a
  wrapper change + restart (not done — would disturb the live runtime for a
  profile-only phase).
- B1 is a single long generation (one prompt, one routing pattern); A2
  cross-check covers a second prompt. Multi-prompt steady-state would
  generalize the "other" and compute buckets slightly, but the ordering is
  robust.

## Decisions

- Used the live B1 soak stats.delta as the authoritative steady-state
  profile (no new runs needed; instrumentation already in place).
- Discarded the OOM'd p9c attempt artifacts (not valid data).
- Recorded the attribution artifact
  (`benchmarks/results/phase-11/e3f/live-baseline-profile.txt`).
- No optimization begun (directive). Next-phase candidates ranked in
  Findings/Classification above.

## Next Phase

- E3G candidates as ranked in Findings/Classification **after the E3G
  correction (2026-09-01; see progress/phase-11-e3g-report.md)**:
  1. **Read per-miss latency** (33.04 ms, 36.4%): quantify the 0.78 ms/miss
     floor (SSD queue depth, 4-worker pool vs per-layer serialization) and
     whether per-layer read batching can be tightened. This is now the
     top target — compute is parity work, not a deficit.
  2. **Per-layer ids readback** (~5.4 ms of the 8.15 ms "other" bucket):
     batch or overlap the 26 host readback/sync points per step.
  3. **Metal compute (43.82 ms, 48.3%)**: WITHDRAWN as a Metal-vs-CPU
     deficit target by the E3G falsification (parity with retained MXFP4
     CPU reference). Any compute-side work would be baseline-wide
     (kernels/fusion/quantization) and requires separate roadmap
     authorization.
  3. **Per-layer ids readback** (≈5.4 ms of "other"): batch or overlap the
     26 host readback/sync points per step.

## Reproduction

    # attribution from the retained live soak (B1) — no new run needed
    python3 - <<'PY'
    import csv
    HDR = open("benchmarks/results/phase-11/e3e-promotion/B1/stats.delta").readline().strip().split(",")
    # NOTE: stats.delta rows are headerless; use the stats.csv header below
    PY
    # (full analysis script embedded in this phase's working notes; artifact:
    #  benchmarks/results/phase-11/e3f/live-baseline-profile.txt)

    # live soak reproduction (E3E promotion verification, already retained)
    tools/phase11_e5_deployment.sh benchmarks/results/phase-11/e3e-promotion A1 B1 A2 B2

Artifacts: `benchmarks/results/phase-11/e3f/live-baseline-profile.txt`
(component table + read-wall regression for B1 soak and A2 cold); source
data in `benchmarks/results/phase-11/e3e-promotion/{A1,B1,A2,B2}/stats.delta`.
Report: this file. ROADMAP updated. Stopped for review.
