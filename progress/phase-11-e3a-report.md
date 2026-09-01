# Phase 11 E3A Report — Corrected-Metal Decode Regression Diagnosis

## Status

**PASS — regression explained, no optimization performed.** The corrected
Metal decode step is **storage/cache-bound** (dominant), mixed with
Metal-specific compute inefficiency (secondary). The routed-expert-ID
synchronization fix (the E4 root-cause fix) is **exonerated**: it costs
~0.01 % of decode step time. The ~24 → ~3 tok/s "regression" is largely an
artifact of the pre-fix baseline being a degenerate no-work path, plus the
real cost of routing-driven SSD traffic and per-step Metal graph execution.
Classification: **storage/cache-bound** (pread 51.6 % of step; + placement
copy 6.8 % = 58.4 % storage/placement), with compute secondary
(route 17.5 % + expert 14.1 % = 31.6 %). No production behavior modified.
Stopped for review.

## Objective

E3A (authorized): determine where the corrected-Metal decode regression
(~24 tok/s on the earlier broken Metal path → ~3.1 tok/s corrected) comes
from, focusing on whether the routed-expert-ID synchronization fix
introduced CPU↔GPU barriers/serialization around routing, SSD reads, expert
placement, or Metal execution. Classify the dominant cause
(synchronization / storage-cache / compute / mixed), quantify in ms/token,
reuse existing instrumentation, do not optimize.

## Method

No new instrumentation needed — the E5 live-server capture
(`benchmarks/results/phase-11/e5-deployment/stats.csv`, streamer per-step
timers, env-gated Phase 7/9D accounting) already records, per decode step:
`pread_us`, `copy_us`, `sync_us`, `route_compute_us` (dense trunk
attention/norm/KDA + routing), `expert_compute_us`, `build_us`, `total_us`,
plus pread bytes/calls and cache lookups/hits/misses.

- Corrected Metal: E5 stats.csv, 360 decode steps, 1 token/step (all 8 runs
  A1–B4 pooled; identical 1.00 tok/step across every run).
- CPU reference: frozen K1 CPU cached MXFP4
  (`benchmarks/results/phase-k1/full/coding-cap4/b8-A-after/stats.csv`,
  127 decode steps) — the retained same-quantization CPU path.
- Pre-fix degenerate baseline: no stats.csv retained from the pre-fix path;
  its behavior is documented in the retained E1 report
  (`progress/phase-11-e1-report.md`): decode steady **hit_rate 1.000,
  SSD 0.00 MB/token** (all routed ids read back as 0 → expert 0 everywhere →
  n_slots=1, nothing to load) at 24.3 tok/s.

Analyzer retained: `tools/phase11_e3a_analyze.py`; canonical output:
`benchmarks/results/phase-11/e3a/decode-decomposition.txt`.

## Results

### Corrected Metal decode step (E5, 360 steps, 1 tok/step)

| component | ms/token | % of step |
|---|---:|---:|
| **pread (SSD expert reads)** | **162.90** | **51.6 %** |
| route_compute (trunk + routing) | 55.31 | 17.5 % |
| expert_compute (MoE mul_mats) | 44.52 | 14.1 % |
| build (graph build/alloc) | 31.60 | 10.0 % |
| other (readback, input setup, trace) | 26.35 | 8.3 % |
| copy (placement) | 21.56 | 6.8 % |
| **sync (backend synchronize)** | **0.02** | **0.01 %** |
| **total** | **315.91** | **100 %** |

→ 3.17 tok/s. Storage+placement = **184.5 ms (58.4 %)**; compute =
99.8 ms (31.6 %); sync = 0.02 ms (0.01 %).

Per-step I/O: **302.3 MB read**, 241 pread calls (1.25 MB avg),
effective read bandwidth **1.86 GB/s**; cache lookups 208/step
(hits 128, misses 80, slots 208).

### CPU reference (K1 cached MXFP4, 127 steps)

| component | ms/token | % of step |
|---|---:|---:|
| copy (repack/placement) | 89.01 | 44.9 % |
| pread (SSD) | 61.16 | 30.9 % |
| route_compute | 20.07 | 10.1 % |
| expert_compute | 15.91 | 8.0 % |
| build | 11.89 | 6.0 % |
| sync | 0.00 | 0.0 % |
| **total** | **198.04** | **100 %** |

→ 5.05 tok/s. Same SSD volume (303.4 MB/step, 212 calls) but effective
read bandwidth **4.96 GB/s** (2.7× Metal) and much cheaper per-step compute
(route+expert = 36.0 ms vs 99.8 ms on Metal).

### Pre-fix degenerate baseline (E1, retained report)

24.3 tok/s decode with **hit_rate 1.000, SSD 0.00 MB/token** — the pre-fix
path (all routed ids = 0 → expert 0 for every position) performed essentially
no routing-driven work: no expert reads, single slot, everything cached.
That number is NOT a valid throughput baseline for real routing; it was the
speed of the *broken* path doing almost nothing.

