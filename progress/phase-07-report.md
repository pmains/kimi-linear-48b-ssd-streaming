# Phase 7 Report — Instrumentation and Observability

## Status

**PASS** — instrumentation and observability phase complete. The
Phase 6B baseline (llama.cpp `eca7742b8`, project `51fff51`) was frozen
and untouched in behavior: no repack-bottleneck fixes, no cache-policy
changes, no Metal work. The instrumented binary reproduces the Phase 6B
oracle bit-identically and the Phase 6B ladder hit rates exactly
(0.305 / 0.585 / 0.756 at 1/4/8 GB), with instrumentation overhead
quantified at ~0–3% (not distinguishable from thermal drift).

## Objective

Measure the system rather than infer its behavior. Specifically:

1. Preserve and expose the measurements that matter: expert
   hits/misses/evictions; cache bytes/capacity **by layer**; pread
   bytes/time; repack/placement bytes/time; routing and expert-compute
   time; trunk/build/sync time; process physical footprint; cache
   residency; overall prefill/decode tok/s.
2. Distinguish **zero-copy hits** (no I/O, no repack, no placement
   bytes), **placement hits** (Phase 6 mode: no I/O, no repack, memcpy
   only), and **misses** (pread + repack + placement) — never collapse
   them into a generic "cache hit rate", because Phase 6B demonstrated
   the costs are materially different.
3. Validate against the Phase 6B oracle and ladder data. Instrumentation
   must not alter generated output, numerics, cache behavior, or
   materially affect performance; overhead must be quantified.
4. Keep Phase 6B findings as baselines, not targets: 4 GB sweet spot on
   24 GB; ≥10 GB memory-pressure degradation; per-slice miss repack
   ~1.1–1.6 ms — all explicitly deferred optimization opportunities.

## Changes

### llama.cpp (on top of `eca7742b8`; project commit `e8baeb16e`)

- `src/llama-expert-stream.h` — extended `llama_expert_stream_stats`:
  - hit classes: `n_zc_hits`, `n_placement_hits` (invariant:
    `cache_hits = zc_hits + placement_hits`);
  - miss-cost split: `repack_us/bytes` (the quantized-layout transform —
    `ggml_backend_tensor_set` into a repack-buft buffer, including the
    legacy bulk tensor_set) and `placement_us/bytes` (memcpy of repacked
    bytes into the compute-consumed buffer: loaded compact tensor,
    persistent slot, or cache block); invariant: `copy_us =
    repack_us + placement_us`;
  - `zc_hit_bytes` (placement bytes elided by zero-copy hits) kept
    distinct from `cache_hit_bytes` (bytes elided from I/O+repack);
  - per-layer observability: `zc_used_[26]` live-entry counter,
    `slice_bytes_` table retained, `dump_cache_layers()`.
- `src/llama-expert-stream.cpp`:
  - split every `copy_us` accumulation into repack/placement at the four
    code sites (legacy bulk tensor_set → repack only; Phase 6 cached
    miss path → repack + placement incl. cache-block copy; Phase 6 hit
    path → placement only; zero-copy miss path → repack + placement);
  - zero-copy hits counted in `zc_place` Phase A; `zc_used_` maintained
    on insert/evict;
  - `dump_cache_layers()` — `step,il,cap_slots,used_slots,
    occupied_bytes,up_bytes,gate_bytes,down_bytes` per MoE layer.
- `src/llama-expert-stream-exec.cpp`:
  - stats.csv extended 20 → 30 columns (**appended**, so all Phase 4–6B
    scripts/artifacts remain valid): `repack_us, placement_us,
    repack_bytes, placement_bytes, zc_hits, placement_hits,
    zc_hit_bytes, build_us_measured, other_us, n_unique_ranges`;
  - `build_us_measured` — direct timing of the four subgraph build+alloc
    sites (preamble, per-layer route, per-layer compute, epilogue),
    replacing the residual-only `build_us` (kept as col 11 for backward
    compatibility); `other_us` is the explicit residual (readback,
    input setup, dedupe, trace dumps, preamble/epilogue compute);
  - `route_compute_us` documented as dense trunk (attention/norm/KDA) +
    routing for the layer's route subgraph;
  - new `KIMI_STREAM_CACHE_LAYERS_FILE` env-gated per-layer cache dump
    (only when a cache is enabled).
- All additions are counters/timers/CSV rows. No routing math, cache
  policy, eviction, graph structure, or memory behavior changed.

### kimi repo

- `tools/phase07_summarize.py` — run-dir summarizer + instrumentation
  validator (per-phase tok/s mean/median and component latencies; cache
  aggregates with the hit-class split; per-layer cache table; memory
  summary; SSD MB/token; per-step invariant checks, exit code 0 = all
  hold). Supports `--ladder ROOT` min-of-2 merge to
  `ladder-summary.csv`.
