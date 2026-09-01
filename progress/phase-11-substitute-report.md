# Phase 11 Controlled CPU Substitution — Layer-0 Q/K/V Projection (A/B/C)

## Status

PASS (causality confirmed; substitution mechanism verified). Replacing
ONLY the layer-0 Q/K/V projection `mul_mat` with its CPU result on the
frozen Metal path collapses that op's own downstream branch to CPU
agreement (max|d| ~1e-2 → ~5e-7), while the other MXFP4 matmuls in the
same layer (g1 f_a/f_b, ssm_beta, gate g_a/g_b) continue to diverge at
their original magnitudes. Conclusion per the directive's second
interpretation: the layer-0 projection is a causally confirmed source,
and the defect affects **MXFP4 `mul_mat` generally**, not this
particular projection. Underlying mechanism (dequant vs accumulation)
is NOT concluded — that is the next experiment. E2/E4 evidence and
localization traces preserved. E3 not begun. Stopped for review.

## Objective

Test whether the identified layer-0 MXFP4 Q/K/V projection `mul_mat` is
causally responsible for the downstream Metal divergence. On an
otherwise frozen Metal execution, route ONLY that projection through
the CPU reference implementation (same inputs, same weights), return
its result to the normal Metal path. Compare three arms:

- A: CPU reference
- B: frozen Metal reference (E1+E2 gates, as in E4/localization)
- C: Metal + layer-0 Q/K/V CPU substitution

Primary question: does replacing only the first divergent MXFP4
`mul_mat` with its CPU result materially restore downstream agreement?
No bitwise equality required after returning to Metal. Generated text
is a secondary observation, not the gate.

## Changes (diagnostic/substitution only, env-gated, default off)

- `llama.cpp/src/models/kimi-linear.cpp` — in `causal_conv1d`, when
  `KIMI_STREAM_LOCALIZE_SUB_QKV_CPU` is set and `il == 0`: name the
  projection output tensors (`kda_sub_Q/K/V`) and pin them to the CPU
  backend via `ggml_backend_sched_set_tensor_backend`.
- `llama.cpp/src/llama-expert-stream-exec.cpp` — CRITICAL FIX: the
  streamed path calls `ggml_backend_sched_reset()` between graph build
  and `ggml_backend_sched_alloc_graph()`, and reset memsets all
  `tensor_backend_id` user assignments to -1 — silently wiping the
  build-time pin (first C run was bit-identical to B because of this).
  The route-subgraph path now re-applies the pin by tensor name AFTER
  the reset and BEFORE the alloc. Rollback = unset the env.
- `tools/phase11_substitute_run.sh` — exact arm-C driver (same envs as
  B plus `KIMI_STREAM_LOCALIZE_SUB_QKV_CPU=1`, same 64-tok config).

No kernel/math changes; default path byte-identical (env unset).

## Results

### Arm C executed cleanly

    localize-sub: EXIT=0   (trace: 46,284 records, same capture set as A/B)

Generated behavior (secondary; Metal text is the open E4 gate):
Prompt 27.3 t/s / Gen 3.6 t/s (vs B 27.9/3.8, A 20.0/8.7). Text output
still garbled — expected, since the defect is not limited to this op.

### Layer 0, first prefill step (ph=0, sp=0) — max|d| by boundary

| boundary | A vs B (frozen) | A vs C (sub) | verdict |
|---|---|---|---|
| l_in / attn_norm | 0 / 0 | 0 / 0 | identical inputs everywhere |
| **kda_Q_proj** | 9.797e-03 | **5.960e-07** | substituted op → CPU agreement |
| **kda_K_proj** | 9.085e-03 | **4.768e-07** | substituted op → CPU agreement |
| **kda_V_proj** | 1.021e-02 | **7.153e-07** | substituted op → CPU agreement |
| kda_Qcur (conv+silu) | 9.318e-04 | **3.725e-08** | branch collapsed |
| kda_Kcur | 1.667e-03 | **5.960e-08** | branch collapsed |
| kda_Vcur | 1.620e-03 | **5.960e-08** | branch collapsed |
| kda_Q_norm | 4.134e-03 | **2.384e-07** | branch collapsed |
| kda_K_norm | 4.664e-03 | **1.788e-07** | branch collapsed |
| kda_g1 (f_a/f_b matmuls) | 1.438e+00 | **1.438e+00** | NOT substituted — unchanged |
| kda_beta (ssm_beta matmul) | 1.370e-03 | **1.370e-03** | NOT substituted — unchanged |
| kda_gate (g_a/g_b matmuls) | 6.752e-03 | **6.752e-03** | NOT substituted — unchanged |
| kda_delta_out | 7.381e-06 | 2.487e-06 | improved (Q/K inputs now CPU) |
| kda_new_state | 6.664e-04 | 4.757e-04 | improved |
| kda_normed | 1.345e-04 | 3.939e-05 | improved |
| kda_gated | 5.649e-05 | 1.934e-05 | improved |
| kda_out / attn_out | 1.111e-04 | **8.172e-05** | improved ~26%, not collapsed |

