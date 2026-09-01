# Phase 11 E4 Report — Perplexity Quality Gate: MXFP4 CPU vs MXFP4 Metal

## Status

EXECUTED — Metal path **NOT quality-qualified** (catastrophic degradation).
Per the E4 directive's decision rule, STOP before E3: investigate Metal
numerical quality/correctness. No kernels changed, no numerical repair
attempted (per directive). E2 remains frozen as the Metal baseline.

## Objective

Run the established perplexity quality gate against the streamed MXFP4
Metal path (frozen E2 baseline) before any E3 compute-side optimization.
Primary comparison: **MXFP4 CPU (reference) vs MXFP4 Metal (candidate)**,
using the accepted K1 methodology as closely as possible: same WikiText
corpus, 32 × 512-token chunks, same model artifact, same streamed path,
same 4096 MiB expert cache, same scoring/parsing, same binary. No tensor
identity / routing identity / generated-text identity criteria. Report
CPU PPL, Metal PPL, ΔPPL, per-chunk results, anomalies, and enough
information to distinguish systematic degradation from isolated outliers.

## Changes

- `tools/phase11_e4_perplexity.sh` — new E4 driver (ARM=cpu|metal; cpu:
  `-ngl 0`; metal: `-ngl 999` + `KIMI_STREAM_METAL_STAGE=1` +
  `KIMI_STREAM_E2_DIRECT_PLACE=1`; same corpus/chunks/ctx/batch/cache as
  K1; covariates before/after; full per-chunk extraction to
  `ppl-result.json`).
- `tools/phasek1_perplexity.sh` — fixed the latent per-chunk parser bug
  (shared with the new E4 driver): the extractor captured the chunk
  index (`m.group(1)`) instead of the PPL value and then crashed
  unpacking floats; the K1 result JSONs had been back-filled by hand
  after the same failure. Now `pairs = [(int(group1), float(group2))...]`
  sorted by chunk index. CPU reference re-verified against the accepted
  K1 number from a fresh run (below), so the historical number is not
  relied on alone.
- `progress/phase-11-e4-report.md` — this report.

No llama.cpp source changes: the frozen E2 fork commit `32c02b145` was
used unchanged (verified `git rev-parse HEAD` before the runs).

## Results

### Environment

Same MacBook Air (24 GB unified), same MXFP4 GGUF
(`moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf`), same
`llama-perplexity` binary (built 2026-08-31 17:57 from the frozen E2
fork), wikitext-2-raw-test, chunks=32, ctx=512, `-b 2048 -ub 512`,
zerocopy cache 4096 MiB, streamed naive path. Both arms ran
sequentially (each loads the 27 GB model; they must not compete for
memory). Covariates retained in each run dir.

### CPU reference (re-run, not just historical)

| metric | value |
|---|---|
| final PPL | **6.7596 ± 0.19264** |
| chunks | 32/32, all parsed |
| runtime | ~70 s/pass, clean EXIT=0 |

The fresh CPU run reproduces the accepted K1 CPU MXFP4 reference
(6.7596 ± 0.193) exactly — protocol continuity confirmed.

### Metal candidate (frozen E2 path)

| metric | value |
|---|---|
| final PPL | **1,477,252.66 ± 42,507.67** |
| chunks | 32/32, all parsed |
| runtime | 2.24 s/pass (Metal is ~31× faster than CPU on this workload), clean EXIT=0 |

### Comparison

| metric | value |
|---|---|
| absolute ΔPPL (Metal − CPU) | **+1,477,245.90** |
| relative ΔPPL | **×218,541 (+21,854,043%)** |
| per-chunk Metal/CPU ratio | min ×155,291 … max ×285,868 |
| chunks with ratio > ×10^5 | **32/32** |

Per-chunk table (all 32):

