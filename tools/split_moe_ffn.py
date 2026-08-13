#!/usr/bin/env python3
"""Split llm_graph_context::build_moe_ffn into route + compute halves.

Behavior-preserving for the conventional path: build_moe_ffn becomes
route() then compute(), identical ops in identical order. The split
exists so the Phase 4 streamer can execute routing as its own graph,
read back the selected expert ids, load expert bytes from SSD, then
execute expert compute as a second graph.

Edits src/llama-graph.cpp and src/llama-graph.h in place.
"""
import re
import sys

CPP = "src/llama-graph.cpp"
HDR = "src/llama-graph.h"

src = open(CPP).read().split("\n")

def find_line(pred, start=0):
    for i in range(start, len(src)):
        if pred(src[i]):
            return i
    raise SystemExit(f"pattern not found from line {start+1}")

# --- locate the full build_moe_ffn (second definition) ---
sig_start = None
count = 0
for i, line in enumerate(src):
    if line.startswith("ggml_tensor * llm_graph_context::build_moe_ffn("):
        count += 1
        if count == 2:
            sig_start = i
            break
assert sig_start is not None

sig_end = find_line(lambda l: "selected_experts_in) const {" in l, sig_start)
logits_line = find_line(lambda l: l.strip() == "ggml_tensor * logits = nullptr;", sig_start)
route_end = find_line(lambda l: l.strip() == "ggml_build_forward_expand(gf, weights);", sig_start)
reshape_line = find_line(lambda l: "cur = ggml_reshape_3d(ctx0, cur, n_embd, 1, n_tokens);" in l, sig_start)
ret_line = find_line(lambda l: l.strip() == "return moe_out;", sig_start)
fn_close = find_line(lambda l: l.strip() == "}", ret_line)
assert reshape_line == route_end + 2, (route_end, reshape_line)  # blank + reshape

# sanity: the compute section ends right before the function's closing brace
assert fn_close == ret_line + 1

signature = src[sig_start:sig_end + 1]          # includes ") const {"
decls = src[sig_end + 1:logits_line]            # n_embd / n_tokens / weight_before_ffn (+blank)
route_code = src[logits_line:route_end + 1]     # logits .. expand(weights)
compute_code = src[reshape_line:ret_line + 1]   # reshape .. return moe_out

# --- assemble new functions ---
def indent_block(block, extra=""):
    return [extra + l if l.strip() else l for l in block]

new_build_moe_ffn = signature + [
    "    const int64_t n_embd   = cur->ne[0];",
    "    const int64_t n_tokens = cur->ne[1];",
    "    const bool weight_before_ffn = arch == LLM_ARCH_LLAMA4; // for llama4, we apply the sigmoid-ed weights before the FFN",
    "",
    "    // Phase 4: split into route (logits -> probs -> topk -> weights) and",
    "    // compute (expert matmuls + weighted sum) so the uncached streamer can",
    "    // execute routing as its own graph, load the selected experts from",
    "    // backing storage, then execute expert compute as a second graph.",
    "    // The conventional path is unchanged: identical ops, identical order.",
    "    ggml_tensor * selected_experts = nullptr;",
    "    ggml_tensor * weights = nullptr;",
    "",
    "    build_moe_ffn_route(cur, gate_inp, gate_inp_b, exp_probs_b,",
    "                        n_expert, n_expert_used, gating_op, il,",
    "                        probs_in, selected_experts_in,",
    "                        &selected_experts, &weights);",
    "",
    "    return build_moe_ffn_compute(cur, weights, selected_experts,",
    "                                 up_exps, up_exps_b, gate_exps, gate_exps_b,",
    "                                 down_exps, down_exps_b,",
    "                                 up_exps_s, gate_exps_s, down_exps_s,",
    "                                 gate_up_exps, gate_up_exps_b,",
    "                                 type_op, n_expert_used, il, weight_before_ffn);",
    "}",
]

