# Stage 8 — Verify Usable Context Scaling under the Native NoPE Mechanism

## Status

**MILESTONE REACHED (2026-08-17)** — 32K authoritative PASS, 64K PASS, and
128K PASS (first major milestone). All four gates pass at every completed rung;
needle retrieval exact at each; zero allocator anomalies. The ladder is paused
at 128K per the protocol's stop-and-characterize rule and the AGENTS.md STOP
point; higher rungs (256K → 512K → 1,048,576) are aspirational and not started.
Runs use the frozen live runtime (`runtime/live`, COMMIT `0a6b2df63`), port
18081, with `--ctx-size` as the only changing knob.

## Objective

Determine, on the current Kimi Linear implementation, how far usable context
scales toward the declared 1,048,576-token native context while preserving
correctness, stable memory behavior, cache semantics, and acceptable service
operation — using the **native NoPE mechanism only**, with **no positional
knob changes**.

Stage 7 established the architectural reason: Kimi Linear is natively NoPE;
both the reference implementation and the llama.cpp path derive ordering from
causality (causal mask in the 7 MLA layers; causal conv1d + recurrent delta-net
scan in the 20 KDA layers). RoPE, YaRN, and every rope-based knob are
NOT APPLICABLE. Stage 8 is therefore a **capacity, correctness, and
memory-behavior validation problem**, not a positional-extrapolation problem.

## Acceptance question

> How far can the current Kimi Linear implementation scale toward its declared
> 1,048,576-token native context while preserving correctness, stable memory
> behavior, cache semantics, and acceptable service operation?

## Ladder

Increasing contexts with no positional knob changes (only `--ctx-size`):

    current validated context (32K) → 64K → 128K → 256K → 512K → 1,048,576

Each rung is a **diagnostic checkpoint, not a mandatory target**. If resource
growth reveals a hard architectural or implementation limit, stop and
characterize it before proceeding.

## Four independent gates (per rung)

1. **Allocation / startup** — the requested context provisions successfully.
2. **Prompt ingestion / prefill correctness** — long prompts prefill correctly.
3. **Post-prefill generation correctness** — generation after long-context
   prefill is correct.
4. **State/cache behavior after long-context use** — KV cache, KDA recurrent
   state, expert cache, and slot behavior remain stable.

## Failure classes (never collapsed into one FAIL)

- **Allocation failure** — the memory model or implementation cannot provision
  the requested context.
- **Correctness failure** — it provisions, but long-context semantics break.
- **Performance failure** — correctness holds, but latency or memory pressure
  makes that rung operationally unusable.

## Memory accounting (recorded separately, where observable)

- KV cache (MLA layers)
- recurrent/KDA state (KDA layers)
- model / expert cache
- total process RSS

## Scaling law

Measure the actual memory-vs-context curve rather than assuming it. With only
7 MLA layers carrying conventional attention-state costs and 20 KDA layers
using recurrent state, the observed slope may differ substantially from a
standard transformer context calculator. Stage 8 establishes that curve.

## Protocol (per rung)

1. Capture a baseline run at the current known-good configuration (32K) first.
2. Use the native NoPE configuration and one exact runtime knob set
   (`--ctx-size` only; no positional knobs).
3. Run the fixed rung ladder with the same caveman request shape at every rung;
   keep the rest of the agent configuration unchanged.
4. Apply the four gates; classify any failure per the three classes above.
5. Record memory separately (KV / KDA state / expert cache / total RSS).
6. Stop at the first rung that fails, times out, or pushes memory into an
   unusable state; characterize it before proceeding.
7. Report each rung in `service-progress/` and save machine-readable outputs
   under `benchmarks/results/` (e.g. `benchmarks/results/no-pe-ladder/`).

Treat two ceilings separately:

- `runtime ceiling`: crashes, allocation failure, or unacceptable memory pressure
- `useful-context ceiling`: caveman runs, but can no longer reliably retrieve or
  use the expanded context

## Measurements per rung

- prompt token count at the boundary
- prompt-eval duration
- first-token latency
- resident memory (split per the four buckets above)
- runtime failure mode, if any
- useful-context result from a small needle/retrieval test
- whether caveman still completes a normal request at that size

## PASS condition

- caveman sustains a measured context target materially larger than the current
  baseline, with the native NoPE mechanism and no positional knobs
- the agent path still works at that size
- the strategy is documented with exact runtime knobs and measured limits
- the useful-context test passes at the milestone rung
- the empirical memory-vs-context curve is recorded

If 1,048,576 is not practical on this machine, record the highest sustainable
value and stop there. Do not guess a higher number without evidence. The
current validated context (32K) is the floor; 128K remains a milestone marker,
with higher rungs aspirational pending measured evidence.

## Rung results (2026-08-17)

All runs: frozen live binary `runtime/live/bin/llama-server` (COMMIT
`0a6b2df63`), `models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`,
`KIMI_STREAM_EXPERTS=naive KIMI_EXPERT_CACHE_MB=4096
KIMI_EXPERT_CACHE_MODE=zerocopy`, `-ngl 0 --no-mmap --parallel 1 -lv 4`, port
18081; only `--ctx-size` changes between rungs.

