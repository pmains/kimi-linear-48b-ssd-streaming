# Phase 02 Report

## Status

PASS — static inventory and empirical runtime measurements complete.

Conventional (full-residency) Metal execution **fails** on this hardware
(`kIOGPUCommandBufferCallbackErrorOutOfMemory`), which is itself the
decisive measured result: SSD-backed expert streaming is required, not
optional. CPU-only execution works and measures the active working set
at ≈ 2.7 GB RSS. All five acceptance questions are answered with
measured/validated values (see Addendum 2).

## Objective

Determine what actually consumes storage and resident memory for
`Kimi-Linear-48B-A3B-Instruct`: the routed/shared/trunk split, tensor
dimensions, parameter counts, quantized sizes, per-layer expert footprint,
theoretical resident floor, and the Phase 2 memory equation at
representative context sizes — starting from the two tiny official
metadata files, with no full checkpoint download.

## Changes

- `refs/kimi-linear/config.json` — official config from
  `moonshotai/Kimi-Linear-48B-A3B-Instruct` (repo sha
  `e1df551a447157d4658b573f9a695d57658590e9`, 1.7 KB).
- `refs/kimi-linear/model.safetensors.index.json` — official safetensors
  index (1.99 MB, 20,493 tensor names, 20 shards).
- `tools/model_inventory.py` — reproducible static-inventory tool:
  classifies tensors, computes parameter counts from config dims +
  llama.cpp loader shapes, estimates quantized sizes (calibrated against
  real GGUF file sizes), computes KDA state + MLA KV accounting, and
  evaluates the memory equation at 8K/32K/128K/1M context.
- `benchmarks/results/phase-02-static-inventory.json` — machine-readable
  results (all numbers below).
- `ROADMAP.md` — Phase 2 now has an Execution Plan: static-first
  sequencing, GGUF as a separate runtime-validation dependency, and the
  model-file vs Metal-resident accounting distinction.
- No implementation code changed; no model weights downloaded.

## Results

### Architecture facts (from official config + llama.cpp source)

- 27 layers: layer 0 dense MLP (`first_k_dense_replace = 1`); layers 1–26
  MoE with 256 experts each, 8 active per token, 1 shared expert per layer.
- Hybrid attention: 7 MLA (full-attention) layers (1-indexed
  `{4,8,12,16,20,24,27}`) and 20 KDA (linear-attention) layers.
- `hidden_size 2304`, `moe_intermediate_size 1024`, `vocab 163840`,
  `tie_word_embeddings false`, `kv_lora_rank 512`, `qk_nope 128`,
  `qk_rope 64`, `v_head_dim 128`, `kda_head_dim 128`, `model_max_length 1M`.
- llama.cpp `src/models/kimi-linear.cpp` (at commit `2606220d9`):
  - KDA layers are recurrent (`n_head_kv == 0` marks them), executed via
    the hybrid recurrent-memory path (`llama-memory-recurrent.h`);
    state is fixed-size regardless of context.
  - MLA layers use a compressed MQA KV cache (converter forces
    `num_key_value_heads = 1`); per token per layer:
    K = `kv_lora_rank + qk_rope` (576), V = `kv_lora_rank` (512).
  - MoE expert tensors are 3D with the expert dimension **last**:
    `ffn_up_exps/gate_exps [2304, 1024, 256]`, `ffn_down_exps [1024, 2304, 256]`.
    Per-expert slices are contiguous within each tensor — a favorable
    layout for Phase 3 per-expert addressability.
  - Integration surface for streaming identified: `build_moe_ffn(...)`
    consuming `layer.ffn_gate_inp / ffn_up_exps / ffn_gate_exps /
    ffn_down_exps / ffn_exp_probs_b` in `llama_model_kimi_linear::graph`.

### Parameter counts (computed, cross-validated)

| Category | Params | Share |
|---:|---:|---:|
| routed experts | 47.11 B | 95.9 % |
| attention KDA (20 layers) | 0.79 B | 1.6 % |
| embedding | 0.38 B | 0.8 % |
| lm_head | 0.38 B | 0.8 % |
| attention MLA (7 layers) | 0.20 B | 0.4 % |
| shared experts (26 layers) | 0.18 B | 0.4 % |
| dense MLP (layer 0) | 0.06 B | 0.1 % |
| router (26 layers) | 0.02 B | <0.1 % |
| norms | <0.01 B | ~0 % |
| **total** | **49.12 B** | 100 % |

