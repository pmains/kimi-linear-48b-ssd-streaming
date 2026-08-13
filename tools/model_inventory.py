#!/usr/bin/env python3
"""Static model inventory for Kimi-Linear-48B-A3B-Instruct (Phase 2).

Classifies every tensor as trunk / shared-expert / routed-expert / state,
computes parameter counts from config dims + llama.cpp loader shapes
(src/models/kimi-linear.cpp at llama.cpp 2606220d9), estimates quantized
sizes, and evaluates the Phase 2 memory equation at representative
context sizes.

Inputs (fetched from the official repo, no weights downloaded):
    refs/kimi-linear/config.json
    refs/kimi-linear/model.safetensors.index.json

Output:
    benchmarks/results/phase-02-static-inventory.json
"""

import json
import math
from collections import OrderedDict

ROOT = "/Users/pmains/Code/openclaw/kimi"
CONFIG_PATH = f"{ROOT}/refs/kimi-linear/config.json"
INDEX_PATH = f"{ROOT}/refs/kimi-linear/model.safetensors.index.json"
OUT_PATH = f"{ROOT}/benchmarks/results/phase-02-static-inventory.json"

# bytes per parameter for reference quantizations
# Q4_K_M / Q4_K_S / Q2_K / Q8_0 calibrated against AaryanK GGUF file sizes
# vs. computed total params (see report); F16 exact at 2.0
BPQ = {
    "f16": 2.0,
    "q8_0": 1.0625,   # 17 bytes per 16-param block (Q8_0)
    "q4_k_m": 0.605,  # calibrated: 29.69 GB file / 49.2B params
    "q4_k_s": 0.568,  # calibrated: 27.95 GB file / 49.2B params
    "q2_k": 0.366,    # calibrated: 18.02 GB file / 49.2B params
}


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_index():
    with open(INDEX_PATH) as f:
        return json.load(f)["weight_map"]


def layer_kind(cfg):
    """0-based layer classification from config."""
    full_attn = set(cfg["linear_attn_config"]["full_attn_layers"])  # 1-indexed
    n_layers = cfg["num_hidden_layers"]
    dense_lead = cfg.get("first_k_dense_replace", 0)
    kinds = []
    for il in range(n_layers):
        attn = "mla" if (il + 1) in full_attn else "kda"
        moe = "dense" if il < dense_lead else "moe"
        kinds.append((attn, moe))
    return kinds


