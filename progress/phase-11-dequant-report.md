# Phase 11 Dequantization Isolation — CPU vs Metal, Identical Raw Blocks

## Status

PASS (question answered). **The defect lies AFTER dequantization.**

Given identical encoded weights and scales, CPU and Metal reconstruct
the same dequantized values for both quantized types present in this
model — bit-identical for all non-NaN q8_0 values, and for mxfp4 except
a subnormal-range flush-to-zero on Metal (max|d| ≈ 3.5e-38, negligible
and not the error class under investigation). Per the directive's
decision tree, MXFP4 (and q8_0) representation decoding is
provisionally cleared; the next experiment isolates the `mul_mat`
arithmetic. E2/E4 evidence and all prior traces preserved. E3 not
begun. Stopped for review.

## Premise correction (verified, must be recorded)

The directive premised this experiment on "the layer-0 Q/K/V MXFP4
mul_mat". The GGUF tensor census (authoritative, from the model file
itself) shows the layer-0 tensors implicated in the first divergence
are **q8_0, not MXFP4**:

    blk.0.attn_q.weight / attn_k.weight / attn_v.weight   q8_0
    blk.0.attn_output.weight                              q8_0
    blk.0.ssm_f_a.weight / ssm_f_b.weight                 q8_0
    blk.0.ssm_g_a.weight / ssm_g_b.weight                 q8_0
    blk.0.ssm_beta.weight                                 q8_0
    blk.0.ssm_conv1d_q/k/v.weight                         f32
    blk.0.ssm_a / ssm_norm.weight                         f32

MXFP4 in this model is the **MoE expert tensors** (`blk.N.ffn_*_exps`,
92 tensors) and some MLA K/V bias tensors (`blk.N.attn_k_b/v_b`).
Census across all `blk.*`: q8_0 ×282, f32 ×233, mxfp4 ×92.

Consequence: the localized "first causally meaningful divergence"
(layer-0 Q/K/V projection `mul_mat`, E1b + localization + substitution
experiments) is a **q8_0 matmul**, not MXFP4. The substitution
experiment's conclusion is therefore corrected from "general to the
Metal MXFP4 mul_mat path" to "general to the Metal **quantized**
mul_mat path (q8_0 dense trunk first; MXFP4 experts are the same
class)". The dequant isolation below therefore tests **both** types:
q8_0 on the ACTUAL localized tensor (primary), mxfp4 on an expert
tensor (per the directive's letter, and because the E4 catastrophe
involves the full model including MXFP4 experts).

## Objective

Per directive: determine whether CPU and Metal reconstruct the same
numerical weight values from identical quantized blocks/scales, using a
small deterministic subset of the already-localized layer-0 projection
tensor (q8_0) and an MXFP4 expert tensor. Capture/compare: identical
raw packed bytes; block-scale data; CPU-dequantized values;
Metal-dequantized values (real Metal shader execution, not a
replication). Decide: if dequantized values disagree materially →
localize within decoding; if they agree → clear representation decoding
and proceed to `mul_mat` arithmetic isolation.

## Changes (diagnostic-only)

- `tools/phase11_dequant_probe.m` — standalone probe (no llama.cpp
  changes; fork untouched at `ce3df5117`):
  - preads a deterministic subset (first N blocks, default 8) of raw
    bytes from the GGUF at a tensor offset;
  - CPU dequant: exact replication of `dequantize_row_q8_0` /
    `dequantize_row_mxfp4` (function bodies copied verbatim from
    ggml-quants.c; fp16→fp32 and e8m0 half/full conversion from
    ggml-impl.h; `kvalues_fp4` from ggml-common.h);
  - Metal dequant: a REAL Metal compute kernel compiled at runtime from
    MSL source copied verbatim from ggml-metal.metal
    (`dequantize_q8_0`, `dequantize_mxfp4`, `e8m0_to_fp32`,
    `kvalues_mxfp4_f`), executed on the same bytes on the GPU;
  - compares bit-for-bit, separates numeric mismatches from
    NaN-payload-only differences, reports first mismatch + magnitudes.
  - Build: `xcrun -sdk macosx clang -fobjc-arc -framework Foundation
    -framework Metal tools/phase11_dequant_probe.m -o <out>`

No kernel, math, or default-path changes. Rollback: n/a (standalone
tool).

## Results

### q8_0 — blk.0.attn_q.weight (offset 822233088; the ACTUAL localized first-divergence op)

16 blocks (512 elements), first 16 blocks of the tensor:

    elements compared: 512
    raw bit mismatches: 64  (numeric: 0, NaN-payload-only: 64)
    => representation decoding AGREES for all non-NaN values;
       only NaN payload encoding differs.

