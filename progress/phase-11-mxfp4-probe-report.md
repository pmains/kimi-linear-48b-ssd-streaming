# Phase 11 — First MXFP4 Expert mul_mat Arithmetic Isolation

## Status

PASS — **first MXFP4 expert `mul_mat` arithmetic CLEARED (numerically
sound).** The first streamed MXFP4 expert mul_mat (layer-1 up
projection, `blk.1.ffn_up_exps.weight`, mul_mat_id, first prefill
step) produces correct Metal results from the intended expert weights
and activations. Production Metal is reproduced to **5.96e-8** (rel
1.6e-7) by the expected Metal arithmetic — the mm_id kernel's
documented fp16-operand/fp32-accumulator semantics (`T=half, S=half`
in the `kernel_mul_mm_id_mxfp4_f32` instantiation) — and production
CPU is reproduced to 7.8e-4 by the exact `ggml_vec_dot_mxfp4_q8_0`
replica (documented q8_0 activation quantization). The CPU-vs-Metal
difference at this boundary (max|d| = 3.6e-3, rel 6.7e-3) is fully
explained by expected conversion semantics on both sides; no defect.
Per the directive: expert arithmetic cleared → **E2 zerocopy /
slot-integrity check identified as the next discriminator**. Stopped
for review. No kernels modified, no E3.

## Objective

Determine whether the first streamed MXFP4 expert `mul_mat` produces
numerically correct Metal results from the intended expert weights and
activations, using the Q/K/V + kda_g1 methodology: identical retained
inputs/weights, production CPU and Metal outputs, and higher-precision
reference reconstructions. Establish (1) the exact first MXFP4 expert
mul_mat executed and its production tensor/layout/path; (2) whether the
expert bytes consumed correspond to the router-selected expert and
intended GGUF tensor; (3) where CPU and Metal first diverge within the
operation; (4) CPU vs reference and Metal vs reference separately;
(5) whether production Metal is reproduced by the reconstructed Metal
arithmetic; (6) whether any discrepancy is explained by expected
quantization/conversion/accumulation/representation semantics — with
particular attention to MXFP4 block-scale interpretation and
layout/addressing (given the earlier GGUF-offset mistake).

## The operation (exact production tensor/layout/path)

- **Layer 0 is a dense q8_0 FFN layer, NOT MoE** (verified from GGUF:
  `blk.0.ffn_up.weight` q8_0 [2304,9216] exists; no `blk.0.ffn_*_exps`
  tensors; no `moe_up`/`router_logits` captures at il=0). The first
  MXFP4 expert mul_mat is therefore at **layer 1**.
- Tensor: `blk.1.ffn_up_exps.weight` — mxfp4 (GGUF type 39), ne
  [2304 (K=n_embd), 1024 (n_ff), 256 (n_expert)], abs_off
  **1,608,189,216** (parsed in-probe, data base 6,949,024); per-expert
  bytes = 2304·1024/32·17 = **1,253,376**; per-expert block layout
  72 q8-blocks·17 B along ne0 (K).
- Path: streamed naive + E2 direct-place, `GGML_OP_MUL_MAT_ID` on
  Metal; 233-token prefill ⇒ `ne21 ≥ 32` ⇒ **mm_id kernel path**
  (`kernel_mul_mm_id_mxfp4_f32` via
  `ggml_metal_library_get_pipeline_mul_mm_id`), whose template
  instantiation is `mul_mm_id<half, half4x4, simdgroup_half8x8, half,
  half2x4, simdgroup_half8x8, block_mxfp4, 2, dequantize_mxfp4, float,
  float4x4, float, float2x4>` — i.e. **dequantized weights and
  activations are both converted to fp16; accumulation is fp32**.
- Captures (retained localize traces): `ffn_normed` (input, 2304×233),
  `router_logits` (256 experts), `moe_up` (n_ff=1024, token-0 slab,
  slot-0 column) at il=1, exec 2 (first prefill step, 233 tokens),
  pos=0, ph=0 — in both cpu and metal act.bin.
- Router: top-8 identical CPU vs Metal: [50, 43, 21, 4, 83, 105, 96,
  123]; slot-0 = **expert 50** (argmax of logits; argsort_top_k
  ordering).

## Expert bytes = router-selected expert at intended GGUF tensor (verified)

The expert slice was read at `UP_ABS_OFF + expert_id·PER_EXP_BYTES` =
1,608,189,216 + 50·1,253,376 = **1,670,858,016** — the SAME addressing
the streamed path uses (tensor offset + expert_id · per-expert bytes).
Evidence of correctness: (a) the dequantized slice is sane
(max|w| = 0.375, mean 3.2e-2 — no garbage/NaN; first block e8m0=121,
nibbles plausible); (b) **reconstructions from exactly these bytes
reproduce the production outputs** (Metal to 5.96e-8, CPU to 7.8e-4) —
wrong bytes/offset could not do that. The earlier GGUF-offset mistake
class is addressed by using the authoritative GGUF-parsed absolute
offset + the verified per-expert stride (nbytes 320,864,256/256 =
1,253,376, consistent with the tensor census).

## Reconstructions vs production (identical retained inputs)

