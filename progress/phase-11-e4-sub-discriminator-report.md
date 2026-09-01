# Phase 11 E4 Cause Discriminator — Layer-0 Q/K/V CPU Substitution on the Frozen Metal Path

## Status

PASS (discriminator interpretable; negative result). Pinning the layer-0
Q/K/V projection to the CPU backend on the otherwise frozen-E2 Metal
path leaves the catastrophic Metal perplexity failure essentially
unchanged: metal-sub final PPL **1,377,693** vs frozen Metal
**1,374,490** on the same 8 chunks (+0.23%, i.e. a 0.15–0.23% per-chunk
perturbation, not a recovery). **Layer-0 Q/K/V arithmetic divergence is
therefore EXCLUDED as the primary E4 cause.** Per the directive: no
kernel changes, no E3, no optimization; stopped for review after
identifying the next useful localization boundary.

## Objective

Decide between the two live explanations of the E4 catastrophe
(Metal PPL ×218,541 vs CPU): (a) the layer-0 Q/K/V projection
divergence (the first causally-localized CPU-vs-Metal difference,
~1e-2) seeds a cascade that destroys logits, or (b) the primary defect
is elsewhere in the Metal path. Use the existing env-gated layer-0
Q/K/V CPU substitution mechanism (`KIMI_STREAM_LOCALIZE_SUB_QKV_CPU`,
frozen fork commit `ce3df5117`, default off, no kernel changes) on an
otherwise frozen-E2 Metal run, with a small representative perplexity
probe (8 × 512-token chunks, same corpus/ctx/cache/binary as E4)
against the normal CPU and frozen-Metal references. Large quality
recovery ⇒ (a) confirmed, stop and report. Still catastrophic ⇒ (b),
exclude layer-0 Q/K/V and identify the next localization boundary.

## Changes

- `tools/phase11_e4_perplexity.sh` — added ARM=metal-sub (append-only):
  the frozen `metal` arm envs (`-ngl 999`,
  `KIMI_STREAM_METAL_STAGE=1`, `KIMI_STREAM_E2_DIRECT_PLACE=1`) plus
  `KIMI_STREAM_LOCALIZE_SUB_QKV_CPU=1`. The `cpu` and `metal` arms are
  byte-unchanged (the existing case branches and env-recording dict were
  touched only to unset/record the new env var). No llama.cpp source
  changes: the fork is exactly the substitution commit `ce3df5117`
  (frozen E2 `32c02b145` + localize `66fb8c053` + substitute), binary
  `build-metal/bin/llama-perplexity` built 2026-08-31 19:31 from that
  commit (verified `git rev-parse HEAD` and env-gate presence in
  `src/models/kimi-linear.cpp` / `src/llama-expert-stream-exec.cpp`).
- `benchmarks/results/phase-11/e4-sub-discriminator/` (new, retained):
  `driver.log` plus per-arm `{cpu,metal,metal-sub}/` with `ppl.log`,
  `ppl-result.json`, `covariates-{before,after}.txt`. All three arms
  ran sequentially on the same binary (each loads the 27 GB GGUF).

## Results

Environment: same MacBook Air (24 GB), same MXFP4 GGUF, wikitext-2-raw-
test, 8 chunks × 512 tokens, ctx=512, `-b 2048 -ub 512`, zerocopy cache
4096 MiB, streamed naive path, same binary for all arms. Wall times:
cpu 16:36:15→16:38:41 (146 s), metal 16:38:41→16:38:55 (14 s),
metal-sub 16:38:55→16:39:08 (13 s). All arms EXIT=0, 8/8 chunks parsed,
no NaN/Inf/asserts.

| arm | final PPL | vs CPU | vs metal |
|---|---:|---:|---:|
| cpu (reference) | **7.2777** | — | — |
| metal (frozen E2) | **1,374,490.14** | ×188,861 | — |
| metal-sub (layer-0 Q/K/V → CPU) | **1,377,692.56** | ×189,303 | ×1.0023 (+0.23%) |

Per-chunk (cpu / metal / metal-sub, sub:metal ratio):

| chunk | cpu | metal | metal-sub | sub/metal |
|---|---:|---:|---:|---:|
| 1 | 4.76 | 873,434.92 | 874,724.66 | 1.0015 |
| 2 | 6.37 | 1,141,934.77 | 1,143,975.67 | 1.0018 |
| 3 | 6.12 | 1,471,131.76 | 1,474,570.51 | 1.0023 |
| 4 | 6.17 | 1,766,346.81 | 1,768,921.27 | 1.0015 |
| 5 | 6.43 | 1,449,365.74 | 1,450,609.55 | 1.0009 |
| 6 | 6.72 | 1,357,543.67 | 1,359,090.77 | 1.0011 |
| 7 | 6.95 | 1,382,616.19 | 1,385,753.48 | 1.0023 |
| 8 | 7.28 | 1,374,490.14 | 1,377,692.56 | 1.0023 |

