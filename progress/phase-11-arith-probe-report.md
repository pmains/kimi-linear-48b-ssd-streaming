# Phase 11 — mul_mat Arithmetic Isolation (weight-mapping verification) — COMPLETED

## Status

PASS — discrepancy **classified: Case A**. The layer-0 Q-projection
CPU-vs-Metal divergence is local quantized dot-product/conversion
arithmetic: the CPU vec_dot path quantizes activations to q8_0 (an
inherent per-element conversion), while the Metal path multiplies f32
activations by dequantized q8_0 weights. Metal is the near-exact path
(≈1e-8 abs vs f64); the frozen CPU reference carries the ~1e-2 error.
Stopped for review per directive (no E3, no kernel changes, no
optimization).

## Objective

Complete the corrected layer-0 Q8_0 `mul_mat` arithmetic probe now that
GGUF tensor addressing is resolved: verify the corrected absolute
address, rerun the retained probe against the correctly addressed q8_0
weights and the bit-identical captured layer-0 activation, compare CPU
vs production-Metal at individual block contributions and cumulative
K=64/256/1024/2304, report error metrics, classify the evidence
(A/B/C), and re-run the minimum dequantization check at the corrected
address (the earlier dequant run used the wrong absolute offset, so its
attn_q attribution is invalid).

## Changes

- `tools/phase11_arith_probe.py` (extended): added `cpu_q8_blocks()` —
  per-32-elem-block contributions of the CPU vec_dot path (q8_0
  activation quantization + f32 within-block accumulation) — and a
  per-row CPU-path-vs-Metal-path comparison section: per-block stats
  (max|d|, mean|d|, rel@max, RMS, NaN/Inf) and cumulative sums at
  K=64/256/1024/2304. All prior output unchanged.
- `benchmarks/results/phase-11/arith-probe/` (new, retained):
  - `run-rows0-15-retained.txt` — the retained probe's original output
    (reproduced bit-identically).
  - `run-rows0-15-cpu-vs-metal.txt` — extended run with the
    block/cumulative CPU-vs-Metal comparison (this is the
    classification evidence).
  - `dequant-corrected-offset-q8_0.txt` — dequant probe re-run at the
    CORRECTED address 829,182,112 (bit-identical, 0 mismatches).
  - `captures/` — the four `l0_*` npy captures (attn_norm in,
    kda_Q_proj out, CPU and Metal) copied from /tmp so the probe
    inputs/outputs are retained in-repo.

## Results

### Address verification (retained tool, not rediscovered)

`tools/phase11_repack_dump` (retained from the mapping work) reports
for `blk.0.attn_q.weight`: `type=8 (q8_0) ne=[2304,4096,1,1]
abs_off=829182112 nbytes=10027008`. Decomposition: padded data-section
start **6,949,024** + GGUF-relative tensor offset **822,233,088** =
**829,182,112** (the earlier probe default used 822,233,088 as an
ABSOLUTE offset — 6.9 MB early; the earlier dequant probe's attn_q
attribution used the same wrong value). Cross-check: repack dump first
block scale `x4[0].d[0]=0x1024`; dequant probe at the corrected offset
reports the same first-block scale `d(half)=0x1024 → 0.000505447` and
`bit-identical: YES, raw bit mismatches: 0 (numeric: 0,
NaN-payload-only: 0)`. The probe's NaN-scale self-check passed for all
16 rows (no warnings).

### Dequantization recheck (minimum, corrected address)

Per directive, the earlier dequant conclusion used incorrect GGUF
addressing; rerun at 829,182,112 (`q8_0`, 16 blocks):

    elements compared: 512
    bit-identical: YES
    raw bit mismatches: 0  (numeric: 0, NaN-payload-only: 0)

