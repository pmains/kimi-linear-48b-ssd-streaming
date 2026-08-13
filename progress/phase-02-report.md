# Phase 02 Report

## Status

PARTIAL — static model inventory complete; runtime-resident measurement pending a validated llama.cpp-compatible GGUF.

Per the Phase 2 execution plan (recorded in `ROADMAP.md`), the GGUF is a separate runtime-validation dependency and does not block the static analysis. Acceptance questions 1–3 are answerable from the static inventory; questions 4–5 (remaining cache budget per context size, ~8 GB resident feasibility) are answered provisionally with an explicit unmeasured runtime term that the GGUF phase must replace with a measurement.

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