- `tools/phase07_run_ladder.sh` — subset ladder (default caps 1/4/8 GB +
  uncached; ctx 4096 pin; min-of-2; reversed order; zerocopy mode by
  default, `KIMI_EXPERT_CACHE_MODE=placement` selects the Phase 6
  reference path) with full instrumentation.
- `tools/phase07_run_overhead.sh` — A/B overhead quantification
  (min-of-2; bash 3.2-safe env handling).
- `tools/phase04_run_streamed.sh` — `KIMI_PHASE7_INSTR=1` passthrough
  (adds mem.csv + cache_layers.csv on top of the pre-existing Phase 6B
  file set) + manifest field.
- `ROADMAP.md` — Phase 7 status block (this report).

## Results

### 1. Correctness — instrumentation does not alter inference

Fresh oracle on the instrumented binary (conventional capture + streamed
zero-copy cache, 2 GB, selfcheck ON, full instrumentation), Phase 5
comparator:

- **OVERALL PASS** — 13/13 executions aligned, **33,648/33,648**
  retrieval ranges byte-exact, **1,512 router rows bit-identical**,
  every activation/logits trace **max|Δ| = 0**.
- **78/78** zero-copy selfchecks OK, 0 mismatches.
- The fresh conventional capture is bit-identical to the frozen Phase 6B
  `conv-10` capture (conventional path byte-unchanged on the new
  binary).

### 2. Hit-class decomposition — the Phase 6B distinction, measured

Decode steady (steps ≥ 8), zero-copy mode, 64-token ref workload, ctx
4096, min-of-2, reversed rung order, in-session uncached control
(`benchmarks/results/phase-07/ladder-zc/`):

| rung | tok/s | ms/step | pread ms | repack ms | placement ms | hit_rate | zc_hit | ph_hit | misses | SSD MB/tok | phys MB |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| uncached | 2.52 | 397 | 99 | 240 | 0 | — | — | — | — | 850 | 1,414 |
| 1 GB | 3.54 | 283 | 81 | 142 | 10 | 0.305 | 0.305 | 0.000 | 8,523 | 592 | 2,441 |
| **4 GB** | **4.73** | **211** | 73 | 82 | 6 | 0.585 | 0.585 | 0.000 | 5,087 | 354 | 5,514 |
| 8 GB | 4.39 | 228 | 78 | 50 | 29 | 0.756 | 0.756 | 0.000 | 2,999 | 210 | 9,505 |

- **Every rung beats the in-session uncached control** (1.40× / 1.88× /
  1.74×); **4 GB is the peak**, matching the Phase 6B sweet spot.
- **Zero-copy hits place ~0 bytes**: placement ms collapses to miss-slice
  cost only (10/6/29 ms vs 65/63/126 ms in placement mode at the same
  rungs — see §5), while `zc_hit_rate == hit_rate` and `ph_hit_rate ==
  0` in zc mode. The three access classes are now individually
  observable: zc hits (0 bytes moved), placement hits (memcpy only),
  misses (pread + repack + placement).
- SSD traffic per token decreases monotonically with budget
  (592 → 354 → 210 MB/tok), the number Phase 8's acceptance table asks
  for.
- Residency: cache_bytes_used = budget (exact, 1,021/4,094/8,191 MB),
  phys_footprint = baseline + touched slots (2.4/5.5/9.5 GB) — bounded,
  consistent with the 6B 8 GB measurement (7.9 GB vs 9.5 GB today; page
  cache / thermal session differences).

### 3. Per-layer cache observability

`cache_layers.csv` exposes capacity and occupancy per MoE layer per
step. 4 GB rung, last decode step:

- 26/26 layers live; **39 slots/layer** (1002 total), all occupied at
  steady state (used == cap).
- Per-kind slice bytes directly visible: up/gate 1,327,104 B (Q4_K) all
  layers; down 1,935,360 B (Q6_K, 6 layers) or 1,327,104 B (Q4_K, 20
  layers) — the per-layer inventory the budget partition is built from,
  now observable at runtime rather than only in the retrieval CSV.
- The 4 GB partition (38..39 slots/layer) matches the sim's exact
  constructor replica (`tools/phase06b_zc_sim.py`).

### 4. Hit-rate validation vs the Phase 6B ladder and sim

| GB | 6B measured | Phase 7 measured | Δ vs 6B | sim pred | Phase 7 Δ vs sim | 6B Δ vs sim |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 0.305 | 0.305 | 0.000 | 0.302 | +0.003 | +0.003 |
| 4 | 0.585 | 0.585 | 0.000 | 0.568 | +0.017 | +0.017 |
| 8 | 0.756 | 0.756 | 0.000 | 0.713 | +0.043 | +0.043 |