**CPU and Metal reconstruct the same q8_0 weights at the correct
address** — bit-identical, no exceptions (the earlier run's 64
NaN-payload-only "mismatches" were an artifact of reading 6.9 MB early
into a different tensor's data). The minimum dequantization check now
passes at the correct address; representation decoding of the actual
localized tensor is confirmed identical.

### Arithmetic isolation (rows 0–15, correctly addressed weights + bit-identical activation)

Activations asserted bit-identical CPU vs Metal (probe assertion
passes). Per row: |cpu−metal| = 3.2e-4 … 7.96e-3 (rel 9.9e-4 …
1.03e-1, row-dependent); f64ref vs metal_actual ≤ 1.1e-7 (rel ≤
1.8e-6); f64ref vs cpu_actual = 3.2e-4 … 7.96e-3 (rel 2.2e-2 … 1.15e-1
on the diverging rows).

**Block level (72 q8_0 blocks of 32 elements per row):**

- Metal replica blocks vs f64 blocks: max|d| ≤ 9.7e-9, mean ≤ 1.7e-9,
  rel@max ≤ 2.1e-6 — Metal's per-block arithmetic is exact to f32
  accumulation precision.
- CPU path blocks vs Metal path blocks: **max|d| = 6.0e-3 (row 12),
  mean 1.9e-4 across rows, rel@max up to 1.65** — individual block
  contributions MATERIALLY differ. This is the q8_0 activation
  quantization in the CPU vec_dot path (each 32-element activation
  block is quantized to q8_0 before the dot).

**Cumulative (K=64/256/1024/2304):**

- f64 vs Metal replica: |d| stays ≤ 3.5e-8 at every cut — Metal
  accumulates exactly.
- CPU path vs Metal path: |d| grows with K as expected for accumulating
  per-block quantization errors — e.g. row 12: K=64 |d|=4.2e-4 →
  K=256 4.5e-4 → K=1024 5.8e-3 → K=2304 7.8e-3. No sudden jump at any
  cut that would indicate an accumulation-order or reduction bug
  independent of the block-level difference.

Cross-row aggregates (16 rows): block max|d| mean 2.05e-3, block mean|d|
mean 1.91e-4, cumulative K=2304 |d| mean 2.99e-3 (max 7.77e-3).
NaN/Inf counts: 0 in every comparison (all rows, all cuts).

### Classification

- **A — individual block contributions materially differ (local
  quantized dot-product/conversion arithmetic): YES.** CPU path block
  contributions differ from Metal's by up to 6e-3 (mean ~2e-4), six
  orders above the Metal-vs-f64 block noise (~1e-9). The conversion is
  the q8_0 activation quantization in the CPU vec_dot path
  (`vec_dot_type = Q8_0`), which Metal does not perform (f32
  activations).
- **B — blocks agree but cumulative diverges (accumulation/precision):
  NO.** Blocks already differ; cumulative divergence is the monotone
  accumulation of block-level differences with no discontinuity at any
  K cut.
- **C — replica agrees with CPU but production Metal differs
  (diagnostic doesn't reproduce production kernel): NO — closed.** The
  host replica of `kernel_mul_mv_ext_q8_0_f32` reproduces the
  production Metal output bit-for-bit (d=0 on rows 0/11/14, ≤1.2e-7
  elsewhere): the diagnostic DOES reproduce the production Metal kernel
  path exactly.

**Verdict: Case A.** The CPU-vs-Metal divergence at the layer-0 Q
projection is the arithmetic difference between the two paths — CPU
quantizes activations to q8_0 (inherent ~1/127-per-element error),
Metal multiplies f32 activations by dequantized q8_0 weights — NOT a
Metal defect, NOT a representation-decoding error, NOT a single-op
corruption. At this boundary **Metal is the accurate path and the
frozen CPU reference carries the quantization error** (magnitude
matches the localization's max|d| ≈ 9e-3 at kda_Q/K/V_proj).

Note: rows 5 and 10 are exactly 0.0 on both paths in the captured
output and in every reconstruction — those output rows' weights are
zero in the file (verified by the probe's exact-zero f64/f32
reconstructions), so they contribute nothing and are consistent.

## Problems

1. The earlier dequant probe's attn_q attribution used offset
   822,233,088 as an absolute file offset (wrong); its 64
   NaN-payload-only "mismatches" were garbage-read artifacts. Rerun at
   829,182,112 is bit-identical with 0 mismatches — conclusion
   re-established at the correct address.
2. The earlier probe run output was only summarized in the report, not
   retained; the run is now retained verbatim under
   `benchmarks/results/phase-11/arith-probe/` (both the original and
   extended forms).
3. Captures lived only in /tmp; copied into
   `benchmarks/results/phase-11/arith-probe/captures/` for retention.

## Decisions

- Classification is **A**; E4's garbage-perplexity explanation remains
  OPEN (cascade/routing-argmax sensitivity vs a later-layer or
  elsewhere defect) — this probe does not resolve it.
- Metal is the correctness anchor at this boundary; the frozen CPU
  reference's q8-activation quantization (~0.1–10% per-dot) is a
  systematic CPU-vs-Metal arithmetic difference to account for in any
  future quality gate design.
- No E3, no kernel changes, no optimization (directive). Stopped for
  review.

## Next Phase

Review decision points:

1. E4 garbage PPL: Metal's layer-0 projection is near-exact, so the
   ×218k PPL must come from the cascade (routing argmax sensitivity —
   K1 measured 91.84% routing divergence between two CPU quants) or a
   real Metal defect in a later layer/op. A bounded next experiment
   would pin the layer-0 Q/K/V projection to CPU on the Metal run
   (already tooled via `KIMI_STREAM_LOCALIZE_SUB_QKV_CPU`) and check
   whether E4 PPL recovers — if it does not, the defect is elsewhere.
2. Decide whether the frozen CPU reference or near-exact Metal is the
   correctness anchor for future quality gates.
3. If authorized, probe later layers (e.g., first mxfp4 expert mul_mat)
   with the same per-block/cumulative method.

## Reproduction

Mapping (offset + repack), via the retained tool:

    c++ -std=c++17 -I llama.cpp/include -I llama.cpp/ggml/include \
        tools/phase11_repack_dump.cpp \
        -L llama.cpp/build-metal/bin -Wl,-rpath,llama.cpp/build-metal/bin \
        -lllama -lggml -lggml-base -lggml-cpu -o /tmp/phase11_repack_dump
    DYLD_LIBRARY_PATH=llama.cpp/build-metal/bin /tmp/phase11_repack_dump \
        models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
        blk.0.attn_q.weight /tmp/attn_q_repacked.bin
    # -> abs_off=829182112, nbytes=10027008; first x4 scale 0x1024

Arithmetic isolation probe (retained; NaN self-check; extended with
CPU-path block/cumulative comparison):

    python3 tools/phase11_arith_probe.py \
        models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
        829182112 /tmp/l0_cpu_attn_norm.npy /tmp/l0_metal_attn_norm.npy \
        0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15
    # full output retained: benchmarks/results/phase-11/arith-probe/run-rows0-15-cpu-vs-metal.txt
    # (captures retained in benchmarks/results/phase-11/arith-probe/captures/)

Dequantization recheck at the corrected address:

    xcrun -sdk macosx clang -fobjc-arc -framework Foundation -framework Metal \
        tools/phase11_dequant_probe.m -o /tmp/phase11_dequant_probe
    /tmp/phase11_dequant_probe \
        models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
        829182112 q8_0 16
    # -> bit-identical: YES, raw bit mismatches: 0 (numeric: 0, NaN-payload-only: 0)