The 64 mismatches are two blocks (7, 15) whose scale half
(`d = 0xff44`) is a NaN (exp=0x1F, mant≠0). CPU produces NaN payload
`0xffe88000`, Metal produces canonical `0x7fc00000` — both NaN, both
propagate identically through arithmetic. Not a numerical divergence.

First block (scale d=0x0c14 → 0.000248909), first 8 values CPU vs
Metal: identical to all printed digits (-0.0161791, 0.00448036,
-0.00746727, 0.0102053, …).

### mxfp4 — blk.1.ffn_up_exps.weight (offset 1601240192; first expert tensor)

16 blocks (512 elements):

    elements compared: 512
    raw bit mismatches: 30  (numeric: 30, NaN-payload-only: 0)
    first at element 448 (block 14): cpu=1.175494351e-38 metal=0.0
    max|d| = 3.53e-38   mean|d| = 7.86e-40

All 30 mismatches are in block 14, whose scale byte `e = 0x00`
decodes to d = 2^-128 (CPU, subnormal) / 2^-127 (Metal, subnormal).
CPU preserves subnormal results (values ≤ 3.5e-38); the Metal shader
flushes the subnormal scale to zero (MSL default FTZ), yielding 0.0.
Magnitude ≤ ~3.5e-38 — twelve orders below the ~1e-2 relative errors
under investigation; not the error class, and cannot explain the
projection divergence. It is a genuine but negligible representational
difference (documented; could matter only for activation values of
order 1e-38, which do not occur in this model's weights).

### Interpretation (the directive's primary question)

**Given identical encoded weights and scales, does Metal produce the
same dequantized values as CPU within the expected representation
precision? YES** — for q8_0 (the actual localized type) bit-identical
on all non-NaN values; for mxfp4 bit-identical except subnormal FTZ at
~1e-38. The dequantized values are NOT materially different.

Per the directive's decision tree: **representation decoding is
provisionally cleared** (for both q8_0 and mxfp4); the ~1e-2 relative
divergence observed at the layer-0 projection `mul_mat` output is
introduced AFTER dequantization, in the **mul_mat arithmetic**
(accumulation precision/order, conversion, kernel specialization, or
another arithmetic behavior). Do not assume accumulation order alone —
the next experiment must compare partial products/accumulations with
identical dequantized inputs.

## Problems

- The directive's premise (MXFP4 in layer-0 projections) was factually
  wrong; corrected above. The model is mixed-quant: dense trunk q8_0,
  experts mxfp4. The localized first-divergence op is q8_0.
- mxfp4 subnormal flush-to-zero on Metal is a real CPU/Metal dequant
  difference but at ~1e-38 absolute it is not the error class; noted
  for completeness, not treated as the defect.

## Decisions

- Clear representation decoding (q8_0 and mxfp4) per the directive's
  decision tree.
- Correct the record: the defect is in Metal **quantized** `mul_mat`
  arithmetic, first observed on q8_0 (dense trunk), same class expected
  on mxfp4 experts.
- Next experiment: isolate `mul_mat` arithmetic — feed identical
  dequantized weights and identical activations to CPU and Metal,
  compare partial products/accumulations at bounded intermediate
  points; discriminate accumulation precision vs ordering vs kernel
  specialization. Do not assume accumulation order alone explains
  ~1e-2 errors.
- Do not modify the production Metal kernel yet (directive).
- E3 not begun; E2/E4 evidence frozen.

## Next Phase (candidate, for review)

`mul_mat` arithmetic isolation (per directive). Concretely: capture the
dequantized layer-0 Q-projection weights (CPU path, deterministic) and
a fixed activation vector, run the matmul on CPU and on Metal with
identical inputs, and compare partial sums at a small set of positions
(e.g., first 4 output rows, k-split at 64/256/1024/2304) to see where
the ~1e-2 error first appears — accumulation precision vs order vs
conversion.

## Reproduction

    # build the probe (diagnostic-only; no llama.cpp changes)
    xcrun -sdk macosx clang -fobjc-arc -framework Foundation -framework Metal \
        tools/phase11_dequant_probe.m -o /tmp/phase11_dequant_probe

    # q8_0: the ACTUAL localized first-divergence tensor (layer-0 Q projection)
    /tmp/phase11_dequant_probe \
        models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
        822233088 q8_0 16
    # -> numeric mismatches: 0 (64 NaN-payload-only in 2 NaN-scale blocks)

    # mxfp4: first expert tensor (per directive)
    /tmp/phase11_dequant_probe \
        models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
        1601240192 mxfp4 16
    # -> 30 numeric mismatches, ALL subnormal FTZ (max|d| 3.5e-38, block 14)

Tensor offsets verified from the GGUF header (llama-gguf r n). Probe
source cites the exact ggml source lines it replicates. Fork commit
`ce3df5117` (unchanged). Main repo: this report + probe.