def build_inventory(cfg, weight_map):
    n_embd = cfg["hidden_size"]
    n_vocab = cfg["vocab_size"]
    n_layers = cfg["num_hidden_layers"]
    n_head = cfg["num_attention_heads"]
    n_expert = cfg["num_experts"]
    n_expert_used = cfg["num_experts_per_token"]
    n_ff_exp = cfg["moe_intermediate_size"]
    n_ff_dense = cfg["intermediate_size"]
    n_shared = cfg["num_shared_experts"]

    kda = cfg["linear_attn_config"]
    kda_head_dim = kda["head_dim"]                 # 128
    d_conv = kda["short_conv_kernel_size"]         # 4
    kv_lora_rank = cfg["kv_lora_rank"]             # 512
    qk_rope = cfg["qk_rope_head_dim"]              # 64
    qk_nope = cfg["qk_nope_head_dim"]              # 128
    qk_head = qk_nope + qk_rope                    # 192
    v_head = cfg["v_head_dim"]                     # 128
    d_inner = n_head * kda_head_dim                # 4096

    kinds = layer_kind(cfg)

    inv = OrderedDict()
    inv["_meta"] = {
        "model": "moonshotai/Kimi-Linear-48B-A3B-Instruct",
        "repo_sha": "e1df551a447157d4658b573f9a695d57658590e9",
        "source": "config.json + model.safetensors.index.json + llama.cpp src/models/kimi-linear.cpp @ 2606220d9",
        "note": "Static accounting only; runtime/Metal residency must be measured separately.",
    }
    inv["config_dims"] = {
        "hidden_size": n_embd, "n_layers": n_layers, "n_head": n_head,
        "n_vocab": n_vocab, "n_expert": n_expert, "n_expert_used": n_expert_used,
        "moe_intermediate_size": n_ff_exp, "dense_intermediate_size": n_ff_dense,
        "n_shared_experts": n_shared, "kv_lora_rank": kv_lora_rank,
        "qk_nope_head_dim": qk_nope, "qk_rope_head_dim": qk_rope,
        "v_head_dim": v_head, "kda_head_dim": kda_head_dim,
        "short_conv_kernel_size": d_conv,
        "full_attn_layers_1idx": kda["full_attn_layers"],
        "kda_layers_1idx": kda["kda_layers"],
        "model_max_length": cfg["model_max_length"],
        "tie_word_embeddings": cfg["tie_word_embeddings"],
    }
    inv["layer_classification"] = {
        f"layer_{il}": {"attention": a, "ffn": m}
        for il, (a, m) in enumerate(kinds)
    }

    inv["n_tensors"] = len(weight_map)

    # ---- tensor shapes (from src/models/kimi-linear.cpp) ----
    shapes = OrderedDict()

    # global
    shapes["token_embd"] = (n_vocab, n_embd)
    shapes["output_norm"] = (n_embd,)
    shapes["output"] = (n_embd, n_vocab)  # tie_word_embeddings=false

    for il, (attn, moe) in enumerate(kinds):
        if attn == "kda":
            shapes[f"layer{il}.conv_q"] = (d_conv, d_inner)
            shapes[f"layer{il}.conv_k"] = (d_conv, d_inner)
            shapes[f"layer{il}.conv_v"] = (d_conv, d_inner)
            shapes[f"layer{il}.q_proj"] = (n_embd, d_inner)
            shapes[f"layer{il}.k_proj"] = (n_embd, d_inner)
            shapes[f"layer{il}.v_proj"] = (n_embd, d_inner)
            shapes[f"layer{il}.f_a"] = (n_embd, kda_head_dim)
            shapes[f"layer{il}.f_b"] = (kda_head_dim, d_inner)
            shapes[f"layer{il}.beta"] = (n_embd, n_head)
            shapes[f"layer{il}.A_log"] = (n_head,)
            shapes[f"layer{il}.dt_bias"] = (d_inner,)
            shapes[f"layer{il}.g_a"] = (n_embd, kda_head_dim)
            shapes[f"layer{il}.g_b"] = (kda_head_dim, d_inner)
            shapes[f"layer{il}.o_norm"] = (kda_head_dim,)
            shapes[f"layer{il}.wo"] = (d_inner, n_embd)
        else:  # mla
            shapes[f"layer{il}.wq"] = (n_embd, n_head * qk_head)
            shapes[f"layer{il}.wkv_a_mqa"] = (n_embd, kv_lora_rank + qk_rope)
            shapes[f"layer{il}.wk_b"] = (qk_nope, kv_lora_rank, n_head)
            shapes[f"layer{il}.wv_b"] = (kv_lora_rank, v_head, n_head)
            shapes[f"layer{il}.wo"] = (n_head * v_head, n_embd)
            shapes[f"layer{il}.kv_a_norm"] = (kv_lora_rank,)
        shapes[f"layer{il}.attn_norm"] = (n_embd,)
        shapes[f"layer{il}.ffn_norm"] = (n_embd,)
        if moe == "dense":
            shapes[f"layer{il}.ffn_gate"] = (n_embd, n_ff_dense)
            shapes[f"layer{il}.ffn_up"] = (n_embd, n_ff_dense)
            shapes[f"layer{il}.ffn_down"] = (n_ff_dense, n_embd)
        else:
            shapes[f"layer{il}.router"] = (n_embd, n_expert)
            shapes[f"layer{il}.router_bias"] = (n_expert,)
            shapes[f"layer{il}.expert_gate"] = (n_embd, n_ff_exp, n_expert)
            shapes[f"layer{il}.expert_up"] = (n_embd, n_ff_exp, n_expert)
            shapes[f"layer{il}.expert_down"] = (n_ff_exp, n_embd, n_expert)
            shapes[f"layer{il}.shexp_gate"] = (n_embd, n_ff_exp * n_shared)
            shapes[f"layer{il}.shexp_up"] = (n_embd, n_ff_exp * n_shared)
            shapes[f"layer{il}.shexp_down"] = (n_ff_exp * n_shared, n_embd)

    # ---- parameter counts per category ----
    def prod(s):
        return math.prod(s)

    cats = OrderedDict()
    cats["embedding"] = prod(shapes["token_embd"])
    cats["lm_head"] = prod(shapes["output"])
    cats["norms"] = prod(shapes["output_norm"]) + sum(
        prod(shapes[f"layer{il}.attn_norm"]) + prod(shapes[f"layer{il}.ffn_norm"])
        for il in range(n_layers))

    attn = OrderedDict(kda=0, mla=0)
    dense_mlp = 0
    router = 0
    shared_expert = 0
    routed_expert = 0
    kda_state_elems = 0
    mla_kv_elems_per_token = 0

    for il, (a, m) in enumerate(kinds):
        if a == "kda":
            for k in ("conv_q", "conv_k", "conv_v", "q_proj", "k_proj", "v_proj",
                      "f_a", "f_b", "beta", "A_log", "dt_bias", "g_a", "g_b",
                      "o_norm", "wo"):
                attn["kda"] += prod(shapes[f"layer{il}.{k}"])
            # recurrent state per layer: s_l = [head_dim, head_dim, n_head]
            kda_state_elems += kda_head_dim * kda_head_dim * n_head
            # conv state per layer: 3 x (d_conv-1) x d_inner
            kda_state_elems += 3 * (d_conv - 1) * d_inner
        else:
            for k in ("wq", "wkv_a_mqa", "wk_b", "wv_b", "wo", "kv_a_norm"):
                attn["mla"] += prod(shapes[f"layer{il}.{k}"])
            # MLA KV per token: K = kv_lora_rank + qk_rope, V = kv_lora_rank (1 head)
            mla_kv_elems_per_token += (kv_lora_rank + qk_rope) + kv_lora_rank

        if m == "dense":
            dense_mlp += sum(prod(shapes[f"layer{il}.{k}"])
                             for k in ("ffn_gate", "ffn_up", "ffn_down"))
        else:
            router += prod(shapes[f"layer{il}.router"]) + prod(shapes[f"layer{il}.router_bias"])
            shared_expert += sum(prod(shapes[f"layer{il}.{k}"])
                                 for k in ("shexp_gate", "shexp_up", "shexp_down"))
            routed_expert += sum(prod(shapes[f"layer{il}.{k}"])
                                 for k in ("expert_gate", "expert_up", "expert_down"))

    cats["attention_kda"] = attn["kda"]
    cats["attention_mla"] = attn["mla"]
    cats["dense_mlp"] = dense_mlp
    cats["router"] = router
    cats["shared_experts"] = shared_expert
    cats["routed_experts"] = routed_expert

    total = sum(cats.values())
    cats["total"] = total

    # ---- per-expert / per-layer footprints ----
    expert_params = (n_embd * n_ff_exp) * 2 + (n_ff_exp * n_embd)  # gate+up+down
    per_layer_experts = expert_params * n_expert
    n_moe_layers = sum(1 for _, m in kinds if m == "moe")
    n_kda_layers = sum(1 for a, _ in kinds if a == "kda")
    n_mla_layers = sum(1 for a, _ in kinds if a == "mla")

    # ---- quantized sizes (bytes) ----
    quant = OrderedDict()
    for q, bp in BPQ.items():
        quant[q] = OrderedDict()
        for name, params in cats.items():
            quant[q][name] = params * bp

    # ---- state / KV ----
    # KDA fixed state (f16): elements -> bytes
    kda_state_f16 = kda_state_elems * 2.0
    # MLA KV per token (f16 and q8): 7 layers
    mla_kv_f16_pt = mla_kv_elems_per_token * 2.0
    mla_kv_q8_pt = mla_kv_elems_per_token * 1.0  # ~1 byte/elem for q8_0 blocks

    state = {
        "kda_fixed_state_f16_bytes": kda_state_f16,
        "kda_fixed_state_f16_mb": kda_state_f16 / 1e6,
        "mla_kv_elems_per_token": mla_kv_elems_per_token,
        "mla_kv_f16_bytes_per_token": mla_kv_f16_pt,
        "mla_kv_q8_bytes_per_token": mla_kv_q8_pt,
        "n_kda_layers": n_kda_layers,
        "n_mla_layers": n_mla_layers,
        "n_moe_layers": n_moe_layers,
        "n_experts_total": n_moe_layers * n_expert,
    }

    # ---- memory equation at context sizes ----
    context_sizes = [8192, 32768, 131072, 1048576]  # 8K, 32K, 128K, 1M (max)
    trunk_q4 = quant["q4_k_m"]["total"] - quant["q4_k_m"]["routed_experts"]
    runtime_estimate_gb = 2.5  # graph/Metal buffers; UNMEASURED, must be measured in GGUF phase
    safety_margin_gb = 2.0
    budget_gb = 24.0

    equation = OrderedDict()
    for ctx in context_sizes:
        for kvq, pt in (("f16", mla_kv_f16_pt), ("q8_0", mla_kv_q8_pt)):
            kv_gb = ctx * pt / 1e9
            headroom_gb = budget_gb - trunk_q4 / 1e9 - runtime_estimate_gb - kv_gb - safety_margin_gb
            equation[f"{ctx}_ctx_{kvq}_kv"] = {
                "kv_bytes": ctx * pt,
                "kv_gb": round(kv_gb, 3),
                "trunk_q4_gb": round(trunk_q4 / 1e9, 3),
                "runtime_est_gb": runtime_estimate_gb,
                "safety_margin_gb": safety_margin_gb,
                "headroom_for_expert_cache_gb": round(headroom_gb, 3),
                "experts_fit_q4_k_m": int(headroom_gb * 1e9 / (expert_params * BPQ["q4_k_m"])),
                "experts_total": n_moe_layers * n_expert,
            }

    out = {
        "meta": inv["_meta"],
        "config_dims": inv["config_dims"],
        "layer_classification": inv["layer_classification"],
        "n_tensors": inv["n_tensors"],
        "params_by_category": cats,
        "per_expert_params": expert_params,
        "per_moe_layer_expert_params": per_layer_experts,
        "quantized_bytes_by_category": quant,
        "state_and_kv": state,
        "memory_equation_24gb": equation,
        "assumptions": {
            "runtime_estimate_gb": runtime_estimate_gb,
            "safety_margin_gb": safety_margin_gb,
            "total_budget_gb": budget_gb,
            "runtime_is_unmeasured_placeholder": True,
        },
    }
    return out


