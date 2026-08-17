# Stage 8 — Verify Usable Context Scaling under the Native NoPE Mechanism

## Status

NOT STARTED — protocol defined 2026-08-17 (operator definition). Runs only
after Stage 7 is frozen (PASS, 2026-08-17, `85882ee`).

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

## Evidence

_(pending)_

## Result

_(pending)_

## Blockers / Follow-up

- Runs only after the Stage 7 verdict (PASS, frozen 2026-08-17).
- Parked (not part of Stage 8): launchd re-arm/hardening, faster cold prefill,
  MXFP4/custom kernels, manager/worker design, model-tier routing, Qwen.
