# Stage 7 — Verify the Native Position Mechanism

## Status

**COMPLETE (2026-08-17) — evidence-based verdict: Kimi Linear uses NoPE
(no positional encoding).** Neither the reference implementation nor the
llama.cpp path applies RoPE, YaRN, or any learned/other position embedding.
The MLA layers reserve 64 "rope" dims structurally (DeepSeek-V3-style MLA
layout) but never rotate them; KDA layers carry position only implicitly via
causal convolution and the recurrent scan. llama.cpp reproduces this exactly
(rope_type = NONE, no `ggml_rope` op, no rope freqs, no rope shift). No
material reference-vs-llama semantic discrepancy was found.

## Deliverable answer

1. **What positional mechanism does Kimi Linear natively use?** NoPE.
   Position information comes only from (a) causal masking in the 7 MLA
   (full-attention) layers and (b) causal conv1d (kernel 4) + recurrent
   delta-net scan in the 20 KDA (linear-attention) layers. The 64-dim
   "rope" slice (`qk_rope_head_dim=64`, `qk_nope_head_dim=128`) is a
   structural artifact of the MLA layout: it is split out and re-concatenated
   without any rotation in the reference forward pass.
2. **Does our llama.cpp path reproduce it?** Yes — semantically identical.
   `llama_model_rope_type()` returns `LLAMA_ROPE_TYPE_NONE` for
   `LLM_ARCH_KIMI_LINEAR`; `src/models/kimi-linear.cpp` builds `Qcur`/`Kcur`
   by concatenating the nope and "pe" slices with no `ggml_rope` call (code
   comment: "Kimi MLA does NOT apply RoPE"); KDA layers use
   `causal_conv1d` + `ggml_kda_scan`. No rope frequency tensors are built and
   the KV-cache K-shift path is gated off (`rope_type != NONE`).
3. **Which context-extension controls can actually affect this model?**
   None of the rope-based ones. `--rope-scaling` (linear/yarn), `--yarn-*`,
   `--rope-freq-base`, `--rope-freq-scale` are all NOT APPLICABLE because no
   rope op exists in the graph to consume them. The only participating
   context controls are memory-allocation knobs: `--ctx-size` (KV cache +
   recurrent state), cache types, and the expert cache budget. The model's
   native context is 1,048,576 tokens (`model_max_length` in config and
   `context_length` in GGUF), so "context extension" for this model is a
   memory-budget question, not a positional-extrapolation question.

## Reference implementation trace

Source: `moonshotai/Kimi-Linear-48B-A3B-Instruct` (HF, fetched 2026-08-17):
`config.json`, `modeling_kimi.py` (KimiLinearForCausalLM, 1101 lines).

Config facts:

- `rope_theta = 10000.0`, `rope_scaling = None`
- `qk_rope_head_dim = 64`, `qk_nope_head_dim = 128`, `v_head_dim = 128`
- `kv_lora_rank = 512`, `q_lora_rank = None`, `mla_use_nope = True`
- `linear_attn_config.full_attn_layers = [4, 8, 12, 16, 20, 24, 27]` (MLA)
- `linear_attn_config.kda_layers = [1,2,3,5,6,7,9,10,11,13,14,15,17,18,19,21,22,23,25,26]` (KDA)
- `linear_attn_config.head_dim = 128` (KDA head dim)
- `model_max_length = 1048576` (native 1M context)
- 27 layers, 32 heads, hidden 2304

Modeling-code facts (`modeling_kimi.py`):

- `KimiMLAAttention.forward` splits `q_states` into `q_pass`/`q_rot` and
  `compressed_kv` into `k_pass`/`k_rot`, then immediately rebuilds
  `query_states = cat(q_pass, q_rot)` and `key_states = cat(k_pass, k_rot)`
  — **no `apply_rotary_pos_emb`, no cos/sin tables, no rotary module, no
  position embedding application anywhere in the file** (grep for
  rotary/apply_rotary/cos/sin/freq_cis/position_embeddings found only the
  stored-but-unused `self.rope_theta = config.rope_theta`).
- `eager_attention_forward` (defined locally) is plain softmax attention:
  `attn_weights = matmul(Q,Kᵀ)*scale; softmax; matmul(attn,V)` — no
  positional input.
- `KimiDeltaAttention` (KDA) uses `q_conv1d/k_conv1d/v_conv1d` + fused gate
  (`fused_kda_gate` with `A_log`/`dt_bias`) and a recurrent scan — no
  positional encoding.
- `position_ids` is derived from `cache_position` and passed down but is
  ignored by both attention classes (MLA forward accepts it via `**kwargs`).

## llama.cpp execution path trace

Source: workspace fork of llama.cpp at `0a6b2df63` (`src/models/kimi-linear.cpp`,
`src/llama-model.cpp`, `src/llama-kv-cache.cpp`, `src/llama-arch.cpp`,
`src/llama-hparams.cpp`).

- `llama_model_rope_type()` → `LLAMA_ROPE_TYPE_NONE` for `LLM_ARCH_KIMI_LINEAR`
  (grouped with Mamba/Jamba/RWKV/etc. "these models do not use RoPE").
- `src/models/kimi-linear.cpp` MLA branch:
  - comment: "Note: Kimi MLA does NOT apply RoPE (rotary_emb=None in vLLM)"
  - comment: "k_pe is used directly without RoPE"
  - `Qcur = concat(q_nope_absorbed, q_pe)`; `Kcur = concat(kv_cmpr, k_pe)`
  - zero `ggml_rope` calls; zero `build_rope_freqs` calls in the file.
- KDA branch: `causal_conv1d(Q/K/V)` + `ggml_l2_norm` + `build_delta_net`
  (chunked/recurrent) — no rope.