def fmt_gb(b):
    return f"{b/1e9:.2f} GB"


def main():
    cfg = load_config()
    wm = load_index()
    inv = build_inventory(cfg, wm)

    with open(OUT_PATH, "w") as f:
        json.dump(inv, f, indent=2)

    p = inv["params_by_category"]
    total = p["total"]
    print("=" * 70)
    print("Kimi-Linear-48B-A3B static inventory (params)")
    print("=" * 70)
    for k in ("embedding", "lm_head", "norms", "attention_kda", "attention_mla",
              "dense_mlp", "router", "shared_experts", "routed_experts", "total"):
        share = p[k] / total * 100
        print(f"  {k:16s} {p[k]/1e9:8.2f} B  ({share:5.1f}%)")
    print("-" * 70)
    routed_share = p["routed_experts"] / total * 100
    trunk = total - p["routed_experts"]
    print(f"  routed experts : {routed_share:.1f}% of params")
    print(f"  non-routed     : {trunk/1e9:.2f} B params ({100-routed_share:.1f}%)")

    ep = inv["per_expert_params"]
    print(f"\n  per-expert params : {ep/1e6:.2f} M  (gate+up+down, 3 x {cfg['hidden_size']}x{cfg['moe_intermediate_size']})")
    for q in ("f16", "q8_0", "q4_k_m"):
        print(f"    {q:6s}: {ep*BPQ[q]/1e6:6.2f} MB/expert   layer({inv['config_dims']['n_expert']} exp): {ep*inv['config_dims']['n_expert']*BPQ[q]/1e9:.2f} GB")

    print(f"\n  quantized totals (calibrated):")
    for q in ("f16", "q8_0", "q4_k_m", "q4_k_s", "q2_k"):
        print(f"    {q:6s}: {fmt_gb(inv['quantized_bytes_by_category'][q]['total'])}")

    s = inv["state_and_kv"]
    print(f"\n  KDA fixed state (20 layers): {s['kda_fixed_state_f16_mb']:.1f} MB f16 (context-independent)")
    print(f"  MLA KV: {s['mla_kv_f16_bytes_per_token']:.0f} B/token f16, {s['mla_kv_q8_bytes_per_token']:.0f} B/token q8_0")

    print("\n  Memory equation @24GB (trunk Q4 + runtime est 2.5GB + margin 2GB):")
    for k, v in inv["memory_equation_24gb"].items():
        print(f"    {k:24s} KV {v['kv_gb']:6.2f} GB | cache headroom {v['headroom_for_expert_cache_gb']:6.2f} GB "
              f"| {v['experts_fit_q4_k_m']:5d} of {v['experts_total']} experts fit (Q4_K_M)")

    print(f"\n  output: {OUT_PATH}")


if __name__ == "__main__":
    main()
