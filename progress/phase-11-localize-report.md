# Phase 11 Metal Numerical Localization — First Bounded Experiment

## Status

PASS (first divergence causally localized). The earliest operation where
CPU and Metal cease to agree is the **layer-0 Q/K/V projection `mul_mat`
(MXFP4 weights)** in the KDA attention path, with **bit-identical inputs**
— the "identical inputs + different CPU/Metal operation result" case, not
input propagation. No kernels modified; diagnostic-only trace captures
only (env-gated, default path byte-identical). E2/E4 evidence frozen.
Stopped for review per directive — E3 NOT begun.

## Objective

Identify the earliest operation responsible for the CPU-vs-Metal
numerical discrepancy and determine whether it is expected backend
rounding or evidence of an incorrect operation/data interpretation.
Start from the E1b localization (layer-1 `attn_out`, max|Δ| ≈ 1.2e-3)
and work backward through the attention computation feeding it,
comparing CPU and Metal at each operation boundary, distinguishing:

1. identical inputs + different CPU/Metal operation result (op-level);
2. already-different inputs propagating through a reasonable operation.

Candidates were NOT assumed: accumulation precision/order, Metal
attention/matmul kernels, MXFP4 dequant, layout/stride, block-scale,
sync/lifetime were all treated as hypotheses to be tested against
boundary evidence.

## Changes (diagnostic-only, env-gated)

- `llama.cpp/src/models/kimi-linear.cpp` — added `llm_trace_act_capture`
  calls (existing env gate `KIMI_TRACE_ACT`; inert by default, warmup
  excluded, default path byte-identical) at every KDA attention boundary
  feeding `attn_out`:
  - `attn_norm` — the normalized input to the attention block
  - `kda_Q_proj` / `kda_K_proj` / `kda_V_proj` — the projection
    `mul_mat` outputs (captured inside `causal_conv1d`, BEFORE conv1d)
  - `kda_Qcur` / `kda_Kcur` / `kda_Vcur` — post-conv1d + silu
  - `kda_g1`, `kda_beta` — KDA decay gate and mixing coefficient
  - `kda_Q_norm` / `kda_K_norm` — l2-normalized Q/K
  - `kda_delta_out`, `kda_new_state` — delta-net output + state
  - `kda_normed`, `kda_gate`, `kda_gated` — o_norm/gating
  - `kda_out` — the wo projection (pre-residual; equals `attn_out`)
  - `causal_conv1d` signature extended with il/sp/ph for the internal
    projection captures (static function, 3 call sites).
- `tools/phase11_localize_run.sh` — exact driver: CPU arm (streamed
  MXFP4, `-ngl 0`) and Metal arm (`-ngl 999` +
  `KIMI_STREAM_METAL_STAGE=1` + `KIMI_STREAM_E2_DIRECT_PLACE=1`, the
  frozen E2 path used in E4), same 64-tok deterministic config as E1b.
- `tools/phase11_localize_analyze.py` — TCAT parser + per-boundary
  stats (n, max|d|, mean|d|, RMS, rel@max, NaN/Inf counts, worst
  values), aligned by (phase, start_pos, il, name), with a backward
  walk from `attn_out` to `l_in`.

No kernel, math, or default-path changes. Fork commit: see git log.

## Results

### Boundary comparison — layer 0, first prefill step (ph=0, sp=0, n_tokens=2)

