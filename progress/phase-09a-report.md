# Phase 9A Report — Prefill → Decode Cache Continuity

## Status

**PASS (isolated effect measured; lever exhausted).** The prefill→decode
cache-continuity change was implemented, correctness-validated, and
benchmarked in isolation against the frozen Phase 8 baseline. The measured
effect on decode SSD traffic is **~0%** (slightly *negative* at the 4 GB
operating point), matching the offline trace replay to within 0.01%. The
reason: at ≤4 GB the zero-copy cache is **already full at decode start**
(the 2+2+4-token batches fill it); warming only swaps *which* experts are
resident, and the prefill tail is not more reusable in decode than the
batch experts it displaces. The prefill-bypass is therefore **not the
unexploited opportunity the selection gate hypothesized**; the LRU→OPT gap
(9B) is. Phase 9A is complete and isolated; 9B proceeds on top of the
frozen Phase 8 baseline + this measured 9A outcome.

## Objective

Phase 9A of the selection-gate scope item 1: investigate why the 233-token
prefill step performs zero expert-cache lookups; determine whether experts
loaded during prefill can safely populate the existing zero-copy
persistent-slot cache for decode without changing model semantics; implement
the smallest change that enables that continuity; validate correctness;
benchmark against the frozen Phase 8 baseline; report the isolated effect.

## Investigation — Why the 233-Token Prefill Has Zero Cache Lookups

The zero-copy persistent-slot path is gated in `load_layer`
(`src/llama-expert-stream.cpp`):

```cpp
bool zc_use = zc_mode_ && cache_enabled() && n_slots > 0 &&
              (int) n_slots <= zc_cap_[il - 1];
```

The 233-token prefill step routes 233×8 expert occurrences per layer; its
per-layer **unique** expert count (~90–224, observed max 224) far exceeds
the per-layer persistent capacity (38–39 slots at the 4 GB budget). So
`zc_use` is false for every layer: the layer falls back to legacy placement
(full pread of every unique expert into a per-step compact tensor, bulk
repack), the cache is **never consulted** (`stats_.n_cache_lookups == 0`,
observed) and its state is **untouched**. The step reads 19.98 GB from SSD
in one go. The only prefill steps that populate the cache are the 2+2+4
-token batches (steps 0,1,3), whose per-layer unique counts (~16, ~16, ~26)
fit the capacity — and those batches alone already fill the cache to
near-capacity at ≤4 GB (see Results).

The cache-arm call (first `n_slots>0` layer, legacy placement, cache left
empty by design) is a separate, modeled bypass; it is unchanged by 9A.

## Safety Determination — Can Prefill Loads Warm the Cache?

**Yes — safe, with no semantic change, by construction and by measurement:**

1. The zc persistent tensors and the legacy per-step compact tensors are
   allocated in the **same repack buffer type**; the runtime's own
   self-checks (`cache_verify_layout`, `zc_verify_slot`) prove the
   per-slice temp repack and the bulk repack produce **byte-identical**
   layouts. Warming copies already-repacked regions of the compact tensors
   into the persistent slots — i.e. exactly the bytes a decode miss would
   have placed, with no pread and no extra repack.
2. Warming runs **after** the prefill step's own graph tensors are built
   and populated; the prefill computation consumes the compact tensors and
   never reads the slots being warmed, so the prefill step's own output is
   bit-identical.
3. Slot bookkeeping reuses the `zc_place` machinery (`zc_take_slot`,
   `zc_map_`, per-layer LRU), so decode hits on warmed entries are
   indistinguishable from hits on normally-placed entries, and eviction
   stays LRU-consistent.
4. **Measured**: `KIMI_CACHE_SELFCHECK=1` run — every first-hit
   per-(layer,kind) on warmed entries verified byte-identical against a
   fresh pread+repack (`zerocopy selfcheck OK`); routing trace md5
   identical to the frozen baseline (below).

## Changes