- GGUF carries `kimi-linear.rope.freq_base = 10000.0` and
  `kimi-linear.rope.dimension_count = 64` metadata, but nothing consumes
  them for this arch (no rope op is built; `get_rope_freq_base` is only
  reached from the K-shift path, which is gated by `rope_type != NONE`).
- KV-cache K-shift (`build_graph_shift`) is only invoked when
  `hparams.rope_type != LLAMA_ROPE_TYPE_NONE` (`llama-kv-cache.cpp:861`) —
  skipped for Kimi Linear.

## GGUF metadata (evidence)

- `kimi-linear.context_length = 1048576`
- `kimi-linear.rope.freq_base = 10000.0`
- `kimi-linear.rope.dimension_count = 64`
- `kimi-linear.attention.head_count = 32`
- `kimi-linear.attention.head_count_kv` = per-layer array
  `[0,0,0,1,0,0,0,1,…]` — 0 for the 20 KDA layers, 1 for the 7 MLA layers
  (positions 4,8,12,16,20,24,27), matching the reference layer split.
- `kimi-linear.kda.head_dim = 128`, `kimi-linear.ssm.conv_kernel = 4`
- `kimi-linear.attention.kv_lora_rank = 512`
- `kimi-linear.attention.key_length = 576` (= 512 + 64 rope),
  `key_length_mla = 192` (= 128 nope + 64 rope), `value_length_mla = 128`

## Classification

| Mechanism / knob | Classification | Implementation evidence |
| --- | --- | --- |
| RoPE (standard) | **NOT APPLICABLE** | Reference reserves 64 rope dims but never rotates (no apply_rotary anywhere); llama.cpp sets rope_type=NONE and builds no ggml_rope op |
| RoPE linear scaling | **NOT APPLICABLE** | No rope op exists to scale; knob feeds nothing for this arch |
| YaRN | **NOT APPLICABLE** | `rope_scaling=None` in reference config; no rope op in llama.cpp |
| NoPE / non-RoPE | **SUPPORTED (native)** | Both implementations pass the "rope" slice through unrotated; position comes from causal mask + causal conv/recurrent scan |
| MLA attention position handling | **SUPPORTED** | Causal mask only; "rope" dims are plain latent dims in both reference and llama.cpp |
| KDA position handling | **SUPPORTED** | Causal conv1d (kernel 4) + recurrent scan; no explicit encoding in either implementation |
| `--ctx-size` | **SUPPORTED (participates)** | Allocates KV cache (MLA layers) + recurrent state (KDA layers); native limit 1M from config/GGUF |
| `--rope-scaling`, `--yarn-*`, `--rope-freq-base`, `--rope-freq-scale` | **NOT APPLICABLE** | No rope op in the kimi-linear graph consumes these parameters |
| K-shift / KV cache shift | **NOT APPLICABLE** | Gated by `rope_type != NONE` (llama-kv-cache.cpp:861); rope_type is NONE for this arch |
| Sliding-window attention | **NOT APPLICABLE** | Not present in reference config or GGUF metadata |

## Native/reference configuration baseline

The native configuration is the default llama.cpp behavior for this arch:
no rope scaling, no rope frequency overrides, `rope_type = NONE`. The live
server already runs it (flags observed: `--ctx-size 32768 --host 127.0.0.1
--port 18080 --parallel 1 --no-mmap --slot-save-path …`; no rope flags),
and a smoke completion under this configuration returned `ok` (native-config
run, 2026-08-17). No discriminating rope experiment is warranted because
source evidence shows no rope op exists in the graph — there is nothing for
a rope knob to change.

## Implication for context-window work (not started)

Because the mechanism is NoPE with a native 1M context, a context-window
ladder for this model is a **memory-budget ladder** (KV cache + recurrent
state + expert cache), not a positional-extrapolation ladder. Defining and
running that ladder is intentionally deferred: per the Stage 7 directive,
no ladder is defined or begun until the native mechanism is verified. This
artifact is that verification. Whether/how to proceed with the ladder will
be decided separately.

## Reproduction

```bash
# Reference config + modeling code (evidence sources)
curl -sL https://huggingface.co/moonshotai/Kimi-Linear-48B-A3B-Instruct/raw/main/config.json
curl -sL https://huggingface.co/moonshotai/Kimi-Linear-48B-A3B-Instruct/raw/main/modeling_kimi.py

# llama.cpp rope-type assignment
grep -n "KIMI_LINEAR" src/llama-model.cpp   # -> LLAMA_ROPE_TYPE_NONE

# No ggml_rope in the Kimi graph
grep -n "ggml_rope\|build_rope" src/models/kimi-linear.cpp

# K-shift gate
grep -n "rope_type != LLAMA_ROPE_TYPE_NONE" src/llama-kv-cache.cpp   # line 861

# GGUF metadata
python3 - <<'EOF'
import sys; sys.path.insert(0, 'llama.cpp/gguf-py')
from gguf import GGUFReader
r = GGUFReader('models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf')
for k in ('kimi-linear.rope.freq_base','kimi-linear.rope.dimension_count',
          'kimi-linear.context_length','kimi-linear.attention.head_count_kv'):
    print(k, '=', r.fields[k].contents())
EOF
```

## Environment

- Workspace: /Users/pmains/Code/openclaw/kimi (HEAD `f8083e0` at stage close)
- llama.cpp dev fork: `0a6b2df63`
- Model: `moonshotai/Kimi-Linear-48B-A3B-Instruct` Q4_K_M GGUF
  (30,061,058,720 bytes; 610 tensors; kv_count 48; GGUF v3)
- Reference files fetched 2026-08-17 from HF main branch