| rung | run dir (UTC) | PID | actual prompt tokens | prefill ms (tok/s) | needle | KV cache | KDA recurrent | expert cache | steady RSS | peak RSS during prefill |
|---|---|---|---|---|---|---|---|---|---|---|
| 32K (authoritative rerun) | `2026-08-17T19-58-31Z-ctx-32768` | 47800 | 23,149 | 782,312.45 (29.59) | yes | 252.00 MiB (32,768 cells, 7 MLA) | 42.81 MiB (R 2.81 + S 40.00) | 4096 MiB zerocopy | 4,390,720 KB (4.39 GB) | 6,240,688 KB (6.24 GB) @ 13:11:47 (prefill end) |
| 64K | `2026-08-17T20-16-36Z-ctx-65536` | 53739 | 46,214 | 1,741,158.61 (26.54) | yes | 504.00 MiB (65,536 cells, 7 MLA) | 42.81 MiB (unchanged) | 4096 MiB zerocopy | 4,335,248 KB (4.33 GB) | 6,138,096 KB (6.14 GB) @ 13:16:47 (prefill start); secondary 5,945,584 KB @ 13:45:51 |
| 128K (milestone) | `2026-08-17T20-48-46Z-ctx-131072` | 66405 | 92,344 | 4,452,334.16 (20.74) | yes | 1008.00 MiB (131,072 cells, 7 MLA) | 42.81 MiB (unchanged) | 4096 MiB zerocopy | 5,464,400 KB (5.46 GB) | 7,832,992 KB (7.83 GB) @ 15:03:15 (prefill end) |

Additional rung provenance:

- **32K partial run — HARNESS-INVALIDATED** (not authoritative):
  `2026-08-17T19-31-29Z-ctx-32768`. Driver died after probe-b because the
  harness was edited mid-run; implementation evidence preserved (prefill
  802,676.09 ms @ 28.84 tok/s, needle retrieved, peak RSS 5,781,552 KB,
  tail-only capture). Only the clean rerun above is the 32K authoritative point.
- **ctx=1024 validation runs** (`...T19-26-11Z/19-26-30Z/19-30-11Z-ctx-1024`):
  harness validation only, not ladder rungs.

### Empirical memory-vs-context curve

- **KV cache (7 MLA layers): linear** — 252.00 → 504.00 → 1008.00 MiB,
  exactly 2× per context doubling (32,768 → 65,536 → 131,072 cells).
- **KDA recurrent state: flat** — 42.81 MiB at every rung (R 2.81 + S 40.00
  MiB), context-independent, confirming the Stage 7 prediction.
- **Expert cache: constant** — 4096 MiB zerocopy at every rung.
- **Steady RSS:** 4.39 GB (32K) → 4.33 GB (64K) → 5.46 GB (128K).
- **Peak RSS during prefill:** 6.24 GB (32K) → 6.14 GB (64K) → 7.83 GB
  (128K). Peak phase varies (prefill start vs end), which is why full-window
  sampling is the correct interpretive rule; steady-state alone understates
  transient pressure.
- **Prefill throughput:** 29.59 → 26.54 → 20.74 tok/s (gentle decline).

## Evidence

- Per-rung `results.json` (classification=PASS; gates 1–4 pass; needle
  retrieved; 0 allocator anomalies; benign il=26 loaded-ids warnings 47/92/182):
  - `dev-openclaw/state/stage8/2026-08-17T19-58-31Z-ctx-32768/results.json`
  - `dev-openclaw/state/stage8/2026-08-17T20-16-36Z-ctx-65536/results.json`
  - `dev-openclaw/state/stage8/2026-08-17T20-48-46Z-ctx-131072/results.json`
- Machine-readable copies committed under `benchmarks/results/no-pe-ladder/`
  (`ctx-32768.json`, `ctx-65536.json`, `ctx-131072.json`).
- Per-run artifacts (server.log, driver.log, rss-samples.tsv, needle.txt,
  probe payloads/responses) in each run dir.
- Run dirs are gitignored (`dev-openclaw/`); the committed copies are the
  authoritative machine-readable record.

## Result

Stage 8 milestone met: caveman sustains **128K usable context** with the
native NoPE mechanism and no positional knobs — 4× the 32K validated baseline
(23,149 → 92,344 actual prompt tokens at the boundary). The agent path still
works at that size (fixed ok probe + needle retrieval + generation gates). The
empirical memory curve is recorded (above): KV linear from the 7 MLA layers
only, KDA recurrent state flat, expert cache bounded, peak RSS 7.83 GB at
128K — comfortably within the 24 GB machine. No allocation, correctness, or
performance failure encountered through the 128K rung.

## Blockers / Follow-up

- Ladder **paused at 128K** (protocol stop-and-characterize rule + AGENTS.md
  STOP point). 256K → 512K → 1,048,576 remain aspirational; the peak-RSS rule
  becomes decisive at 256K+. Launch only on explicit instruction.
- 64K peak RSS (6.14 GB) slightly below 32K (6.24 GB) because the peak phase
  differs (prefill start vs end) — expected with full-window sampling, not an
  anomaly.
- Completed separately (not part of Stage 8): Qwen3-8B interactive/manager
  tier — see `progress/qwen-manager-tier-report.md` (commit `00f07d0`).
- Still parked: launchd re-arm/hardening, faster cold prefill, MXFP4/custom
  kernels, manager/worker design, model-tier routing.