- `llama.cpp/src/llama-expert-stream.h` — declare
  `zc_warm_tail(il, unique, n_slots, kinds, expert_buft)`.
- `llama.cpp/src/llama-expert-stream.cpp` —
  - call site in `load_layer`: after legacy placement, when
    `zc_mode_ && cache_enabled() && n_slots > zc_cap_[il-1]` (the oversized
    bypass case only), feed the step's tail through the persistent LRU;
  - `zc_warm_tail`: ensure the layer's persistent tensors exist; walk the
    last `min(n_slots, cap)` unique experts in stream order; touch/insert
    via `zc_take_slot` (bookkeeping + eviction identical to `zc_place`);
    copy each warmed expert's 3 already-repacked slices from the compact
    loaded tensors into the persistent slots (host memcpy, counted as
    placement work; no pread, no repack).

  Deliberately **excluded** from 9A (deferred to 9B): any smarter choice of
  *which* prefill experts to warm (frequency-aware admission etc.). 9A
  implements only the semantically neutral "feed the stream through the
  existing LRU" continuity.

- `tools/phase09a_warm_sim.py` — offline replay of the proposed change
  (extends `phase09_selection_gate.py`'s validated LRU model with
  oversized-step tail feeding); writes
  `benchmarks/results/phase-09a-warm-sim.json`.

- `benchmarks/results/phase-09a/` — correctness run + controlled ladder
  (below). Frozen Phase 8 artifacts untouched; live runtime
  (`runtime/live`, commit `cad716035`) untouched.

## Results

### Offline replay (prediction, before implementation)

`tools/phase09a_warm_sim.py` over the Phase 8 traces (coding/reasoning/ref,
budgets 1–8 GB): warming changes decode MB/token by **−0.6% … +3.8%** —
essentially zero, slightly negative at 4/6 GB (the prefill tail displaces
the immediately-preceding 4-token batch's experts, which decode reuses
sooner). Decode-start occupancy is already at capacity ≤4 GB; warming adds
occupancy only at 6/8 GB (45→56/75 slots) and that occupancy does not
translate into hits (decode LRU converges within ~1–2 steps regardless).

### Correctness (first, per instruction)

coding-lru @ 4 GB, `KIMI_CACHE_SELFCHECK=1`:

- **Routing trace md5-identical to the frozen baseline**
  (`da45ab777b0fdd99be62f6c46a642e5c` for both) → identical router
  decisions → identical hidden states → identical generated tokens
  (temp 0, seed 1, deterministic).
- All zero-copy selfchecks OK (warmed slot bytes == fresh pread+repack).
- `tools/phase07_summarize.py` invariants: **PASS, 0 violations**.
- Stats confirm the intended behavior change: step 2 (233-token prefill)
  still `lookups=0` (bypass preserved) but now `evictions=648`,
  `placement_bytes=3.89 GB` (warm); decode starts fully occupied.

### Benchmark vs frozen Phase 8 baseline (controlled ladder, 3 reps,
interleaved uncached controls, settle 15 s, selfcheck off, same protocol
as Phase 8)

`benchmarks/results/phase-09a/ladder/{coding-lru,reasoning,ref}/` — 18 runs
(cap-4 r1-r3 + interleaved uncached controls per workload). moe.csv
md5-identical to the frozen baseline in **all 18 runs**; invariants PASS
(0 violations) in all 18.

**Deterministic columns (cross-session comparable; zero dispersion):**

| workload | hit rate P8 → 9A | SSD MB/token (steady decode) P8 → 9A |
|---|---:|---:|
| coding | 0.664 → 0.664 | 286.34 → 286.37 |
| reasoning | 0.564 → 0.564 | 372.37 → 372.39 |
| ref | 0.590 → 0.590 | 354.49 → 354.35 |

Steady-state decode (steps ≥ 8) is **byte-identical**: same pread bytes
(33884.1 MB coding), same hit counts (16491 coding). The entire warm
effect is confined to the **first 8 decode steps and is negative there**
(coding first-8 decode MB: 2774.7 → 2864.0; hits 987 → 965; reasoning
+9.3 MB; ref +56.6 MB) — the prefill tail displaces the immediately-
preceding 4-token batch's experts, which decode reuses first.

