# Phase 11 E3D Report — Can Expert Reads Overlap With Useful Metal Compute?

## Status

**PASS — determination complete: useful compute/I/O overlap is NOT feasible
in the current streamed subgraph architecture.** The per-token/per-layer
dependency chain is strictly serial — route subgraph → ids readback → expert
reads → expert compute — both *within* a layer (reads need the routing
decision; expert compute needs the loaded weights) and *across* layers
(route subgraph N+1 consumes act(N), which is written by compute N). No Metal
compute can be moved into the read window without either (a) a per-slot
compute split that changes the expert accumulation order (E4 quality risk,
forbidden by directive) or (b) breaking the routing dependency. The only
overlap-able work under exact semantics is the resident shared-expert FFN
(~2.2 ms/step, +2% ceiling), which is within run-to-run noise and requires a
non-minimal graph change. **No prototype implemented — the analysis is
conclusive from code + retained E3C measurements; the directive's
"minimal env-gated prototype" clause is not triggered.** Decode ceiling from
overlap: ~9.67 tok/s (+2%) safe, ~10.7 tok/s (+13%) only with E4-risky
per-slot surgery. Reads remain the dominant serial cost; the productive
direction stays read-side (E3C workers already realized the recoverable
parallelism; cache-density is the remaining lever). Stopped for review.

## Objective

E3C improved corrected-Metal decode 6.41 → 9.47 tok/s with bounded parallel
read workers while preserving correctness and quality. E3D (authorized):
determine whether *additional* latency can be removed by overlapping expert
reads with Metal computation rather than waiting for each read phase to
complete. Map the per-token/per-layer dependency sequence around
routing → expert selection → expert reads → expert compute; identify any work
that can safely execute while expert reads are outstanding (independent
experts within a layer, or work across layers if dependencies permit);
quantify the maximum realistically overlap-able read time from the existing
E3C measurements; implement a bounded env-gated prototype only if necessary
to validate overlap. Preserve exact routing/model semantics and E4 quality.
Do not begin general kernel optimization. Report, retain artifacts, update
ROADMAP, stop for review.

## Method

No source changes. Two evidence sources:

1. **Code dependency mapping** of `process_ubatch_streamed`
   (`llama.cpp/src/llama-expert-stream-exec.cpp`) and the streamed graph
   builders (`llama.cpp/src/models/kimi-linear.cpp`): for each layer the loop
   is `stream_build_layer_route` → `graph_compute` (Metal, dense trunk +
   routing) → ids readback (+sync) → `streamer_->load_layer` (cache lookup →
   preads via the Phase 9D/9F pool → placement) → `stream_build_layer_compute`
   → `graph_compute` (Metal, expert FFN) → next layer, whose route subgraph
   reads the persistent `P.act` written by this layer's compute subgraph.

2. **Bounded per-layer trace** (existing `KIMI_PHASE9C_TRACE`
   instrumentation, no code change; identical deterministic E3C W4 arm
   config: fork `a895f6826`, MXFP4, `-c 4096 -n 48 --temp 0 --seed 7`,
   `-ngl 999`, `KIMI_STREAM_METAL_STAGE=1`, `KIMI_STREAM_E2_DIRECT_PLACE=1`,
   naive stream, zerocopy 4096 MiB, `KIMI_EXPERT_READ_WORKERS=4`, prompt
   `benchmarks/prompts/phase-11-e5-engineering.md`). Retained under
   `benchmarks/results/phase-11/e3d/`.

## Results

### Per-layer decode serialization (E3D trace, W4, decode-only rows, n=1222)

| phase | ms/layer | ms/step (×26) | Metal? |
|---|---:|---:|---|
| route subgraph (attention/norm/KDA + routing) | 0.83 | 21.6 | yes |
| ids readback + sync | 0.21 | 5.4 | no |
| load_wall (preads + placement + bookkeeping) | 2.21 | 57.5 | no |
| — of which pread_wall (read phase) | 2.06 | 53.5 | no |
| expert compute subgraph | 0.78 | 20.2 | yes |
| step total (stats file) | | 116.0 | |

(This run: Generation 8.6 t/s vs E3C's 9.4 — run-to-run/thermal variance;
structure is what E3D measures. E3C authoritative step decomposition: 105.6
ms/step = pread_wall 48.8 + route 22.1 + expert 18.9 + build 7.0 + placement
6.5 + other 0.2.)

### Dependency sequence (the map E3D asked for)

Per token, per layer `il`:

```
route(il)   [Metal: dense trunk + topk routing]   →  produces routing ids
    ↓ (ids must cross to host; readback + sync)
load(il)    [I/O: cache lookup → preads → placement]  →  produces loaded experts
    ↓ (compute subgraph consumes the loaded tensors)
compute(il) [Metal: expert FFN over loaded tensors]  →  writes act(il)
    ↓ (route(il+1) consumes act(il))
route(il+1) ...
```

