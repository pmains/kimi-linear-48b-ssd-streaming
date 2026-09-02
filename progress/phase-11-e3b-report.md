# Phase 11 E3B Report — Metal Expert-Read Throughput Deficit

## Status

**PASS — deficit explained; classification: live-read interleaving
(execution interaction), NOT read pattern, NOT Metal-vs-CPU.** The E3A
"Metal 1.86 vs CPU 4.96 GB/s" comparison does **not reproduce** under a
controlled same-binary A/B: live Metal ≈ live CPU (2.88 vs 2.95 GB/s by
per-read sum; 3.50 vs 3.29 GB/s decode-step effective). The E3A numbers
were a **cross-session artifact** (E5 live-server session vs K1 benchmark
session). A standalone no-inference/no-Metal replay of the exact Metal
(offset,length) sequence sustains **4.10 GB/s at concurrency 1 — higher
than live Metal (2.88)** — so the read pattern is not the cause. The
live-vs-standalone gap (~1.4×) is per-layer read/compute interleaving
(reads issued between graph phases, not overlapped) and affects Metal and
CPU equally. Bounded read concurrency recovers large headroom: **6.68 /
8.01 / 8.33 GB/s at concurrency 2 / 4 / 8** (~2.0–2.9× serial). No
production behavior modified. Stopped for review.

## Objective

E3A found corrected Metal and the CPU reference both read ~303 MB/token,
but Metal appeared to spend ~163 ms/token in pread (~1.86 GB/s effective)
vs ~61 ms/token (~4.96 GB/s) on CPU. E3B (authorized): determine whether
this difference comes from the read pattern itself (offset/size/order) or
from interaction/serialization with Metal execution. Instrument individual
expert reads as needed; compare Metal and CPU; retain a Metal decode read
trace; replay the exact (offset, length) sequence in a standalone
no-inference/no-Metal driver; replay with bounded concurrency 1/2/4/8.
Do not optimize production behavior.

## Method

- **Read pattern instrumentation**: existing env-gated Phase 9C per-pread
  trace (`KIMI_PHASE9C_TRACE=<path>` → `<path>.preads`) records every
  expert read as `call#,il,kind,offset,bytes,us` — offset, size, latency,
  layer, kind, and ordering, in issue order. No code changes needed.
- **Live traces**: bounded 32-token decode (same binary
  `llama.cpp/build-metal`, same prompt `phase-11-e5-engineering.md`, same
  config: naive stream, zerocopy 4096 MiB, MXFP4; only `-ngl` differs):
  Metal `-ngl 999` + `KIMI_STREAM_METAL_STAGE=1` + `KIMI_STREAM_E2_
  DIRECT_PLACE=1` (26,721 reads, 33.49 GB) and CPU `-ngl 0` (26,766 reads,
  33.55 GB) — identical routing (same seed → same 16,875 distinct offsets).
- **Controlled A/B per-step stats**: same two runs with
  `KIMI_STREAM_STATS_FILE` capture, decode-step decomposition.
- **Standalone replay** (`tools/phase11_e3b_replay.py`): loads the exact
  (offset, length) sequence from the trace, issues `os.pread` against the
  GGUF with NO inference, NO Metal, bounded thread concurrency 1/2/4/8;
  measures wall, effective GB/s, per-read latency stats, sum-of-read-times.

## Results

### 1. Controlled same-binary A/B — no Metal-vs-CPU deficit

| metric (decode steps, 31 each) | Metal | CPU |
|---|---:|---:|
| pread ms/step | 79.8 | 85.6 |
| pread % of step | 53.8 % | 45.5 % |
| effective GB/s | **3.50** | **3.29** |
| MB/step | 280 | 282 |
| pread calls/step | 223 | 225 |

Live per-read sums (full 32-token runs): Metal 2.88 GB/s, CPU 2.95 GB/s.
→ The Metal-vs-CPU "deficit" is not reproducible in a controlled
comparison; Metal is marginally faster here. E3A's 1.86 vs 4.96 compared
two different sessions (E5 live server under load vs K1 benchmark) with
different page-cache/thermal/server state.

### 2. Standalone replay of the exact Metal trace (no inference, no Metal)

| concurrency | wall s | effective GB/s | mean lat µs | p99 µs |
|---:|---:|---:|---:|---:|
| 1 | 8.17 | **4.10** | 305 | 488 |
| 2 | 5.01 | **6.68** | 375 | 662 |
| 4 | 4.18 | **8.01** | 625 | 1095 |
| 8 | 4.02 | **8.33** | 1202 | 2183 |

All 26,721 reads returned correct lengths (ok=26,721/26,721). CPU trace
replay @1: 4.00 GB/s — same pattern, same standalone throughput.

### 3. Live vs standalone

- Live Metal 2.88 GB/s (sum of per-read us) vs standalone @1 4.10 GB/s →
  **live inference interleaving costs ~1.42×** on the read path
  (per-layer read batches separated by graph build/compute; reads not
  overlapped). CPU live (2.95) vs its standalone (4.00) shows the same
  ~1.36× — this is execution interaction, not Metal-specific.