**Routed experts are 95.9 % of the model** (47.11 B of 49.12 B params;
6,656 experts = 26 layers × 256). Non-routed trunk ≈ 2.01 B params (4.1 %).

Cross-validation: computed quantized totals vs real GGUF file sizes
(`AaryanK/Kimi-Linear-48B-A3B-Instruct-GGUF`, public metadata only):

| Quant | Computed | Actual file |
|---:|---:|---:|
| Q8_0 | 52.19 GB | 52.25 GB |
| Q4_K_M | 29.72 GB | 29.69 GB |
| Q4_K_S | 27.90 GB | 27.95 GB |
| Q2_K | 17.98 GB | 18.02 GB |

Agreement within 0.2 % validates the parameter accounting.

### Per-expert footprint

One expert = gate + up + down = 3 × (2304 × 1024) = 7.08 M params:

| Quant | Per expert | Per MoE layer (256) |
|---:|---:|---:|
| f16 | 14.16 MB | 3.62 GB |
| Q8_0 | 7.52 MB | 1.93 GB |
| Q4_K_M | 4.28 MB | 1.10 GB |

### State / KV accounting

- KDA recurrent state (20 layers): **22.4 MB f16, context-independent**.
- MLA KV cache (7 layers): 15,232 B/token f16 (7,616 B/token Q8_0) —
  **the only context-proportional term**.

### Memory equation @ 24 GB (provisional; runtime term unmeasured)

Trunk at Q4_K_M ≈ **1.24 GB**. Equation assumes runtime/Metal ≈ 2.5 GB
(placeholder — unmeasured) and safety margin 2 GB:

| Context | KV (f16 / q8) | Cache headroom (f16 KV) | Experts fit Q4_K_M |
|---:|---:|---:|
| 8K | 0.12 / 0.06 GB | 18.16 GB | 4,240 / 6,656 |
| 32K | 0.50 / 0.25 GB | 17.78 GB | 4,152 / 6,656 |
| 128K | 2.00 / 1.00 GB | 16.29 GB | 3,803 / 6,656 |
| 1M | 15.97 / 7.99 GB | 2.31 GB | 539 / 6,656 |

Key output: even at 128K context, roughly **16 GB of the 24 GB budget can
become expert cache** (≈3,800 of 6,656 experts at Q4_K_M) before the
unmeasured runtime term is replaced with a real number. The model's
advertised 1M context is not compatible with the streaming premise at
24 GB (MLA KV alone ≈ 16 GB f16); the roadmap's 8K/32K/128K targets are
all comfortably feasible.

## Problems

- `runtime/state` (graph buffers, Metal buffers, tokenizer, working
  memory) is a placeholder estimate (2.5 GB), not a measurement. It must
  be measured with a real model in llama.cpp before acceptance
  questions 4–5 are fully answered.
- No llama.cpp-compatible Kimi Linear GGUF is validated locally yet.
  Community GGUFs exist (`AaryanK/...`, `cturan/...`) but at least one
  (`cturan`) carries a public warning that it does not work with standard
  llama.cpp; compatibility with our exact commit `2606220d9` is unverified
  for all of them. This is the Phase-2 runtime dependency, not a blocker
  for the static inventory.
- MLA KV growth at the model's maximum context (1M) is prohibitive at
  24 GB with SSD-backed experts; long-context ambitions must stay within
  the measured feasible range (128K is fine).
- `n_embd_head_k_mla` (per-head key dim = 192) vs the GGUF
  `key_length_mla` (576 = kv_lora_rank + qk_rope, per-token key length)
  naming can mislead; both were resolved from the graph code in
  `kimi-linear.cpp`.

## Decisions

- Execute Phase 2 static-first from official metadata; GGUF treated as a
  separate runtime-validation dependency.
- Preserve the model-file vs Metal-resident accounting distinction; the
  empirical equation is authoritative for feasibility claims.
- The phase's headline output is the **cache-convertible budget per
  context size**, not a binary fit/no-fit answer.
- Accept PARTIAL status now; the GGUF runtime measurement completes the
  phase.
- 8K/32K/128K remain the measurement contexts; 1M is documented as
  infeasible under the 24 GB streaming premise (MLA KV).

## Next Phase

- Obtain and validate a llama.cpp-compatible Kimi Linear GGUF against
  commit `2606220d9` (load + generate smoke test) — this is the
  remaining dependency for completing Phase 2.