Hit rates are **bit-identical to Phase 6B** and track the per-layer-LRU
sim to the exact same tolerance (1–4 pp) — cache behavior is unchanged
by instrumentation.

### 5. Placement-mode reference ladder (bonus validation)

`benchmarks/results/phase-07/ladder/` (Phase 6 reference path, same
protocol; hit rates match the sim to the same tolerance):

| GB | tok/s | placement ms | hit_rate | ph_hit | zc_hit |
|---|---:|---:|---:|---:|---:|
| uncached | 2.37 | 0 | — | — | — |
| 1 | 3.08 | 65 | 0.337 | 0.337 | 0.000 |
| 4 | 3.59 | 63 | 0.582 | 0.582 | 0.000 |
| 8 | 3.00 | 126 | 0.765 | 0.765 | 0.000 |

Placement-mode hits are visibly more expensive than zero-copy hits at
every rung (65–126 ms/step placement vs 6–29 ms) — the two hit classes
the old single "hit rate" collapsed together.

### 6. Trunk / build / sync decomposition

Decode steady, 4 GB zc rung (ms/step): pread 73 + repack 82 +
placement 6 + sync ~0 + route(trunk+routing) 21 + expert 18 +
**build_measured 2** + other 10 ≈ 212 ms vs total 211 ms (accounting
closes within noise).

- `build_us_measured` (~2 ms/step decode) is an order of magnitude below
  the legacy residual `build_us` (~12–24 ms) — the residual is dominated
  by readback/input-setup/trace-dump work, not graph construction. The
  legacy residual column is retained for backward compatibility.
- `route_compute_us` carries the layer's dense trunk (attention, norms,
  KDA) plus routing; `expert_compute_us` is the MoE matmul compute —
  the two compute classes are separable, and both are stable across
  cache rungs (no confound at ≤ 8 GB; both degrade ~10× at ≥ 10 GB per
  the 6B finding — unchanged).

### 7. Instrumentation overhead — quantified

- **Uncached A/B** (min-of-2, same binary): instrumented 2.476 vs base
  2.491 tok/s → **−0.6%**.
- **4 GB zero-copy, 10 alternating runs with linear drift fit**
  (base drift 3.10 ms/run, instr drift 3.27 ms/run — same rate):
  intercept delta **+7.1 ms/step = +3.0%**.
- **Direct attribution**: the instrumentation's own work (mem snapshot +
  per-layer dump + extra CSV columns) lands in `other_us` at +0.1 to
  +1.2 ms/step (< 0.5% of a ~230 ms step).
- **Thermal noise bound**: consecutive-run drift reached ~17% on this
  fanless Air (6B reported the same class of effect), i.e. the overhead
  is **not distinguishable from thermal noise**; the honest bound is
  ~0–3%, with the per-step attribution suggesting < 0.5% from the
  instrumentation itself.
- Output, numerics, and cache behavior are unaffected (oracle + hit
  rates above), so the overhead is pure wall-time, not fidelity.

### 8. Invariant validation (per-step, every run)

All Phase 7 runs pass the summarizer's invariant checks (0 violations on
uncached, placement, zero-copy, oracle, and smoke runs):

- lookups == hits + misses; hits == zc_hits + placement_hits;
- copy_us == repack_us + placement_us;
- repack_bytes == pread_bytes (every byte read is repacked exactly
  once, both modes);
- zero-copy mode: placement_bytes ≤ pread_bytes (pure-zc decode steps
  have exact equality; prefill steps mixing legacy-fallback layers are
  detected as mixed steps, expected by design);
- placement mode: zc_hit_bytes == 0;
- measured component sum ≤ total (+1 ms clock slack).

## Problems

1. **Overhead measurement is thermal-noise-limited.** The fanless Air's
   consecutive-run drift (~17%) exceeds the instrumentation signal
   (~3%). Mitigated with min-of-2 + a 10-run alternating drift fit; the
   residual uncertainty is reported, not hidden.
2. The per-layer dump flushes per step (26 rows × fflush). Cost is
   included in the measured overhead (~µs–ms); acceptable for an
   observability phase, candidate for buffering in a later optimization
   phase — not touched here.
3. Mixed prefill steps (legacy-fallback + zero-copy layers in one step)
   make per-step byte invariants mode-aware; the summarizer reports
   them explicitly rather than flagging them.
4. Cross-session absolute tok/s are not comparable (thermal); the
   in-session uncached control is the bar, as in Phase 6B.

## Decisions

- **Append-only stats columns** (20 → 30): Phase 4–6B scripts and
  artifacts stay valid; new columns ride along.
