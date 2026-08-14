# Phase 05 Report

> **OWNER CORRECTION (2026-08-14)**: this phase was begun before Phase 4's
> acceptance criteria fully passed. Phase 4 acceptance 2 (resident memory
> measurably lower) is **not met** (see `phase-04-report.md`), so per the
> owner's decision tree Phase 5 should not have started. The evidence below is
> correct but is **provisional** — treat it as validation data pending a real
> Phase 4 close, not as a completed Phase 5. Phase 4D (pageable model loading,
> see `ROADMAP.md`) is that close: once its oracle passes with resident memory
> reduced, Phase 5 is re-validated on the low-residency streamer and becomes
> authoritative. **UPDATE (2026-08-14)**: Phase 5 was re-validated under the
> virtualized loader — all three comparisons (10-token, 64-token, alt prompt)
> are bit-identical (max|Δ| = 0) with the expert parents not materialized
> (see `phase-04d-report.md`). The correctness evidence is now authoritative;
> the report's overall status remains provisional only because Phase 4
> acceptance 2 (process-level RSS below conventional) is still open.

## Status

**PASS (provisional)** — streamed execution is bit-identical to conventional
execution across longer generations and a second prompt, well inside the
documented tolerances.

## Objective

Prove that expert streaming changes storage behavior, not model behavior:
compare streamed execution against the conventional reference for router
decisions, selected experts, routing weights, intermediate activations, logits,
and generated tokens, over more than the minimal 10-token checkpoint.

## Changes

No runtime code changes were required for correctness (Phase 4's
`c111f595f` — allocate loaded experts in the parent CPU-repack buffer — already
made the paths bit-identical). One supporting fix landed during this phase:

- llama.cpp `60dc29e64` — free per-layer loaded-expert backend buffers (they
  were scheduler-external and leaked ~1 GB/step, OOMing beyond ~10-20 tokens).
  Deferred the free to the next layer's scheduler reset so buffers are never
  freed while still referenced. This is a memory fix, not a correctness fix.

## Results

Three deterministic comparisons (`--temp 0 --seed 1`, `-ngl 0` CPU), each
conventional vs streamed via `tools/phase04_compare.py`:

| comparison | prompt | tokens | retrieval | router rows | activations/logits | generated text |
|---|---|---|---|---|---|---|
| Phase 4 A/B/C | phase-04-ref (coding) | 10 | 36,288/36,288 exact | 1,512 bit-identical | bit-identical (max\|d\|=0) | — |
| longer generation | phase-04-ref (coding) | 64 | 69,984/69,984 exact | 2,916 bit-identical | bit-identical (max\|d\|=0) | identical |
| additional prompt | phase-03-reasoning-pumps | 32 | 169,416/169,416 exact | 7,059 bit-identical | bit-identical (max\|d\|=0) | identical (567 chars) |

Every captured boundary (`l_in`, `attn_out`, `ffn_inp`, `ffn_normed`,
`router_logits`, `router_weights`, `moe_up/gate/down`, `moe_out`, `l_out`,
`logits`) is bit-identical at every layer of every execution. Temperature-0
sampling is deterministic (identical seed/sequence), and because the logits are
bit-identical, the greedy argmax produces identical tokens at every step — the
generated text is byte-identical (verified for both the 32-token reasoning run
and the 64-token coding run).

## Tolerances

The comparison is **conventional-vs-streamed**, both of which quantize with the
same Q4_K/Q6_K weights and (after the Phase 4 fix) run the *same* `mul_mat_id`
kernels over the same byte layout. The correct tolerance is therefore
**bit-identity (max|d| = 0)**, which is far tighter than any Q4_K-vs-fp32
tolerance (~1e-2 relative). The 1e-5 activation / 1e-5 logits / 1e-6 mean-logits
criteria in the comparator are met with ~6+ orders of magnitude of margin
(observed max|d| = 0.0).

## Problems

- The buffer leak (`60dc29e64`) blocked generations longer than ~10-20 tokens;
  fixed and re-verified (64-token run completes and remains bit-identical).
- Resident memory vs conventional (Phase 4 acceptance 2) is *still not reduced*:
  the streamed executor reuses the unchanged model load, so the full 256-expert
  collection (repacked, ~28 GB) remains resident. The streaming change affects
  only the compute path (which experts are read into the small per-step buffer),
  not model residency. This is a Phase 4 gap, documented there, not a Phase 5
  correctness issue.

## Decisions

- Bit-identity is the reference criterion, not a loose Q4_K tolerance, because
  both paths share quantization and kernels.
- The buffer free is deferred to the scheduler reset rather than freed
  immediately after compute, to respect the scheduler's external-input lifetime.

## Next Phase

Phase 6 (bounded expert cache). Phase 5's equivalence gives Phase 6 a clean
baseline: any cache/eviction/prefetch added must preserve bit-identity against
the conventional oracle. Note for Phase 6: a cache should store already-repacked
expert bytes so a hit elides both the SSD pread *and* the ~240 ms re-repack
(see `phase-04-report.md` 4B decomposition).

## Reproduction

```bash
# longer generation (coding prompt, 64 tokens)
tools/phase04_run_capture.sh    benchmarks/results/traces/phase-05-conv-long    benchmarks/prompts/phase-04-ref.md 64 1
tools/phase04_run_streamed.sh   benchmarks/results/traces/phase-05-stream-long  benchmarks/prompts/phase-04-ref.md 64 1 naive
python3 tools/phase04_compare.py \
    benchmarks/results/traces/phase-05-conv-long/act.bin    benchmarks/results/traces/phase-05-conv-long/moe.csv \
    benchmarks/results/traces/phase-05-stream-long/act.bin  benchmarks/results/traces/phase-05-stream-long/moe.csv \
    benchmarks/results/traces/phase-05-stream-long/retr.csv

# additional prompt (reasoning, 32 tokens)
tools/phase04_run_capture.sh    benchmarks/results/traces/phase-05-alt-conv    benchmarks/prompts/phase-03-reasoning-pumps.md 32 1
tools/phase04_run_streamed.sh   benchmarks/results/traces/phase-05-alt-stream  benchmarks/prompts/phase-03-reasoning-pumps.md 32 1 naive
python3 tools/phase04_compare.py \
    benchmarks/results/traces/phase-05-alt-conv/act.bin    benchmarks/results/traces/phase-05-alt-conv/moe.csv \
    benchmarks/results/traces/phase-05-alt-stream/act.bin  benchmarks/results/traces/phase-05-alt-stream/moe.csv \
    benchmarks/results/traces/phase-05-alt-stream/retr.csv
```

Environment: Apple M5, 24 GB unified memory, macOS 26.5.2; llama.cpp at
`60dc29e64`; model `moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`.
