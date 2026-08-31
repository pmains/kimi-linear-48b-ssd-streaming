# Phase K1 working notes (draft — superseded by progress/kimi-k1-mxfp4-report.md)

Status: EXPERIMENT IN PROGRESS, 2026-08-29 evening MST

## Setup

- Baseline tree: llama.cpp `caea707b7` (frozen 9G baseline, phase-09f) —
  already contains native MXFP4 support:
  - GGML_TYPE_MXFP4 (blck 32, 1×E8M0 + 16B packed = 17 B/32 elems = 4.25 bits)
  - LLAMA_FTYPE_MOSTLY_MXFP4_MOE (38): MoE tensors (ne[2]>1) → MXFP4,
    everything else → Q8_0, output → Q8_0
  - CPU: direct `ggml_vec_dot_mxfp4_q8_0_generic` (no dequant fallback)
  - Metal: `kernel_mul_mv_mxfp4_f32` + `kernel_mul_mv_id_mxfp4_f32` (MoE)
- Model source: requantize Q4_K_M GGUF (30,061,058,720 B) → MXFP4_MOE
  via `llama-quantize --allow-requantize ... MXFP4_MOE 6`
  - CAVEAT (documented): double-quantization (Q4_K_M → f32 → MXFP4), not
    from f16. From-f16 conversion is a possible promotion follow-up.
  - Output: 27,205,376,672 B (27.21 GB) = 9.50% storage reduction.
- Hardware: Apple M5, 10 cores (4P+6E), 24 GB unified, macOS 26.5.2.
  Live server (port 18080, Q4_K_M, -ngl 0, ctx 65536) shares the machine;
  idle during the session (covariates captured per bracket).

## Correctness gate (smoke)

- MXFP4 streamed run: 64 tok, seed 7, coding prompt, cache 4096, workers 1:
  invariants PASS (0 violations), full artifact set emitted, output valid.
- Q4_K_M control: invariants PASS.
- Head-to-head (smoke, n=1): decode 6.31 vs 4.42 tok/s; prefill 16.3 vs
  11.5; SSD 255 vs 269 MB/tok; resident 6059 vs 6617 MB (noisy single runs).

## Expert-cache density math

- Per-expert: 7.08M params (3 × 2304×1024). Q4_K_M 4.28 MB/expert (measured
  inventory); MXFP4 = 7.08e6 × 17/32 = 3.76 MB/expert (+12.1% density).
- Trunk: 2.01B non-routed params. Q4_K_M mix ≈ 1.3 GB; MXFP4_MOE trunk
  Q8_0 ≈ 2.14 GB (+~0.8 GB resident — consistent with pilot resident
  delta: A 5573 vs B 5999 MB).

## Harness (new, K-track)

- tools/phasek1_run_brackets.sh — frozen 9G bracket structure (seeded slot
  order, paired S_i, env covariates, full artifact set), A=Q4_K_M w1
  control, B=MXFP4 w1 candidate; per-run k1-model.json provenance sidecar.
- tools/phasek1_analyze.py — 9G statistical gates; byte-identity/moe-baseline
  re-scoped to A runs only (cross-model deviation, documented); B gets
  presence/invariants; adds routing-divergence + per-model SSD/resident/hit
  metrics. moe.csv is seed-independent (temp 0 greedy) → A moe md5 must
  match frozen baseline da45ab... at full length (128 tok).
- Pilot (2 brackets × 64 tok): median S 1.583, A spread CV 4.2%,
  invariants 6/6 PASS, order verification PASS.

## Planned measurements

1. [RUNNING] coding-cap4 10 brackets × 128 tok (seed 20260829)
2. uncached 10 brackets × 128 tok (second session — isolates compute)
3. Wikitext-2-raw perplexity (32 chunks × 512 ctx, streamed, cache 4096)
   both models (tools/phasek1_perplexity.sh; corpus in benchmarks/data/)
4. Optional: Metal smoke test (llama-server -ngl on MXFP4) for "direct
   MXFP4 computation where supported" — note frozen protocol is CPU-only
5. progress/kimi-k1-mxfp4-report.md