### Propagation to later layers (A vs C, max|d| at l_in)

| layer | A vs B (frozen) | A vs C (sub) |
|---|---|---|
| 1 | 1.064e-03 | 1.046e-03 (essentially unchanged) |
| 5 | — | 2.783e-02 |
| 12 | — | 7.410e-02 |

Layer-1 `l_in` barely moves because layer-0's output still carries
divergence from the un-substituted g1/beta/gate/wo MXFP4 matmuls (and
the FFN experts). Later layers are unchanged — the seed is re-injected
by every other MXFP4 matmul.

## Interpretation (the directive's primary question)

1. **Does replacing only the first divergent MXFP4 `mul_mat` with its
   CPU result materially restore downstream agreement?**
   - For the substituted op's OWN branch: **yes, completely** —
     projection → conv1d → l2-norm collapse from ~1e-2 to ~1e-7
     (f32-copy noise). The pin is verified effective: the op ran on
     CPU, its result returned to Metal, and downstream sees the CPU
     values.
   - For the full attention output: **partially** — attn_out improves
     ~26% (1.11e-4 → 8.2e-5) but does not collapse, because the same
     layer contains other MXFP4 matmuls that were deliberately not
     substituted and still diverge at their original magnitudes
     (g1 1.438, beta 1.37e-3, gate 6.75e-3).
   - Layer-1 and later: **essentially unchanged** — divergence is
     re-introduced by the remaining MXFP4 matmuls.

2. **Causally confirmed source**: yes — the layer-0 Q/K/V projection
   `mul_mat` is a confirmed source of its branch's divergence
   (substituting it restores CPU agreement for that branch).

3. **Generality** (directive's fallback interpretation): the defect is
   NOT specific to the layer-0 projection. Every other MXFP4-weight
   `mul_mat` examined (ssm_f_a/f_b, ssm_beta, ssm_g_a/g_b — all with
   bit-identical inputs and weights) shows the same class of
   divergence at the same relative scale (~1e-3..1e-2 rel). The
   localization + substitution together indicate **MXFP4 `mul_mat` on
   Metal diverges from CPU generally**, with the layer-0 projection
   merely being the first occurrence.

4. NOT concluded (per directive): whether the defect is MXFP4
   dequantization, MXFP4 accumulation/math, or another implementation
   detail inside the Metal `mul_mat`. That requires the next
   experiment (dequantized-values comparison).

## Problems

- First arm-C build appeared to do nothing (C bit-identical to B):
  `ggml_backend_sched_reset()` between graph build and alloc wipes
  `tensor_backend_id` user assignments. Fixed by re-applying the pin
  after reset in the streamed route path. Documented so future
  substitution experiments pin at the correct phase.
- Generated text remains garbled on C (expected; E4 gate). Not used as
  a correctness criterion.

## Decisions

- Substitution stays env-gated (`KIMI_STREAM_LOCALIZE_SUB_QKV_CPU`,
  default off), narrow (layer-0 Q/K/V projection only), and
  rollback-able by unsetting the env.
- Do not modify the production Metal kernel yet (directive).
- Do not conclude dequant vs accumulation from this experiment.
- Preserve localization traces (A/B) and arm-C trace as evidence.

## Next Phase (candidate, for review)

Isolate the two major components of the suspect operation:
MXFP4 weights → dequantized values → matrix multiplication.
Compare whether CPU and Metal reconstruct different values from the
same MXFP4 blocks/scales (dequantization divergence) or perform
different arithmetic on equivalent reconstructed values (accumulation
divergence). Concrete bounded experiment: dump the dequantized weight
values for one layer-0 projection (CPU dequant vs Metal dequant) and
the per-element products/accumulations at a few positions; classify
which stage first disagrees. Only after that is a kernel-level
correction candidate.

## Reproduction

    # 1. build (fork at the substitution commit)
    cd llama.cpp && cmake --build build-metal -j10

    # 2. arm C (Metal + layer-0 Q/K/V CPU substitution), same config as A/B
    tools/phase11_substitute_run.sh
    # -> benchmarks/results/phase-k1/localize/sub/act.bin (EXIT=0)

    # 3. compare (reuse the preserved A/B traces)
    python3 tools/phase11_localize_analyze.py \
        benchmarks/results/phase-k1/localize/cpu/act.bin \
        benchmarks/results/phase-k1/localize/sub/act.bin --il 0 --ph 0 --sp 0
    python3 tools/phase11_localize_analyze.py \
        benchmarks/results/phase-k1/localize/metal/act.bin \
        benchmarks/results/phase-k1/localize/sub/act.bin --il 0 --ph 0 --sp 0

Artifacts: `benchmarks/results/phase-k1/localize/{cpu,metal,sub}/`
(act.bin + run.log each). Fork: substitution commit (diagnostic-only,
env-gated) on top of the localization commit `66fb8c053`. Main repo:
this report + `tools/phase11_substitute_run.sh`.