| boundary | n | max|d| | mean|d| | RMS | rel@max | NaN/Inf |
|---|---|---|---|---|---|---|
| l_in (embeddings) | 2304 | **0.000e+00** | 0 | 0 | 0 | 0/0 |
| attn_norm | 2304 | **0.000e+00** | 0 | 0 | 0 | 0/0 |
| **kda_Q_proj** | 4096 | **9.797e-03** | 1.596e-03 | 2.062e-03 | 1.63e-02 | 0/0 |
| **kda_K_proj** | 4096 | **9.085e-03** | 1.601e-03 | 2.064e-03 | 2.20e-02 | 0/0 |
| **kda_V_proj** | 4096 | **1.021e-02** | 1.734e-03 | 2.201e-03 | 1.38e-02 | 0/0 |
| kda_Qcur (post-conv+silu) | 4096 | 9.318e-04 | 4.628e-05 | 8.193e-05 | 5.45e-03 | 0/0 |
| kda_Kcur | 4096 | 1.667e-03 | 4.090e-05 | 7.990e-05 | 4.55e-03 | 0/0 |
| kda_Vcur | 4096 | 1.620e-03 | 7.583e-05 | 1.269e-04 | 1.09e-02 | 0/0 |
| kda_g1 | 4096 | 1.438e+00 | 1.693e-02 | 5.235e-02 | 6.61e-03 | 0/0 |
| kda_beta | 32 | 1.370e-03 | 4.911e-04 | 6.099e-04 | 4.95e-03 | 0/0 |
| kda_Q_norm | 4096 | 4.134e-03 | 2.901e-04 | 4.685e-04 | 6.74e-03 | 0/0 |
| kda_K_norm | 4096 | 4.664e-03 | 2.638e-04 | 4.492e-04 | 1.27e-02 | 0/0 |
| kda_delta_out | 4096 | 7.381e-06 | 3.085e-07 | 7.045e-07 | 1.28e-02 | 0/0 |
| kda_new_state | 16384 | 6.664e-04 | 5.678e-06 | 1.340e-05 | 5.63e-02 | 0/0 |
| kda_normed | 4096 | 1.345e-04 | 5.154e-06 | 1.133e-05 | 1.61e-02 | 0/0 |
| kda_gate | 4096 | 6.752e-03 | 4.362e-04 | 6.260e-04 | 1.20e-02 | 0/0 |
| kda_gated | 4096 | 5.649e-05 | 2.443e-06 | 5.387e-06 | 1.11e-02 | 0/0 |
| kda_out / attn_out | 2304 | 1.111e-04 | 1.336e-05 | 1.675e-05 | 8.74e-03 | 0/0 |

Layer 1 (ph=0, sp=0): `l_in` is already divergent at 1.064e-03 (rel
7.13e-03) — i.e. the seed propagates INTO layer 1 from layer 0's
output; layer-1 `attn_norm` amplifies to 3.539e-03 and the layer-1
projections show the same pattern (kda_Q_proj 1.457e-02, rel 1.44e-02).
This confirms working backward from layer-1 `attn_out` leads to layer 0,
as the directive anticipated (early-layer seed, not late-layer routing).

### Classification (the directive's primary question)

**Case 1 — identical inputs + different CPU/Metal operation result.**
At layer 0, `l_in` and `attn_norm` are bit-identical (max|d| = 0.0), and
the first non-zero divergence appears exactly at the Q/K/V projection
`mul_mat` outputs (`kda_Q/K/V_proj`). The op's inputs (attn_norm, and
the MXFP4 weights wq/wk/wv from the same GGUF) are identical; only the
operation result differs. Everything downstream (conv1d, silu, l2-norm,
delta-net, gating, wo) merely propagates this seed — the delta-net even
attenuates it locally (kda_delta_out 7.4e-06) before later layers
re-amplify through additional MXFP4 matmuls.

### Large/structured enough to explain E4? Yes.

- Magnitude: projection max|d| ≈ 1e-2 with rel ≈ 1.4–2.2e-2 is
  **~2,700–3,500× above the f32 accumulation-order noise floor** for a
  length-2304 dot product (noise ≈ 2.9e-06 abs / 5.7e-06 rel). This is
  NOT expected backend rounding.