## Findings

1. **The synchronization fix is NOT the cause.** `sync_us` = 0.02 ms/token
   (0.01 % of the decode step) — one `ggml_backend_synchronize` per MoE
   layer-step measures <1 µs each. There is no CPU↔GPU barrier or
   serialization cost attributable to the routed-expert-ID readback fix.
2. **The dominant cost is SSD expert reads: pread = 162.9 ms/token
   (51.6 %)**, plus placement copy 21.6 ms (6.8 %). The corrected path
   routes real experts → ~302 MB of expert weights read from SSD per
   generated token (80 cache misses/step at the 4 GiB zerocopy cache).
3. **The "24 → 3 tok/s" gap is primarily baseline artifact + real work:**
   the pre-fix 24 tok/s path did 0 MB/token of SSD work (degenerate
   all-expert-0); the corrected path does 302 MB/token. A path that reads
   ~300 MB from SSD per token cannot be compared to one that reads nothing.
4. **Secondary Metal-specific inefficiencies (real, but not dominant):**
   (a) Metal effective read bandwidth is 1.86 GB/s vs CPU's 4.96 GB/s for
   the same volume/pattern — reads are issued at comparable call counts
   (241 vs 212) and sizes (1.25 vs 1.43 MB), so the per-byte cost is ~2.7×
   on Metal (serialized against Metal graph execution rather than overlapped
   with CPU compute); (b) per-step compute (route+expert = 99.8 ms) is
   ~2.8× the CPU's 36.0 ms — likely per-step graph rebuild + small-kernel
   Metal launch overhead at 1 token/step, not arithmetic (E1b/E4 showed the
   arithmetic itself is sound and fast at prefill scale).
5. Net effect: corrected Metal (315.9 ms/step) is now 1.6× *slower* than
   corrected CPU (198.0 ms/step) at the same quantization and cache budget —
   the honest framing of the "regression": Metal decode regressed *below*
   CPU decode, and the two candidate causes are storage-read efficiency
   (2.7×) and per-step compute/launch overhead (2.8×), in that order of
   absolute contribution (pread gap 101.7 ms vs compute gap 63.8 ms).

## Classification

**STORAGE/CACHE-BOUND (mixed, sync-exonerated).**

- pread (SSD expert reads): 162.9 ms/token = **51.6 %** of decode step.
- storage+placement total: 184.5 ms = **58.4 %**.
- compute (route+expert): 99.8 ms = **31.6 %** (secondary).
- sync (idsync fix): 0.02 ms = **0.01 %** — not a factor.

Quantified dominant cost: **162.9 ms/token of SSD pread** (51.6 %),
i.e. ~302 MB SSD read per generated token at ~1.9 GB/s effective on Metal.

## Problems / limitations

- No per-step stats.csv was retained from the pre-fix Metal path (E1-era
  captures kept only run logs and the report's summary lines); the pre-fix
  comparison therefore relies on the retained E1 report's documented
  degenerate behavior (hit_rate 1.000, SSD 0.00 MB/token, 24.3 tok/s)
  rather than a per-step timer decomposition. This does not affect the
  corrected-path decomposition (which is fully measured) or the conclusion
  that the fix's sync cost is negligible.
- `sync_us` measures the explicit `ggml_backend_synchronize` calls; any
  implicit Metal pipeline drain hidden inside route/expert compute would
  appear in those timers, which is consistent with finding 4b but not
  separately attributable without kernel-level profiling (out of scope;
  compute is secondary regardless).

## Decisions

- No code changes, no env-gated diagnostics added, no production behavior
  modified (directive).
- Classification recorded as storage/cache-bound (mixed, sync-exonerated);
  quantified 162.9 ms/token pread (51.6 %).
- E3B (optimization) is NOT started. Candidate directions for a future
  authorized phase, from this evidence, in order of absolute contribution:
  (1) overlap/hide SSD pread (async expert reads / prefetch — pread gap
  vs CPU is 101.7 ms/step), (2) reduce Metal per-step compute/launch
  overhead (63.8 ms/step gap vs CPU), (3) cache-density improvements to
  cut the 80 misses/step.

## Reproduction

    python3 tools/phase11_e3a_analyze.py \
        benchmarks/results/phase-11/e5-deployment/stats.csv \
        benchmarks/results/phase-k1/full/coding-cap4/b8-A-after/stats.csv

    # expected key lines (see benchmarks/results/phase-11/e3a/decode-decomposition.txt)
    # Metal: pread 162.90 ms (51.6%), sync 0.02 ms (0.01%), total 315.91 ms -> 3.17 tok/s
    # CPU:   pread  61.16 ms (30.9%), total 198.04 ms -> 5.05 tok/s