- **Repack/placement defined by operation class**, not code path:
  repack = tensor_set into a repack-buft buffer; placement = memcpy of
  repacked bytes into the final buffer (incl. cache-block copies). The
  legacy uncached path is repack-only by definition (bulk tensor_set),
  so its placement_bytes = 0 — consistent across all three modes.
- **Measured build time as a new column**; the legacy residual stays as
  col 11 (backward compatibility).
- **Per-layer observability as a separate file** (cache_layers.csv) —
  the data is 2-D (layer × step) and would have widened the stats row
  past usefulness.
- **Result preservation**: ladder/smoke/overhead run dirs keep the
  measured artifacts (stats.csv, mem.csv, cache_layers.csv, moe.csv,
  retr.csv, run.log, manifest.json) plus the merged ladder-summary.csv;
  act.bin (the TCAT activation trace, ~190 MB per 64-token run) is
  retained only for the oracle pair (oracle-conv, oracle-zc) where the
  comparator needs it — every act.bin is reproducible with the
  documented `tools/phase04_run_streamed.sh` command.
- **zc_used_ maintained explicitly** (O(1) insert/evict) instead of
  map scans at dump time.
- Zero-copy mode remains the Phase 7 default ladder path (the 6B
  baseline); placement mode retained as an env-selectable reference.

## Next Phase

Phase 8 (Memory Ladder) consumes Phase 7's output directly: the
roadmap's acceptance table (cache, resident RAM, hit rate, decode tok/s,
SSD traffic) maps 1:1 onto `ladder-summary.csv` columns
(hit_rate/zc_hit_rate, phys_decode_mb, tok_per_s, ssd_mb_per_token,
misses, cache_mb). The ladder protocol, instrumentation, and validator
are now reusable as-is: `KIMI_PHASE7_INSTR=1
tools/phase07_run_ladder.sh <root> 1 2 4 6 8 12` then
`tools/phase07_summarize.py --ladder <root>`. Per the STOP point, no
further optimization work is started from this phase; the deferred items
(per-slice repack ~1.1–1.6 ms; ≥10 GB memory pressure) remain baselines
for a future optimization phase selected by Phase 8's measurements.

## Reproduction

```bash
# 1. Oracle (fresh conventional capture + zero-copy cached streamed run +
#    comparator), full instrumentation incl. selfcheck
CTX=4096 tools/phase04_run_capture.sh benchmarks/results/phase-07/oracle-conv \
    benchmarks/prompts/phase-04-ref.md 10 1
CTX=4096 KIMI_EXPERT_CACHE_MB=2048 KIMI_EXPERT_CACHE_MODE=zerocopy \
    KIMI_CACHE_SELFCHECK=1 KIMI_PHASE7_INSTR=1 \
    tools/phase04_run_streamed.sh benchmarks/results/phase-07/oracle-zc \
    benchmarks/prompts/phase-04-ref.md 10 1 naive
python3 tools/phase04_compare.py benchmarks/results/phase-07/oracle-conv/act.bin \
    benchmarks/results/phase-07/oracle-conv/moe.csv \
    benchmarks/results/phase-07/oracle-zc/act.bin \
    benchmarks/results/phase-07/oracle-zc/moe.csv \
    benchmarks/results/phase-07/oracle-zc/retr.csv

# 2. Instrumented ladder (zero-copy, 1/4/8 GB + uncached, min-of-2,
#    reversed order) + merged summary + invariant validation
tools/phase07_run_ladder.sh benchmarks/results/phase-07/ladder-zc 1 4 8
python3 tools/phase07_summarize.py --ladder benchmarks/results/phase-07/ladder-zc

# 3. Hit-rate cross-check vs the per-layer-LRU sim (exact 6B partition)
python3 tools/phase06b_zc_sim.py benchmarks/results/phase-07/ladder-zc/uncached/moe.csv \
    benchmarks/results/phase-07/ladder-zc/uncached/retr.csv 1 4 8

# 4. Instrumentation overhead A/B (uncached; then 4 GB zero-copy)
tools/phase07_run_overhead.sh benchmarks/results/phase-07/overhead-uncached
KIMI_EXPERT_CACHE_MB=4096 KIMI_EXPERT_CACHE_MODE=zerocopy \
    tools/phase07_run_overhead.sh benchmarks/results/phase-07/overhead-4gb

# 5. Per-step invariant check on any run dir
python3 tools/phase07_summarize.py benchmarks/results/phase-07/ladder-zc/cap-4-b
```

Environment: Apple M5 (Mac17,3), 24 GB unified memory, macOS 26.5.2;
llama.cpp `eca7742b8` + Phase 7 changes (`e8baeb16e`); project commit `<PROJECT-COMMIT>`;
model `moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`; `-ngl 0`
CPU, `--no-mmap`, `--ctx-size 4096`, `--temp 0 --seed 1`.