Critical dependencies that block overlap:

- **Within a layer**: reads cannot start until the route subgraph has
  produced the routing ids; expert compute cannot start until the reads have
  completed. The read phase is *sandwiched* between the two Metal phases —
  the GPU idles during reads with no eligible work.
- **Across layers**: route(il+1) needs act(il) = compute(il) output. So the
  next layer's route subgraph (and therefore its reads) cannot start until
  the current layer's expert compute finishes. Strictly serial.
- **Independent experts within a layer**: cache *hits* are already resident
  (zerocopy persistent slots; no I/O). Only misses read. In principle the hit
  slots' matmuls could run while miss slots are read — but the compute
  subgraph consumes the full loaded tensors (all slots) via `SET_ROWS` +
  `mul_mat_id` in slot order. Splitting hit/miss compute changes the expert
  accumulation order → output no longer byte-identical, E4 PPL 7.2601 at
  risk. The directive requires exact E4 preservation; not authorized.
- **Shared expert (shexp)**: resident and independent of the routed loads —
  the only genuinely overlap-able Metal work. But it is computed inside the
  same post-load compute subgraph; moving it to a separate subgraph is a
  graph change with trace/capture implications for ~+2% (below noise).
- **Preamble/epilogue/build**: host-side or serial; no reads in preamble;
  epilogue after the last layer.

### Maximum realistically overlap-able read time (from E3C measurements)

- Read wall (pread_wall): **48.8 ms/step** (46% of the 105.6 ms step) at W4.
- Overlap-able Metal work available during that window under exact
  semantics: **shared-expert FFN ≈ 1/9 of expert compute ≈ 2.2 ms/step**
  (8 routed + 1 shared expert per layer, equal FFN width 1024).
- Overlap-able only with graph surgery + E4 risk: cache-hit expert compute
  ≈ 5/8 of expert compute ≈ **11.8–12.6 ms/step** theoretical.
- Cross-layer: **0 ms** (routing dependency).
- ⇒ **Realistically overlap-able read time ≈ 2.2 ms/step (4.5% of the read
  wall); absolute theoretical ceiling with E4-risky surgery ≈ 12.6 ms/step
  (26%)**.

### Decode ceiling estimate

| scenario | step ms | tok/s | vs W4 (9.47) |
|---|---:|---:|---|
| E3C W4 baseline | 105.6 | 9.47 | — |
| + safe shexp overlap | ~103.4 | ~9.67 | +2% |
| + E4-risky hit/miss slot split | ~93 | ~10.7 | +13% |
| impossible limit (all compute hidden) | ~86.7 | ~11.5 | +21% (unreachable) |

The read wall itself cannot be hidden: there is not enough independent
compute to fill it (48.8 ms of reads vs ~2–13 ms of eligible compute), and
the reads are structurally on the critical path.

### Correctness of the trace arm

- EXIT=0; 0 retrieval mismatches (retr.csv: 0 non-ok rows).
- Cache behavior identical to E3C arms: 208 lookups, 61.5% hit, 80
  misses/step — scheduling unchanged, only instrumentation added.

## Findings

1. **The dependency chain is strictly serial per layer and across layers**:
   route → read → compute, with route(il+1) dependent on compute(il). No
   Metal compute can be moved into the read window without breaking routing
   semantics or the load→compute data dependency.
2. **The only safely overlap-able Metal work is the resident shared-expert
   FFN (~2.2 ms/step, +2% ceiling)** — within run-to-run noise and requiring
   a non-minimal subgraph split with trace-capture implications. Not
   justified.
3. **The meaningful overlap candidate (cache-hit expert compute, ~12 ms/step,
   +13%) requires a per-slot compute split that changes the expert
   accumulation order** — output would no longer be byte-identical and E4
   PPL 7.2601 is at risk. Forbidden by "preserve exact E4 quality".
4. **Cross-layer overlap is impossible**: route(il+1) consumes act(il).
5. **The E3C read-side parallelism already captured the recoverable
   headroom**: live effective read bandwidth 3.50 → 6.17 GB/s at W4 vs the
   E3B standalone ceiling 8.01 GB/s @4 workers; the residual gap is read
   throughput, not schedulable compute.
6. **No prototype is necessary**: the directive's prototype clause applies
   when a bounded experiment is needed to *validate* overlap. Here the
   dependency map + retained E3C measurements are conclusive; a prototype
   would either be a no-op (shexp, below noise) or violate the exact-E4
   constraint (hit/miss split).

## Classification