- With a real model: measure actual resident footprint (Metal buffers,
  process RSS, graph memory) to replace the 2.5 GB placeholder; verify
  the MLA KV allocation empirically at 8K/32K/128K.
- Confirm the Q4_K_M per-expert slice layout in the GGUF (offsets for
  Phase 3 expert addressability).
- Then answer acceptance questions 4–5 with measured values and update
  this report to PASS.

## Addendum: GGUF selection and header validation (2026-08-13)

### Artifact decision

- The upstream llama.cpp CI does not host a canonical Kimi Linear GGUF:
  `ggml-org/models` contains no Kimi files (verified via HF API), and no
  in-tree CI reference exists (grep of `ci/`, `.github/`, `scripts/`,
  `models/` at commit `2606220d9`). The phase-01 "CI activity" reference
  was PR-level, not a stable artifact. Criteria 1–2 of the plan are
  therefore closed as not obtainable.
- Selected: **`bartowski/moonshotai_Kimi-Linear-48B-A3B-Instruct-GGUF`**,
  `moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf` — the most
  downloaded Kimi Linear GGUF (12.7K), produced with upstream llama.cpp
  (b7966) using imatrix per the repo README. Alternatives documented:
  `mradermacher/...-i1-GGUF` (imatrix, 29.70 GB) and `AaryanK/...`
  (provenance unverified). `cturan/...` carries a public warning that it
  does not work with standard llama.cpp and was rejected.
- Artifact facts: 30,061,058,720 bytes; LFS sha256
  `a1a7d865370652221f937163f7e94c99e1f114861335ba4f8666606843f1620f`;
  HF scanner flags PAIT-GGUF-100 (heuristic, common on K-quant GGUFs;
  independently verified below). Download in progress to
  `models/kimi-linear/` (gitignored).

### Header validation (8 MB range download, no weight data)

`tools/gguf_header_check.py` parses the GGUF v3 header directly:

- `general.architecture = kimi-linear`, name "Kimi Linear 48B A3B Instruct";
  610 tensors (experts merged into 3D per-layer tensors — the safetensors
  index has 20,493 because each expert is a separate HF tensor).
- Hyperparameters match `config.json`: 27 blocks, 256 experts, 8 used,
  1024 expert FFN, 1 shared, 1 leading dense block, 2304 embedding,
  head_count_kv per-layer mask (0 = KDA, 1 = MLA), key_length_mla 192,
  kv_lora_rank 512, ssm conv kernel 4, kda head_dim 128, rope 64.
- **Element count matches the static inventory exactly: 49.12 B params,
  diff 0.** Tensor data totals 30.05 GB, matching the actual file size
  (30.06 GB); the earlier 29.72 GB calibration was a uniform-Q4_K
  approximation — the real quant mix is: expert gate/up = Q4_K
  (1,327,104 B each), expert down = Q6_K (1,935,360 B), router gate_inp
  = F32, shared experts = Q6_K/Q8_0, KDA = Q4_K/Q5_0 mix.
- **Per-expert footprint in this artifact: 4.59 MB** (gate+up+down).
  Per MoE layer: 1.17 GB. (Corrects the earlier uniform-calibration
  figure of 4.28 MB; the true value depends on the quant mix.)
- **Phase 3 groundwork confirmed**: expert tensors are
  `[2304, 1024, 256]` / `[1024, 2304, 256]` with the expert dimension
  last; per-expert slices are contiguous, whole blocks, 16-byte aligned.
  Expert addressability reduces to
  `offset = tensor.offset + expert_id * per_expert_bytes`, computable
  from the header alone. Full per-layer offset table in
  `benchmarks/results/phase-02-gguf-header-analysis.json`.
- Remaining Phase 2 work (acceptance 4–5): build llama.cpp at
  `2606220d9`, load the full model, measure resident/Metal footprint at
  8K/32K/128K, and replace the 2.5 GB runtime placeholder.

## Addendum 2: Empirical runtime measurements (2026-08-13) — closes Phase 2

Build: llama.cpp `2606220d9`, cmake 4.4.2, Apple clang 21, Release,
Metal, `build-metal/`. Artifact: bartowski Q4_K_M, SHA-256 verified.

### Device facts (measured)

- MTL0 (Apple M5) max working set: **18,186 MiB** — the GPU-side budget
  is NOT 24 GB; unified memory is 24 GB but Metal reports 18.2 GB.