The 8-chunk CPU reference reproduces the E4 32-chunk reference
bit-for-bit on the shared chunks (chunks 1–8: 4.7629 / 6.3715 / 6.1192
/ 6.1727 / 6.4346 / 6.7221 / 6.9535 / 7.2777 in both runs), confirming
protocol continuity on the current binary.

### Substitution engagement (mechanical evidence)

The substitution is not a silent no-op in this run. The teardown
diagnostics show the CPU compute buffer grew from 9.00 MiB (metal) to
**22.06 MiB** (metal-sub) while the MTL0 buffer is unchanged
(375.772 MiB both) — consistent with the layer-0 Q/K/V mul_mat
(2304×4096 q8_0, ~7 MB output space) being executed on the CPU backend
and copied back to Metal. The per-chunk PPL perturbation (0.09–0.23%)
is likewise a real, if minuscule, trajectory change from
CPU-computed layer-0 projection values.

## Interpretation

- **Large quality recovery: NO.** metal-sub PPL remains ~1.38M, five
  orders of magnitude above the CPU reference, within +0.23% of the
  frozen Metal failure on the same chunks. Every chunk stays
  ×179k–×287k vs CPU.
- **Layer-0 Q/K/V arithmetic divergence excluded as the primary E4
  cause.** Substituting the exact op whose divergence was localized
  first (and shown by the substitute experiment to collapse its own
  downstream branch) does not move the catastrophic failure. This is
  consistent with the substitute experiment's propagation finding
  (layer-1 `l_in` essentially unchanged because every other layer-0
  matmul — g1/beta/gate/wo/FFN — re-injects divergence) and with the
  arith probe (the layer-0 divergence itself is the CPU path's q8_0
  activation quantization, i.e. a reference-side artifact, not a Metal
  corruption). The E4 catastrophe therefore originates downstream of
  layer-0 Q/K/V.

## Next localization boundary (identified, NOT run)

Per the directive, proceed only far enough to identify the next useful
boundary:

1. **Layer-0 `kda_g1` (delta-net f_a/f_b matmuls, q8_0) — primary
   candidate.** The substitute experiment's layer-0 table shows
   `kda_g1` at max|d| = **1.438e+00** (A vs B, first prefill step) —
   two orders of magnitude above the Q/K/V projection's ~1e-2, the
   largest single-boundary divergence measured anywhere, and
   deliberately NOT downstream of the Q/K/V branch (unchanged by
   substitution). A gating output off by ~1.4 absolute would corrupt
   the delta-net state update and plausibly produce garbage logits.
2. **First MXFP4 expert `mul_mat`** (MoE experts are the model's only
   mxfp4 tensors per the dequant census) — the routing/expert path
   feeds argmax decisions every layer.
3. **Streamed data-path integrity** (E2 zerocopy slot placement:
   verify the packed expert bytes landed in the correct slots with
   correct strides for all layers) — a wrong-bytes-to-slot bug would
   produce garbage without any kernel being wrong.

Boundary 1 is the cheapest high-value next experiment: apply the same
per-boundary CPU-vs-Metal trace comparison (E1b/localize tooling) to
`kda_g1`'s inputs/outputs, then, if confirmed, a bounded substitution
probe for `kda_g1` analogous to this one.

## Problems

- Negative result narrows but does not locate the defect; the
  catastrophe source remains open (downstream of layer-0 Q/K/V).
- Teardown warnings (`MTL0 compute buffer size … does not match
  expectation`) appear in all arms including the frozen reference;
  benign teardown-time noise, EXIT=0, unchanged from E4-era runs.

## Decisions

- Layer-0 Q/K/V CPU substitution does not recover Metal quality →
  layer-0 Q/K/V arithmetic divergence excluded as the primary E4
  cause.
- Metal path remains NOT quality-qualified; E2 stays frozen; no
  kernels modified; E3 not begun.
- Next localization boundary: layer-0 `kda_g1` (delta-net gating),
  then first mxfp4 expert mul_mat, then streamed data-path integrity
  (in that order).
- STOPPED for review per directive.

## Reproduction

    # driver: cpu|metal|metal-sub, same binary, 8 chunks
    tools/phase11_e4_perplexity.sh cpu       benchmarks/results/phase-11/e4-sub-discriminator/cpu 8 512
    tools/phase11_e4_perplexity.sh metal     benchmarks/results/phase-11/e4-sub-discriminator/metal 8 512
    tools/phase11_e4_perplexity.sh metal-sub benchmarks/results/phase-11/e4-sub-discriminator/metal-sub 8 512

Fork commit `ce3df5117` (unchanged); binary `llama.cpp/build-metal/bin/
llama-perplexity` built 2026-08-31 19:31. Full artifacts retained under
`benchmarks/results/phase-11/e4-sub-discriminator/` (driver.log +
per-arm ppl.log / ppl-result.json / covariates).