route_sig = [
    "ggml_tensor * llm_graph_context::build_moe_ffn_route(",
    "         ggml_tensor * cur,",
    "         ggml_tensor * gate_inp,",
    "         ggml_tensor * gate_inp_b,",
    "         ggml_tensor * exp_probs_b,",
    "             int64_t   n_expert,",
    "             int64_t   n_expert_used,",
    "        llama_expert_gating_func_type gating_op,",
    "                 int   il,",
    "         ggml_tensor * probs_in,",
    "         ggml_tensor * selected_experts_in,",
    "         ggml_tensor ** selected_experts_out,",
    "         ggml_tensor ** weights_out) const {",
]
route_body = ["    const int64_t n_tokens = cur->ne[1];", ""] + route_code + [
    "    *selected_experts_out = selected_experts;",
    "    *weights_out = weights;",
    "",
    "    return selected_experts;",
    "}",
]

compute_sig = [
    "ggml_tensor * llm_graph_context::build_moe_ffn_compute(",
    "         ggml_tensor * cur,",
    "         ggml_tensor * weights,",
    "         ggml_tensor * selected_experts,",
    "         ggml_tensor * up_exps,",
    "         ggml_tensor * up_exps_b,",
    "         ggml_tensor * gate_exps,",
    "         ggml_tensor * gate_exps_b,",
    "         ggml_tensor * down_exps,",
    "         ggml_tensor * down_exps_b,",
    "         ggml_tensor * up_exps_s,",
    "         ggml_tensor * gate_exps_s,",
    "         ggml_tensor * down_exps_s,",
    "         ggml_tensor * gate_up_exps,",
    "         ggml_tensor * gate_up_exps_b,",
    "     llm_ffn_op_type   type_op,",
    "             int64_t   n_expert_used,",
    "                 int   il,",
    "                bool   weight_before_ffn) const {",
]
compute_body = [
    "    const int64_t n_embd   = cur->ne[0];",
    "    const int64_t n_tokens = cur->ne[1];",
    "",
] + compute_code + [
    "}",
]

new_section = (
    new_build_moe_ffn + [""] +
    route_sig + route_body + [""] +
    compute_sig + compute_body
)

out = src[:sig_start] + new_section + src[fn_close + 1:]
open(CPP, "w").write("\n".join(out))
print(f"llama-graph.cpp: replaced lines {sig_start+1}..{fn_close+1} "
      f"({fn_close - sig_start + 1} lines) with {len(new_section)} lines")

# --- header declarations ---
hdr = open(HDR).read()
anchor = "             ggml_tensor * selected_experts_in) const;\n"
assert hdr.count(anchor) == 1, hdr.count(anchor)
decls_add = anchor + """
    // Phase 4: routing half of build_moe_ffn (logits -> probs -> topk -> weights).
    // Executed as its own graph by the uncached streamer so the selected expert
    // ids can be read back and expert bytes loaded from backing storage before
    // expert compute is built. Outputs via selected_experts_out / weights_out.
    ggml_tensor * build_moe_ffn_route(
             ggml_tensor * cur,
             ggml_tensor * gate_inp,
             ggml_tensor * gate_inp_b,
             ggml_tensor * exp_probs_b,
                 int64_t   n_expert,
                 int64_t   n_expert_used,
            llama_expert_gating_func_type gating_op,
                     int   il,
             ggml_tensor * probs_in,
             ggml_tensor * selected_experts_in,
             ggml_tensor ** selected_experts_out,
             ggml_tensor ** weights_out) const;

    // Phase 4: compute half of build_moe_ffn (expert matmuls + weighted sum),
    // consuming pre-loaded expert tensors and the routed weights/ids.
    ggml_tensor * build_moe_ffn_compute(
             ggml_tensor * cur,
             ggml_tensor * weights,
             ggml_tensor * selected_experts,
             ggml_tensor * up_exps,
             ggml_tensor * up_exps_b,
             ggml_tensor * gate_exps,
             ggml_tensor * gate_exps_b,
             ggml_tensor * down_exps,
             ggml_tensor * down_exps_b,
             ggml_tensor * up_exps_s,
             ggml_tensor * gate_exps_s,
             ggml_tensor * down_exps_s,
             ggml_tensor * gate_up_exps,
             ggml_tensor * gate_up_exps_b,
         llm_ffn_op_type   type_op,
                 int64_t   n_expert_used,
                     int   il,
                    bool   weight_before_ffn) const;
"""
open(HDR, "w").write(hdr.replace(anchor, decls_add))
print("llama-graph.h: added route/compute declarations")