- Model file: 28,040 MiB mmap'd (`CPU_Mapped model buffer`); measured
  RSS during CPU decode ≈ **2.7 GB** — only touched pages become
  resident (trunk + active experts).
- Compute buffers (measured): MTL0 202.5 MiB + CPU 113.5 MiB.
- KDA recurrent-state buffer (`llama_memory_recurrent`): **42.81 MiB,
  constant** across context sizes.

### KV cache scaling (measured, linear)

| Context | KV buffer | B/token |
|---:|---:|---:|
| 8K | 63.00 MiB | 8,064 |
| 32K | 252.00 MiB | 8,064 |
| 128K | 1,008.00 MiB | 8,064 |

Measured KV is ~half the f16 static estimate (15,232 B/token): the
actual cache format is more compact than the naive f16 assumption.

### Conventional Metal execution: OOM

`llama-cli` with default settings (and again at `-b 16 -ub 16`): 23
`kIOGPUCommandBufferCallbackErrorOutOfMemory` errors; decode fails with
ret -3. Cause: Metal uploads full weight tensors on first use; MoE
experts are per-layer 3D tensors (~26 GB of expert weights total), so
**all 256 experts per layer are uploaded regardless of routing**;
30 GB > 18.2 GB working set. Graph buffers are not the problem (202 MB).

### CPU-only execution: works

`-ngl 0`: loads, generates correctly ("The capital of France is
Paris."), RSS ≈ 2.7 GB. Confirms the model artifact and our build are
sound, and that the active working set is small.

### Conclusions (measured)

1. Conventional execution is impossible on this hardware. **Expert
   streaming is required, not optional.**
2. The active working set is small and now measured: trunk + 8 active
   experts/layer ≈ 2.7 GB. The project premise is validated.
3. **Design constraint for Phases 3–4**: expert weights must be
   materialized at per-expert granularity (separate tensors/buffers).
   View-slicing the 3D per-layer expert tensor forces whole-tensor Metal
   upload and defeats streaming.
4. Memory equation budget side: GPU-resident components must fit the
   **18.2 GB Metal working set**; CPU RAM (24 GB) holds page cache.

### Updated memory equation (measured, streamed-design target)

    trunk 1.24 + buffers ~0.3 + RS 0.04 + KV + cache + margin <= 18.2 GB (GPU)

| Context | KV | Cache headroom (18.2 GB, margin 2) | Experts fit (4.59 MB) |
|---:|---:|---:|---:|
| 8K | 0.06 GB | ~14.6 GB | ~3,180 |
| 32K | 0.25 GB | ~14.4 GB | ~3,130 |
| 128K | 1.00 GB | ~13.6 GB | ~2,960 |

All roadmap cache budgets (1–12 GB) fit comfortably at 128K context.

### Acceptance (all answered)

1. Routed experts = 95.9 % of params (47.11 B of 49.12 B).
2. Unavoidable resident component ≈ 1.6 GB before KV (trunk 1.24 +
   buffers 0.3 + RS 0.04).
3. Individual expert = 4.59 MB (Q4_K_M: gate/up Q4_K, down Q6_K).
4. Cache budget per context: 13.6–14.6 GB GPU-side at 8K–128K
   (≈ 2,960–3,180 experts).
5. ~8 GB resident: yes — measured active working set is only ≈ 2.7 GB;
   conventional execution does not even fit (OOM).

## Reproduction

Static inventory (no weights downloaded):

```bash
python3 tools/model_inventory.py
# reads refs/kimi-linear/config.json + refs/kimi-linear/model.safetensors.index.json
# writes benchmarks/results/phase-02-static-inventory.json
```

Metadata fetch:

```bash
curl -fsSL "https://huggingface.co/moonshotai/Kimi-Linear-48B-A3B-Instruct/resolve/main/config.json" \
  -o refs/kimi-linear/config.json
curl -fsSL "https://huggingface.co/moonshotai/Kimi-Linear-48B-A3B-Instruct/resolve/main/model.safetensors.index.json" \
  -o refs/kimi-linear/model.safetensors.index.json
```

Reference GGUF sizes (public metadata, no download):

```bash
curl -fsSL "https://huggingface.co/api/models/AaryanK/Kimi-Linear-48B-A3B-Instruct-GGUF/tree/main"
```

Environment: MacBook Air, Apple M5, 24 GB unified memory (`Mac17,3`);
llama.cpp pinned at `2606220d9f2705dab8260633e9f85ce5b081319e`;
Python 3.14.6.