Inputs: `ffn_normed` captured token-0 column; note it is NOT
bit-identical CPU vs Metal (max|d| = 1.1e-3, propagated from layer-0),
so each path is reconstructed from its OWN captured input; a
common-input cross-path isolates the op arithmetic.

### CPU path (input: cpu ffn_normed)

| comparison | max\|d\| | rel@max |
|---|---:|---:|
| prod_cpu vs f64ref | 3.169e-03 | 7.87e-02 |
| **prod_cpu vs cpu_q8 replica** | **7.751e-04** | 1.67e-02 |
| f64ref vs cpu_q8 replica | 3.110e-03 | 7.72e-02 |

The exact `ggml_vec_dot_mxfp4_q8_0` replica (q8_0-quantized
activations, int32 kvalues dot per block, scale = fp16(q8.d) ·
e8m0_half) reproduces production CPU to **7.8e-4** — the residual is
the q8_0 activation-quantization path itself, the documented CPU
semantics (same Case-A class as Q/K/V).

### Metal path (input: metal ffn_normed)

| comparison | max\|d\| | rel@max |
|---|---:|---:|
| prod_metal vs f64ref | 1.404e-04 | 2.39e-03 |
| prod_metal vs f32seq | 1.405e-04 | 2.40e-03 |
| prod_metal vs half_weights | 1.405e-04 | 2.40e-03 |
| **prod_metal vs half_w+a (fp16 ops, fp32 acc)** | **5.960e-08** | 1.65e-07 |
| f64ref vs half_w+a | 1.404e-04 | 2.39e-03 |

**Production Metal is reproduced to 5.96e-8 (fp32 accumulation noise)
by the half_w+a reconstruction** — dequantized weights AND activations
converted to fp16, fp32 accumulation — exactly the
`kernel_mul_mm_id_mxfp4_f32` semantics. The 1.4e-4 gap vs f64 is the
documented fp16 operand conversion (rel ~2.4e-3), an expected
mixed-precision conversion, NOT a defect.

### Where CPU and Metal first diverge (per-block, common cpu input)

Worst per-block |d| = 3.2e-4 at block 67 (K=2144), row 0
(cpu=-5.476e-3, metal=-5.792e-3) — same magnitude class as the whole
op; there is no discontinuity: divergence is uniformly the
quantization/conversion difference (CPU q8_0 act quant vs Metal fp16
operand conversion), distributed across blocks, not a single-op
corruption.

### Production CPU vs Metal at the boundary

max|d| = **3.596e-3**, mean 8.6e-4, rel@max 6.7e-3 — small relative
divergence, both sides explained by their documented semantics.

## Problems

- The probe's first run had two replica-side bugs (mxfp4 nibble layout
  misread as 2j/2j+1 instead of j/j+16; missing d_q8 scale in the CPU
  replica), producing incoherent numbers. Fixed (layout per
  `dequantize_row_mxfp4`: byte j low nibble = element j, high = j+16;
  CPU scale product per `ggml_vec_dot_mxfp4_q8_0`); the corrected run
  is the retained artifact.
- ffn_normed at il=1 is not bit-identical CPU vs Metal (1.1e-3,
  propagated from layer 0) — handled by per-path input reconstruction
  plus a common-input cross-path; not an op defect.

## Decisions

- **First MXFP4 expert mul_mat arithmetic CLEARED**: Metal produces
  the expected result from the intended expert bytes and activations,
  reproduced to 5.96e-8 by the mm_id kernel's documented fp16-operand
  arithmetic. The CPU-vs-Metal difference is explained by documented
  conversion semantics (CPU q8_0 activation quantization; Metal fp16
  operand conversion). No Metal defect at this boundary.
- The fp16 operand conversion on Metal (rel ~2.4e-3 vs f64) is a real
  precision note — but it is small (smaller than the CPU reference's
  own q8 error class) and CANNOT by itself explain the E4 ×218k PPL.
- **Next discriminator (identified, NOT run): the E2 zerocopy /
  slot-integrity check** — verify the packed expert bytes land in the
  correct slots/strides for all layers. A wrong-bytes-to-slot bug
  would produce garbage logits with no kernel being wrong, and is the
  remaining streamed-data-path suspect consistent with "op arithmetic
  is sound at the first expert" + "E4 garbage persists".
- No kernels modified; no E3; STOPPED for review per directive.

## Next Phase

1. E2 zerocopy slot-integrity check: verify, for all layers, that the
   pread slice → slot placement uses the correct expert_id and stride
   (compare placed slot bytes vs GGUF slice; check ids→slot mapping
   for every layer of a decode step and the first prefill step).
2. If the data path is also clean, the remaining E4 hypotheses are
   later-layer Metal op arithmetic (repeat this probe on a deeper
   layer's expert and on moe_down) and routing-argmax amplification
   (weakened by K1: 91.84% routing divergence between two CPU quants,
   +1.37% PPL).

## Reproduction

    python3 tools/phase11_mxfp4_probe.py \
        models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
        benchmarks/results/phase-k1/localize/cpu/act.bin \
        benchmarks/results/phase-k1/localize/metal/act.bin
    # -> benchmarks/results/phase-11/mxfp4-probe/run.txt
    # key line: prod_metal vs half_w+a max|d|=5.960e-08 (reproduction)