**Compute/I/O overlap: NOT feasible under exact routing/model semantics and
the E4 bar.** The E3D question is answered with evidence: reads are strictly
serialized between the routing subgraph and the expert-compute subgraph; the
next layer cannot start until the current layer's compute finishes; the only
independent Metal work (shared expert) is ~2 ms/step. Maximum realistically
overlap-able read time ≈ 2.2 ms of the 48.8 ms read wall (4.5%). Decode
ceiling from overlap: ~9.67 tok/s (+2%); ~10.7 tok/s only with E4-risky
per-slot surgery. Recommended: do NOT pursue compute/read overlap; the
remaining decode lever is read-side (cache density — 80 misses/step at the
4 GiB zerocopy budget — and residual read bandwidth), not compute scheduling.

## Problems / limitations

- The E3D trace arm ran slightly slower than the retained E3C W4 arm (8.6 vs
  9.4 t/s; step 116.0 vs 105.6 ms) — thermal/system variance on the same
  binary/config; per-layer proportions match E3C (route 21.6/53.5 read/20.2
  compute vs 22.1/48.8/18.9). Structure conclusions are unaffected.
- shexp overlap was quantified arithmetically (1 shared vs 8 routed experts,
  equal width) rather than measured; the conservative ceiling (+2%) is below
  noise either way.
- The hit/miss split ceiling (12.6 ms/step) assumes perfect overlap with zero
  scheduling overhead; the E4 risk is the binding constraint, not the
  ceiling.

## Decisions

- No source changes; no prototype. The E3D question is answered analytically
  from code + retained measurements, per "implement only that bounded
  prototype *if necessary*".
- Exact routing/model semantics and E4 quality preserved (nothing changed;
  trace arm reproduced identical cache behavior and 0 retrieval mismatches).
- Broader compute/kernel optimization NOT started (directive).
- Recommended next lever recorded: cache density (reduce 80 misses/step) and
  residual read bandwidth — read-side, not overlap.

## Next Phase

- E3E candidates (not started, in evidence order): (1) cache-density — larger
  effective zerocopy budget / smarter eviction to cut 80 misses/step (each
  miss = 3 preads ≈ 3.76 MB); (2) residual read bandwidth (6.17 vs 8.01 GB/s
  standalone @4); (3) promote `KIMI_EXPERT_READ_WORKERS=2/4` into the live E5
  server config (pending authorization) — the E3C operating-point finding.
- Compute/I/O overlap is recorded as NOT feasible; do not re-litigate without
  new evidence (e.g., a graph-level scheduling engine that could compute
  hit-slot matmuls mid-read *and* prove E4 PPL 7.2601 exactly — high effort,
  +13% ceiling).

## Reproduction

    # bounded per-layer trace (existing instrumentation, no code change)
    mkdir -p benchmarks/results/phase-11/e3d
    env KIMI_EXPERT_READ_WORKERS=4 \
        KIMI_STREAM_STATS_FILE=benchmarks/results/phase-11/e3d/w4-stats.csv \
        KIMI_STREAM_RETR_FILE=benchmarks/results/phase-11/e3d/w4-retr.csv \
        KIMI_STREAM_CACHE_LAYERS_FILE=benchmarks/results/phase-11/e3d/w4-layers.csv \
        KIMI_PHASE9C_TRACE=benchmarks/results/phase-11/e3d/w4-p9c.csv \
        KIMI_STREAM_EXPERTS=naive KIMI_EXPERT_CACHE_MODE=zerocopy \
        KIMI_EXPERT_CACHE_MB=4096 KIMI_STREAM_METAL_STAGE=1 \
        KIMI_STREAM_E2_DIRECT_PLACE=1 \
        llama.cpp/build-metal/bin/llama-cli -m models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
        -c 4096 -f benchmarks/prompts/phase-11-e5-engineering.md -n 48 \
        --temp 0 --seed 7 --no-display-prompt --no-conversation --single-turn -ngl 999 < /dev/null

    # per-layer decode serialization summary (decode-only rows, step >= 4)
    awk -F, 'NR>1 && $3=="moe" && $1>=4 {r+=$4; rb+=$5; lw+=$6; pw+=$8; pl+=$10; cp+=$14; n++}
      END {printf "route %.3f | readback %.3f | load_wall %.3f | pread_wall %.3f | placement %.3f | compute %.3f ms/layer (n=%d)\n",
           r/n/1000, rb/n/1000, lw/n/1000, pw/n/1000, pl/n/1000, cp/n/1000, n}' \
      benchmarks/results/phase-11/e3d/w4-p9c.csv

Artifacts: `benchmarks/results/phase-11/e3d/` (w4-p9c.csv per-layer trace,
w4-stats.csv, w4-retr.csv, w4-layers.csv, w4.out). Report: this file.
ROADMAP updated. Stopped for review.