- Structure: the errors are scattered per-element (diff signs +2038 /
  −2032 / 0 26; corr(|val|,|diff|) ≈ −0.03 over 3,966 elements with
  |val|>1e-2; cpu/metal ratio median ≈ 1.0001 with heavy tails
  −15.6…+56.3 at small values). The pattern is consistent with
  per-element dequantization/accumulation differences in the MXFP4
  matmul (e.g. dequant arithmetic or accumulation order on Metal), NOT
  a uniform block-scale factor or a layout/stride misinterpretation
  (those would show a correlated, structured error).
- Amplification: a ~1e-2 relative seed at layer 0's projection, pushed
  through 26 layers of MXFP4 matmuls + the routing argmax (which turns
  small logit differences into different expert sets, as E1b showed
  from layer 11), is fully consistent with the E4 result
  (PPL 1,477,252 vs 6.7596, every chunk > ×10^5).

### Candidate causes (evidence-ranked, not assumed)

1. **MXFP4 dequantization/conversion in the Metal mul_mat path** — the
   first divergent op is an MXFP4-weight matmul with identical inputs;
   per-element scattered errors at 1e-2 scale are the signature of
   dequant arithmetic differences. STRONGEST candidate by evidence.
2. **Metal-specific matmul accumulation order/precision** for MXFP4 —
   same boundary, cannot be fully separated from (1) by this trace;
   a controlled substitution (pin the projection mul_mat to CPU on an
   otherwise-Metal run) discriminates these.
3. NOT supported by evidence here: layout/stride interpretation,
   block-scale handling as a uniform factor, sync/lifetime issues
   (traces are synchronized after each readback; no NaN/Inf, no
   corruption pattern).

## Problems

- The trace format stores only the token-0 column (n_embd floats per
  record), so the stats are token-0 only. Sufficient for this bounded
  first experiment (the first prefill step has n_tokens=2; token 0
  shows the divergence cleanly), but a full-tensor comparison would
  strengthen the structuredness claim. Deferred.
- The layer-0 projection divergence is visible at the FIRST prefill
  step, so the seed is not a warmup artifact.

## Decisions

- Freeze E2/E4 evidence; do not begin E3 (per directive).
- Diagnostic-only instrumentation: env-gated `llm_trace_act_capture`
  calls only; no kernel/math/default-path changes; committed
  separately from any future fix.
- First-divergence gate met: the projection `mul_mat` (MXFP4) at layer
  0 is the earliest boundary where CPU and Metal cease to agree with
  identical inputs, at a magnitude far above rounding noise.

## Next Phase (candidate, for review)

- **Controlled substitution/bisection** (per directive's suggestion):
  execute the layer-0 Q/K/V projection `mul_mat` on CPU while the
  surrounding path runs on Metal (or force the Metal mul_mat to use
  the CPU dequant path), and check whether downstream boundaries
  return to CPU-agreement. A substitution that restores downstream
  agreement is much stronger evidence than comparing final
  activations.
- If substitution confirms the MXFP4 matmul: inspect the Metal MXFP4
  dequant kernel/type conversion vs the CPU path for arithmetic
  differences (f32 vs f16 intermediates, block-scale application
  order). Do NOT modify kernels until the correction is clear.
- Re-run this localization and then the E4 gate once a fix candidate
  exists.

## Reproduction

    # 1. build the fork (diagnostic captures)
    cd llama.cpp && cmake --build build-metal -j10

    # 2. run the trace pair (CPU reference + frozen-E2 Metal path)
    tools/phase11_localize_run.sh
    # -> benchmarks/results/phase-k1/localize/{cpu,metal}/act.bin

    # 3. analyze (layer 0 first prefill step)
    python3 tools/phase11_localize_analyze.py \
        benchmarks/results/phase-k1/localize/cpu/act.bin \
        benchmarks/results/phase-k1/localize/metal/act.bin \
        --il 0 --ph 0 --sp 0

Artifacts retained: `benchmarks/results/phase-k1/localize/cpu/`,
`localize/metal/` (act.bin × 2, run.log). Fork: diagnostic-only commit
on top of `32c02b145` (frozen E2 baseline). Main repo: this report +
drivers.