**Full-decode MB/token (the gate's metric):** coding 286.40 → 287.09
(+0.24%), reasoning 370.1 → ~370.2, ref 345.1 → ~345.7 — essentially
zero, slightly negative, matching the offline replay to 0.01%.

**Timing (NOT cross-session comparable; in-session controls below):**

| workload | decode tok/s (median, in-session ratio vs its uncached control) |
|---|---:|
| coding | 4.10 (1.64× vs in-session 2.60) — P8 5.39 (2.11×) |
| reasoning | 3.39 (1.32× vs in-session 2.56) — P8 4.58 (1.75×) |
| ref | 3.82 (1.48× vs in-session 2.58) — P8 4.91 (1.83×) |

Tok/s is depressed **across the board today** vs 2026-08-14 (uncached
control also slower: 2.60-2.62; pread_ms +32-44% in every run including
uncached; route_ms +50%, expert_ms +55%; load-samples show Brave at
55-133% CPU, NordVPN, ZoomUpdater, mds indexing). The 9A change cannot
affect steady decode timing (decode steps execute the identical code
path; steady bytes/hits identical), so the tok/s gap is environmental,
not attributable. In-session ratio moved (2.11→1.64) because the SSD
slowdown is uneven (cached pread +32% vs uncached +14%).

### Metric table (Phase 9A required metrics)

| metric | Phase 8 frozen (cap-4 coding) | Phase 9A (cap-4 coding) |
|---:|---:|---:|
| decode tok/s (vs in-session uncached) | 5.39 (2.11×) | 4.10 (1.64×; env confounded) |
| SSD MB/token (full decode) | 286.4 | 287.1 (+0.24%) |
| SSD MB/token (steady decode) | 286.3 | 286.4 (identical) |
| decode hit rate (steady) | 0.664 | 0.664 (identical) |
| decode-start cache occupancy | 37.7/38-39 slots | 37.8/38-39 slots |
| decode pread time/step (steady) | 66 ms | 87 ms (env) |
| TTFT / prefill cost | ~19.5 s | ~+0.8 s warm copy (+3-5%) |
| memory (decode steady phys / cache) | 5.59 GB / 4.09 GB | 5.59 GB / 4.09 GB |

### Why the lever is ~0% (measured + replayed)

1. At 4 GB the cache is **already full** at decode start (measured: 38–39
   of 38–39 slots/layer, layer 26 structurally at 20/38 because the last
   MoE layer is not routed during the big prefill). Warming cannot add
   occupancy; it can only swap residents.
2. The swap is neutral-to-negative: the 2+2+4-token batches immediately
   precede decode, and their experts have shorter reuse distance than the
   233-token prefill tail. Measured first-decode-step hits: 138 → 124.
3. Runtime measurement reproduces the replay to 0.01% (286.40→287.09
   measured vs 286.4→287.1 replayed), so the negative result is not an
   implementation artifact.

## Problems

- The selection gate's "prefill cache warm-up is a structural,
  currently-unexploited opportunity" hypothesis is **disconfirmed by
  measurement**: the cache is not cold at decode start (the small batches
  fill it), and the prefill tail is not a better resident set. The gate's
  own decomposition already implied this (decode-phase compulsory is only
  16.6 MB/token; nearly all decode traffic is reload traffic whose fix is
  policy, not warm start).
- Cross-session timing (pread ms, TTFT) is thermal/state dependent;
  prefill-cost attribution uses the in-ladder comparisons and the
  deterministic placement delta (+0.8 s warm copy on the 233-token step)
  rather than absolute cross-session times.
- Layer 26 (last MoE layer) cannot be warmed by the 233-token prefill (it
  routes zero experts during that step) — a structural limitation, not a
  bug.

## Decisions

- **Implement the semantically neutral tail-feed as the 9A change** (not a
  frequency/pinning-aware warm selection — that belongs to 9B's policy
  work).
- **Keep the arm-call bypass untouched** (validated Phase 8 fidelity model
  preserved; lookups=0 on the prefill step still holds).
- **Warm only oversized steps** (`n_slots > cap`), i.e. exactly the
  documented bypass; in-cap steps already populate via `zc_place`.
- **Keep the 9A change in the dev tree (committed), do NOT promote it to
  the live runtime**: measured decode benefit ≈ 0% (slightly negative in
  the first 8 decode steps) at a +0.8 s (+3-5%) TTFT cost. The live
  runtime (`runtime/live`) stays frozen at Phase 8 behavior regardless.
  9B's offline policy replay will evaluate policy candidates both from the
  frozen baseline and from the 9A-warm start state (per the instruction to
  benchmark 9B "on top of 9A"); the warm hook is also the natural plug
  point for a smarter prefill admission policy if 9B's replay shows one
  winning. If 9B lands without needing the warm start, revert this commit.
- **Report tok/s with the environmental confound explicit**: deterministic
  columns (MB/token, hit rate, bytes) are the reliable cross-session
  comparators; tok/s uses in-session uncached controls only.

## Next Phase (9B — replacement policy)

- 9A measured: the prefill-bypass lever is exhausted; the 40% LRU→OPT gap
  at 4 GB (115.3 MB/token policy-fixable) is the live opportunity.
- Use the existing offline trace replay (selection-gate tool) FIRST to
  rank candidate policies (segmented LRU, 2Q, CLOCK, frequency-aware
  admission, pinning) by MB/token, LRU→OPT gap recovered, complexity,
  metadata overhead, runtime overhead — per instructions; then implement
  only the selected policy, validate correctness, benchmark it (vs the
  frozen Phase 8 baseline and vs the 9A-warm state), and stop/report.
- Keep: moe.csv md5 determinism gate, `phase07_summarize` invariants,
  in-session uncached controls, MB/token + tok/s reported separately.
- Selection-gate traces/results (`benchmarks/results/phase-09-selection-
  gate.json`, `tools/phase09_selection_gate.py`) remain the baseline for
  future MXFP4 evaluation; the 9A artifacts are additive.

## Reproduction

```bash
# offline replay of the 9A warm change (writes
# benchmarks/results/phase-09a-warm-sim.json)
python3 tools/phase09a_warm_sim.py

# correctness run (selfcheck on)
CTX=4096 KIMI_PHASE7_INSTR=1 KIMI_EXPERT_CACHE_MB=4096 \
  KIMI_EXPERT_CACHE_MODE=zerocopy KIMI_CACHE_SELFCHECK=1 \
  tools/phase04_run_streamed.sh \
  benchmarks/results/phase-09a/correctness/cap-4-selfcheck \
  benchmarks/prompts/phase-03-coding-lru.md 128 1 naive
python3 tools/phase07_summarize.py \
  benchmarks/results/phase-09a/correctness/cap-4-selfcheck
md5 benchmarks/results/phase-09a/correctness/cap-4-selfcheck/moe.csv \
     benchmarks/results/phase-08/coding-lru/cap-4-r1/moe.csv

# controlled ladder (3 workloads x cap-4, interleaved uncached controls)
tools/phase07b_run_baseline.sh benchmarks/results/phase-09a/ladder/coding-lru \
  4 --reps 3 --prompt benchmarks/prompts/phase-03-coding-lru.md --n-tokens 128
# ... same for reasoning / ref
python3 tools/phase07b_summarize.py --root benchmarks/results/phase-09a/ladder/coding-lru
```

Environment: llama.cpp dev tree `cad716035` + 9A change (worktree dirty),
Release build, CPU backend (repack on) + Metal, `-ngl 0 --no-mmap`,
ctx 4096, seed 1, temp 0, zerocopy cache mode, 4 GB budget.