| chunk | CPU | Metal | ratio |
|---|---|---|---|
| 1 | 4.7629 | 871,841.49 | ×183,048 |
| 2 | 6.3715 | 1,140,711.88 | ×179,033 |
| 3 | 6.1192 | 1,469,655.83 | ×240,171 |
| 4 | 6.1727 | 1,764,574.83 | ×285,868 |
| 5 | 6.4346 | 1,448,366.34 | ×225,090 |
| 6 | 6.7221 | 1,356,084.47 | ×201,735 |
| 7 | 6.9535 | 1,381,139.81 | ×198,625 |
| 8 | 7.2777 | 1,373,325.76 | ×188,703 |
| 9 | 7.8125 | 1,329,202.86 | ×170,138 |
| 10 | 8.0980 | 1,299,515.53 | ×160,474 |
| 11 | 8.1761 | 1,359,471.22 | ×166,274 |
| 12 | 8.2430 | 1,400,073.17 | ×169,850 |
| 13 | 8.7248 | 1,354,884.36 | ×155,291 |
| 14 | 8.3760 | 1,331,822.46 | ×159,005 |
| 15 | 8.3093 | 1,373,536.92 | ×165,301 |
| 16 | 8.2131 | 1,401,737.17 | ×170,671 |
| 17 | 8.1813 | 1,379,232.83 | ×168,584 |
| 18 | 8.4904 | 1,406,382.92 | ×165,644 |
| 19 | 8.2172 | 1,421,519.27 | ×172,993 |
| 20 | 8.1492 | 1,456,533.89 | ×178,733 |
| 21 | 8.0988 | 1,436,089.15 | ×177,321 |
| 22 | 7.8658 | 1,440,243.41 | ×183,102 |
| 23 | 7.5269 | 1,485,668.38 | ×197,381 |
| 24 | 7.2890 | 1,486,040.43 | ×203,874 |
| 25 | 7.0802 | 1,450,941.47 | ×204,929 |
| 26 | 6.9415 | 1,437,568.85 | ×207,098 |
| 27 | 6.7869 | 1,459,816.65 | ×215,093 |
| 28 | 6.7137 | 1,462,656.34 | ×217,861 |
| 29 | 6.7134 | 1,445,971.17 | ×215,386 |
| 30 | 6.7183 | 1,444,719.72 | ×215,042 |
| 31 | 6.7645 | 1,472,351.84 | ×217,659 |
| 32 | 6.7596 | 1,477,252.66 | ×218,541 |

### Systematic vs isolated (per directive)

**Systematic degradation, not outliers.** Every one of the 32 chunks is
degraded by 5+ orders of magnitude (min ×155,291); the Metal PPL is
large from chunk 1 onward and grows slightly with chunk index. There
are no clean chunks, no NaN/Inf, no runtime failures, no asserts, no
anomalous-single-chunk pattern. This is the quantitative confirmation of
the E1b localization: small per-op numeric drift from the first layers,
amplified through 26 MoE layers and the routing argmax, producing
garbage logits (routing set divergence from prefill layer 11;
activation max|d| grows monotonically to O(1) by layer 18+).

## Problems

- Metal streamed MXFP4 perplexity is catastrophically degraded vs the
  CPU reference (×218,541 relative; every chunk > ×10^5). Root cause
  per E1b evidence is cumulative numeric divergence in the Metal path
  (attention/router/expert matmul accumulation order and/or MXFP4
  dequant on Metal), amplified by 26 MoE layers + routing argmax — NOT
  fixed here, per directive ("do not change kernels or attempt to
  repair numerical divergence during this experiment").
- The Metal path therefore cannot be quality-qualified for the tested
  workload. The E1b observation that "Metal output text is garbled" is
  now quantified as a PPL of ~1.5M.
- Tooling note: the per-chunk parser bug in `phasek1_perplexity.sh`
  (chunk-index vs value; crash on unpack) was latent — the accepted K1
  numbers were correct because they were back-filled from logs. Fixed
  in both scripts; the fresh CPU re-run confirms 6.7596, so no K1
  conclusion changes.

## Decisions

- Per the E4 directive decision rule: Metal PPL materially degraded →
  **stop before E3** and investigate Metal numerical quality/correctness.
- E2 stays frozen (`32c02b145`) as the Metal baseline; no source
  changes in this experiment.
- No tensor/routing/text identity used as the quality criterion; PPL is
  the gate.
- The E4 driver is retained as the exact executable configuration
  (`tools/phase11_e4_perplexity.sh`, cpu|metal arms).

## Next Phase

- **Investigate Metal numerical quality/correctness before any E3
  compute work** (directive: do not begin E3 automatically after E4).
  Candidates grounded in existing evidence: per-op accumulation-order
  comparison for attention/router/expert matmuls on Metal vs CPU;
  MXFP4 dequant numerics on Metal; a targeted per-layer logit
  comparison to find the first op where divergence becomes
  irreversible, using the restored act/moe traces
  (`benchmarks/results/phase-k1/e1b-metal/`, `e1b-cpu-trace/`).
- If/when the Metal path is numerically qualified, re-run this E4 gate
  before any E3 performance claim.
- E3 (compute-side optimization) is NOT started.

## Reproduction

CPU reference (fresh re-run, ~10 min):

    tools/phase11_e4_perplexity.sh cpu benchmarks/results/phase-k1/e4-cpu 32 512
    # final PPL = 6.7596 +/- 0.19264 (matches accepted K1 reference)

Metal candidate (frozen E2 path, ~1 min compute):

    tools/phase11_e4_perplexity.sh metal benchmarks/results/phase-k1/e4-metal 32 512
    # final PPL = 1477252.6561 +/- 42507.66605

Artifacts (retained): `benchmarks/results/phase-k1/e4-cpu/` (run.log,
ppl.log, ppl-result.json, covariates), `benchmarks/results/phase-k1/e4-metal/`
(same). Both arms: wikitext-2-raw-test, chunks=32, ctx=512, `-b 2048
-ub 512`, zerocopy cache 4096 MiB, streamed naive path, same binary.
Fork commit: `32c02b145` (frozen E2 baseline, unchanged).