- E3A's server-side 162.9 ms/step (1.86 GB/s) is the E5 server under
  ~600 GB cumulative traffic (page-cache/thermal/server state), not a
  Metal property; the controlled Metal run here is 79.8 ms/step (3.50).

## Findings

1. **Read pattern is NOT the cause.** The exact Metal (offset, length)
   sequence replayed standalone achieves 4.10 GB/s at concurrency 1 —
   higher than live Metal's 2.88 GB/s. Sizes are uniform (1,253,376 B =
   one expert slice), offsets match CPU's exactly (same routing), ordering
   is sequential per layer.
2. **Interaction/serialization with inference execution is the live-path
   overhead (~1.4×)**, and it is backend-agnostic (Metal 2.88, CPU 2.95
   live; both ~4.0–4.1 standalone): reads are issued in per-layer batches
   between graph phases with no overlap with compute.
3. **The E3A Metal-vs-CPU deficit (1.86 vs 4.96) was a cross-session
   artifact**, not a Metal property: controlled A/B shows Metal ≥ CPU
   (3.50 vs 3.29 GB/s decode-step; 2.88 vs 2.95 live sums).
4. **The SSD has large headroom under bounded concurrency**: 8.33 GB/s at
   concurrency 8 (2.9× serial) on the same pattern — the frozen
   workers=1 read path leaves ~2× (conc 2) to ~2.9× (conc 8) on the table.

## Classification

**Read-interleaving / execution interaction (backend-agnostic), with
pattern exonerated and Metal-vs-CPU exonerated.**

- Deficit vs E3A's claim: cross-session artifact (no Metal-vs-CPU deficit
  in controlled A/B).
- Live-path overhead vs isolated pattern: ~1.4×, from per-layer
  read/compute interleaving (no read overlap), affecting Metal and CPU
  equally.
- Headroom available: bounded read concurrency recovers 1.6× (@2) to
  2.9× (@8) of the serial read throughput (4.10 → 8.33 GB/s standalone).

Quantified: standalone serial pattern throughput 4.10 GB/s; live Metal
2.88 GB/s (1.42× interleaving cost); SSD sustains 8.33 GB/s at
concurrency 8.

## Problems / limitations

- The E3A "Metal deficit" numbers are superseded: they compared two
  different sessions. The controlled A/B and the standalone replay are the
  authoritative comparisons.
- The standalone replay uses Python `os.pread`; wall includes thread
  scheduling overhead at high concurrency, but the @1 numbers (4.0–4.1)
  are clean and the concurrency trend is monotonic.
- Live trace captures were 32-token runs (bounded per directive); the
  pattern is representative (16,875 distinct offsets, all 26 layers).

## Decisions

- No code changes; no production behavior modified (directive).
- Reused existing Phase 9C per-pread trace instrumentation (no new
  instrumentation added).
- E3B classification recorded: execution interleaving (backend-agnostic),
  pattern and Metal-vs-CPU exonerated; quantified 4.10 GB/s standalone @1
  vs 2.88 GB/s live Metal (1.42×), 8.33 GB/s @8 headroom.
- Optimization NOT started. Candidate E3C directions (from evidence, in
  contribution order): (1) overlap reads with compute (async/prefetch —
  recovers the 1.4× interleaving cost), (2) bounded read concurrency
  (workers 2–4 → up to ~2× serial read throughput), (3) note: NOT a
  Metal-vs-CPU issue, so Metal-specific read work is not indicated.

## Reproduction

    # live traces (bounded 32-token decode; Metal and CPU)
    env KIMI_PHASE9C_TRACE=benchmarks/results/phase-11/e3b/metal-trace \
        KIMI_STREAM_EXPERTS=naive KIMI_EXPERT_CACHE_MODE=zerocopy \
        KIMI_EXPERT_CACHE_MB=4096 KIMI_STREAM_METAL_STAGE=1 \
        KIMI_STREAM_E2_DIRECT_PLACE=1 \
        llama.cpp/build-metal/bin/llama-cli -m models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
        -c 4096 -f benchmarks/prompts/phase-11-e5-engineering.md -n 32 \
        --temp 0 --seed 7 --no-display-prompt --no-conversation --single-turn -ngl 999 < /dev/null
    # same without the two KIMI_STREAM_* envs and with -ngl 0 for CPU

    # controlled per-step stats A/B (KIMI_STREAM_STATS_FILE=...)

    # standalone replay (no inference, no Metal)
    python3 tools/phase11_e3b_replay.py \
        benchmarks/results/phase-11/e3b/metal-trace.preads \
        models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf 1 2 4 8

Artifacts retained: `benchmarks/results/phase-11/e3b/` (metal-trace.preads,
cpu-trace.preads, ctrl-metal.csv, ctrl-cpu.csv, replay-metal-trace.txt,
replay-cpu-trace.txt); driver `tools/phase11_e3b_replay.py`; report this
file; ROADMAP updated.
