# Phase 11 — kda_g1 (delta-net decay gate) Arithmetic Isolation

## Status

PASS — **kda_g1 classified as benign backend arithmetic (Case A class),
NOT a candidate Metal defect.** The layer-0 `kda_g1` CPU-vs-Metal
divergence (max|d| = 1.438, the largest absolute boundary value seen in
localization) is a small **relative** divergence: 0.66% on values of
magnitude ±217 (g1 reaches ±217 because A = `-exp(A_log)` is huge, up to
−201). It originates at the `f_a` matmul from the CPU path's q8_0
activation quantization (same documented `vec_dot_type=Q8_0` mechanism
as the Q/K/V Case A), propagates through `f_b_out`/softplus, and is
amplified by the per-head A constants. Metal is the near-exact path
(f64 agreement ≤4.7e-7 at every matmul stage; production Metal within
1.3e-3 relative of f64 at g1 vs CPU's 7.4e-3). Stopped for review per
directive; next downstream boundary identified (first mxfp4 expert
mul_mat / E2 slot-integrity). No kernels modified, no E3.

## Objective

Determine whether the large CPU-vs-Metal divergence at layer-0 `kda_g1`
(delta-net decay gate, substitute report max|d| = 1.438) is an expected
backend arithmetic difference like the layer-0 Q/K/V projection, or
evidence of the first materially incorrect Metal computation associated
with the E4 quality catastrophe. Identify the exact production
operations and tensor types feeding the boundary; reconstruct/compare
the CPU and production-Metal arithmetic from identical retained inputs
and weights; measure individual operation outputs rather than treating
`kda_g1` as one black box.

## Production chain (from `src/models/kimi-linear.cpp`, `build_kda`)

    f_a     = mul_mat(ssm_f_a, cur)          # q8_0 [2304,128] x f32 [2304]
    f_b_out = mul_mat(ssm_f_b, f_a)          # q8_0 [128,4096] x f32 [128]
    z       = f_b_out + ssm_dt.bias          # f32 [4096]
    s       = softplus(z)
    g1      = s * A[head]                    # A = ssm_a = -exp(A_log), per head

Tensor facts (GGUF metadata parsed in-probe, cross-checked against the
retained repack dump): `blk.0.ssm_f_a.weight` q8_0 [2304,128] abs_off
917,219,104; `blk.0.ssm_f_b.weight` q8_0 [128,4096] abs_off 917,532,448;
`blk.0.ssm_dt.bias` f32 [4096]; `blk.0.ssm_a` f32 [1,32] (A values
−0.226 … −201.2, i.e. decays up to 201×). Input `cur` = `attn_norm`
capture (bit-identical CPU vs Metal, asserted). Retained traces:
`benchmarks/results/phase-k1/localize/{cpu,metal}/act.bin`, layer-0
`attn_norm` + `kda_g1` records, first prefill step (exec 2, n_tokens
233, ph=0, sp=0, token-0 column).

## Changes

- `tools/phase11_g1_probe.py` (new, retained): full kda_g1 chain
  reconstruction in three arithmetic paths — f64 reference, CPU
  vec_dot replica (q8_0 activation quantization per 32-block, f32
  within-block accumulation), and Metal `kernel_mul_mv_ext_q8_0_f32`
  replica (f32 activations, residue-class accumulation, simd_shuffle
  tree) — with per-stage comparison (f_a, f_b_out, z, s, g1), a beta
  boundary self-check (`kda_beta` = sigmoid(mul_mat(ssm_beta, cur))),
  a per-head empirical A-estimate (prod_metal / s_met per head),
  production-vs-replica comparisons, and mismatch-row localization.
- `tools/phase11_arith_probe.py` — **latent fp16 subnormal-decode bug
  fixed** (see Problems). Re-run is byte-identical to the retained
  `run-rows0-15-cpu-vs-metal.txt`, so the earlier Q/K/V Case A
  conclusion is unaffected.
- `benchmarks/results/phase-11/g1-probe/run.txt` (retained): full
  probe output (final, post-fix).

## Results

### Machinery validation

- attn_norm input: bit-identical CPU vs Metal (assert passes).
- Beta self-check: production cpu-vs-metal kda_beta max|d| = 1.37e-3;
  replicas reproduce production to ≤3.3e-4 (beta values ~O(0.5–1),
  i.e. ~1e-3 relative — same residual class as kda_g1, not a
  kda_g1-specific anomaly).
- Per-head empirical A estimate: A_est(median) / A_loaded = 0.9993 …
  0.9999 across all 32 heads — the chain structure (row→head mapping,
  softplus, A multiply) is confirmed against production to ~0.04%.

### Stage decomposition (f64 ref vs CPU replica vs Metal replica)

| stage | cpu vs metal | f64 vs cpu | f64 vs metal |
|---|---:|---:|---:|
| f_a | 8.024e-03 | 8.024e-03 | 4.722e-07 |
| f_b_out | 2.139e-02 | 2.139e-02 | 4.743e-07 |
| z (+bias) | 2.139e-02 | 2.139e-02 | 4.743e-07 |
| s (softplus) | 9.250e-03 | 9.250e-03 | 2.328e-07 |
| g1 (*A) | 1.175e+00 | 1.175e+00 | 3.360e-05 |

Metal replica tracks f64 to ≤4.7e-7 at every matmul stage (≤3.4e-5 at
g1 after the A amplification); the CPU replica carries the error from
the first matmul (f_a: 8.0e-3), which is the q8_0 activation
quantization of `cur` in the CPU vec_dot path — the identical mechanism
and magnitude class as the Q/K/V Case A finding.

### Production agreement (post-fix)

| comparison | max\|d\| | rel@max |
|---|---:|---:|
| PRODUCTION cpu vs metal kda_g1 | 1.438370e+00 | 6.609e-03 |
| prod_cpu vs cpu replica | 4.398e-01 | 2.030e-03 |
| prod_metal vs metal replica | 2.747e-01 | 1.336e-03 |
| prod_metal vs f64 | 2.747e-01 | 1.336e-03 |
| prod_cpu vs f64 | 1.615e+00 | 7.414e-03 |
| cpu-replica vs metal-replica | 1.175e+00 | 5.395e-03 |
| mismatch rows (|prod−replica|>1) | **0 / 4096** | — |

Production g1 magnitudes: amax 217.8 (A up to −201). So the 1.438
production CPU-vs-Metal gap is 0.66% relative; the replica
CPU-vs-Metal gap (1.175, 0.54% relative) reproduces the production gap
in class and magnitude. Production Metal is within 1.3e-3 relative of
f64; production CPU is 7.4e-3 relative — Metal is ~5.5× closer to the
reference. W_fb dequantized weights are sane (max|w| = 0.62); the
earlier "huge weights ~123" were the fp16 subnormal-decode artifact.

## Problems

- **Latent fp16 subnormal decode bug (probe-side, now fixed):**
  `fp16_to_fp32` in the arith probe decoded subnormal fp16 scales 2^14×
  too large (returned 0.969 for the subnormal 0x3e0 instead of 5.9e-5).
  The g1 probe initially inherited it, producing phantom "huge" W_fb
  weights (max ~123) and 43 phantom mismatch rows where production
  implied f_b_out ≈ 0. Fixed to ggml `ggml_fp16_to_fp32` semantics in
  both probes. The arith-probe re-run is byte-identical to the retained
  output (attn_q rows 0–15 contain no subnormal scales), so the earlier
  Q/K/V Case A conclusion is unchanged. All kda_g1 numbers above are
  post-fix.
- The metal-replica reproduction of production Metal is 1.3e-3 relative
  at g1 (vs ≤1.2e-7 for the Q/K/V matmul). Residual is consistent with
  kernel-variant accumulation-order detail for these shapes (f_a
  ne01=128, f_b ne00=128) plus f32 softplus; it is 100× below the
  production CPU-vs-Metal gap and does not change the classification.

## Decisions

- **kda_g1 divergence is benign backend arithmetic** (Case A class):
  CPU q8_0 activation quantization at f_a (and f_b) amplified by the
  large per-head A constants (up to −201). Not a Metal defect; not the
  E4 cause. Metal remains the correctness anchor at this boundary.
- Layer-0 KDA boundaries are now fully cleared: Q/K/V projection
  (Case A), kda_g1 (Case A), beta/gate/delta_out all ≤ ~1e-3 class.
- Next downstream boundary (identified, NOT run): the **first mxfp4
  expert `mul_mat`** (MoE experts are the model's only mxfp4 tensors
  per the dequant census — the original "Metal MXFP4 mul_mat" suspect
  was never actually probed after the premise correction), and/or the
  **E2 zerocopy slot-integrity check** (wrong-bytes-to-slot would
  produce garbage without any kernel being wrong).
- No kernels modified; no E3; STOPPED for review per directive.

## Next Phase

1. Probe the first mxfp4 expert `mul_mat` arithmetic with the same
   per-block/cumulative method (weights from GGUF, activations from
   retained moe/act traces, f64 + CPU-replica + Metal-replica).
2. If authorized, run the E2 zerocopy slot-integrity check (verify
   packed expert bytes land in the correct slots/strides for all
   layers).
3. Re-evaluate the E4 ×218k PPL explanation after (1)/(2): remaining
   hypotheses are a later-layer Metal op defect, a streamed
   data-path defect, or routing-argmax amplification (weakened by K1:
   91.84% routing divergence between two CPU quants with +1.37% PPL).

## Reproduction

    # g1 probe (reads retained traces; GGUF offsets parsed in-probe)
    python3 tools/phase11_g1_probe.py \
        models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
        benchmarks/results/phase-k1/localize/cpu/act.bin \
        benchmarks/results/phase-k1/localize/metal/act.bin
    # -> benchmarks/results/phase-11/g1-probe/run.txt

    # arith probe unchanged-verification (byte-identical to retained):
    python3 tools/phase11_arith_probe.py \
        models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
        829182112 benchmarks/results/phase-11/arith-probe/captures/l0_cpu_attn_norm.npy \
        benchmarks/results/phase-11/arith-probe/captures/l0_metal_attn_norm.npy \
        0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15
    # diff vs benchmarks/results/phase-11/arith-probe/run-rows0-15-cpu-vs-metal.txt: identical
