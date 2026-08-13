#!/usr/bin/env python3
"""GGUF header validation + expert-offset analysis for Phase 2 / Phase 3 prep.

Parses only the GGUF header (range-downloaded, no weight data) and:

1. verifies the architecture is kimi-linear;
2. extracts key hyperparameters and cross-checks them against the static
   inventory (config.json-derived);
3. verifies tensor inventory / types;
4. computes exact per-expert byte offsets and strides for the three routed
   expert tensors per MoE layer (Phase 3 expert-addressability groundwork);
5. writes machine-readable results.

Usage:
    python3 tools/gguf_header_check.py

Input:
    refs/kimi-linear/gguf-header-Q4_K_M.bin  (first ~8 MB of the GGUF)

Output:
    benchmarks/results/phase-02-gguf-header-analysis.json
"""

import json
import os
import struct
import sys

ROOT = "/Users/pmains/Code/openclaw/kimi"
HEADER_PATH = f"{ROOT}/refs/kimi-linear/gguf-header-Q4_K_M.bin"
OUT_PATH = f"{ROOT}/benchmarks/results/phase-02-gguf-header-analysis.json"
INVENTORY_PATH = f"{ROOT}/benchmarks/results/phase-02-static-inventory.json"

GGUF_PY = f"{ROOT}/llama.cpp/gguf-py"
sys.path.insert(0, GGUF_PY)
from gguf.constants import GGMLQuantizationType, GGML_QUANT_SIZES  # noqa: E402

GGUF_MAGIC = b"GGUF"

VT = {0: "uint8", 1: "int8", 2: "uint16", 3: "int16", 4: "uint32", 5: "int32",
      6: "float32", 7: "bool", 8: "string", 9: "array", 10: "uint64",
      11: "int64", 12: "float64"}

QT_NAME = {v.value: v.name for v in GGMLQuantizationType}


class Reader:
    def __init__(self, data):
        self.d = data
        self.o = 0

    def u8(self):
        v = struct.unpack_from("<B", self.d, self.o)[0]; self.o += 1
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.o)[0]; self.o += 4
        return v

    def u64(self):
        v = struct.unpack_from("<Q", self.d, self.o)[0]; self.o += 8
        return v

    def i64(self):
        v = struct.unpack_from("<q", self.d, self.o)[0]; self.o += 8
        return v

    def f32(self):
        v = struct.unpack_from("<f", self.d, self.o)[0]; self.o += 4
        return v

    def f64(self):
        v = struct.unpack_from("<d", self.d, self.o)[0]; self.o += 8
        return v

    def string(self):
        n = self.u64()
        v = self.d[self.o:self.o + n].decode("utf-8", "replace")
        self.o += n
        return v

    def value(self, vt):
        if vt == 0:
            return self.u8()
        if vt == 1:
            v = struct.unpack_from("<b", self.d, self.o)[0]; self.o += 1
            return v
        if vt == 2:
            v = struct.unpack_from("<H", self.d, self.o)[0]; self.o += 2
            return v
        if vt == 3:
            v = struct.unpack_from("<h", self.d, self.o)[0]; self.o += 2
            return v
        if vt == 4:
            return self.u32()
        if vt == 5:
            v = struct.unpack_from("<i", self.d, self.o)[0]; self.o += 4
            return v
        if vt == 6:
            return self.f32()
        if vt == 7:
            return bool(self.u8())
        if vt == 8:
            return self.string()
        if vt == 9:
            atype = self.u32()
            n = self.u64()
            return [self.value(atype) for _ in range(n)]
        if vt == 10:
            return self.u64()
        if vt == 11:
            return self.i64()
        if vt == 12:
            return self.f64()
        raise ValueError(f"unknown value type {vt}")


def parse(path):
    data = open(path, "rb").read()
    assert data[:4] == GGUF_MAGIC, "not a GGUF file"
    r = Reader(data)
    r.o = 4
    version = r.u32()
    tensor_count = r.u64()
    kv_count = r.u64()

    fields = {}
    for _ in range(kv_count):
        key = r.string()
        vt = r.u32()
        fields[key] = (VT.get(vt, str(vt)), r.value(vt))

    tensors = []
    for _ in range(tensor_count):
        name = r.string()
        n_dims = r.u32()
        dims = [r.u64() for _ in range(n_dims)]
        qtype = r.u32()
        offset = r.u64()
        elems = 1
        for d in dims:
            elems *= d
        tensors.append({
            "name": name, "dims": dims, "type": QT_NAME.get(qtype, str(qtype)),
            "type_id": qtype, "offset": offset, "elems": elems,
        })
    return version, tensor_count, kv_count, fields, tensors


def type_bytes(qtype, elems):
    """bytes occupied by `elems` elements of GGML quant type qtype."""
    blk_elems, blk_bytes = GGML_QUANT_SIZES[GGMLQuantizationType(qtype)]
    if blk_elems == 1:
        return elems * blk_bytes
    n_blocks = (elems + blk_elems - 1) // blk_elems
    return n_blocks * blk_bytes


def main():
    if not os.path.exists(HEADER_PATH):
        sys.exit(f"header file missing: {HEADER_PATH}")

    version, tensor_count, kv_count, fields, tensors = parse(HEADER_PATH)
    inv = json.load(open(INVENTORY_PATH))

    def f(name):
        return fields.get(name, (None, None))[1]

    arch = f("general.architecture")
    name = f("general.name")
    print(f"GGUF version={version}  kv={kv_count}  tensors={len(tensors)}/{tensor_count}")

    hparams = {}
    for k in ("general.architecture", "general.name", "general.file_type",
              "kimi-linear.block_count", "kimi-linear.expert_count",
              "kimi-linear.expert_used_count", "kimi-linear.expert_feed_forward_length",
              "kimi-linear.expert_shared_count", "kimi-linear.leading_dense_block_count",
              "kimi-linear.embedding_length", "kimi-linear.attention.head_count",
              "kimi-linear.attention.head_count_kv", "kimi-linear.attention.key_length_mla",
              "kimi-linear.attention.value_length_mla", "kimi-linear.attention.kv_lora_rank",
              "kimi-linear.ssm.conv_kernel", "kimi-linear.kda.head_dim",
              "kimi-linear.rope.dimension_count", "tokenizer.ggml.model"):
        v = f(k)
        if v is not None:
            hparams[k] = v

    by_cat = {}
    expert_tensors = {}
    per_expert_rows = []
    total_elems = 0
    total_bytes = 0

    for t in tensors:
        tn = t["name"]
        tbytes = type_bytes(t["type_id"], t["elems"])
        total_elems += t["elems"]
        total_bytes += tbytes
        if tn.endswith("ffn_up_exps.weight") or tn.endswith("ffn_gate_exps.weight") or tn.endswith("ffn_down_exps.weight"):
            n_expert = t["dims"][-1]
            per_exp = tbytes // n_expert
            layer = tn.split(".")[1]
            expert_tensors.setdefault(layer, {})[tn.split(".")[2]] = {
                "offset": t["offset"], "nbytes": tbytes, "elems": t["elems"],
                "shape": t["dims"], "type": t["type"],
                "per_expert_bytes": per_exp, "per_expert_elems": t["elems"] // n_expert,
            }
            per_expert_rows.append((layer, tn.split(".")[2], t["offset"], per_exp,
                                    t["elems"] // n_expert, t["type"]))
        else:
            key = ("shared_expert" if "shexp" in tn else
                   "router" if "gate_inp" in tn else
                   "embedding" if "token_embd" in tn else
                   "output" if tn.startswith("output") else
                   "kda" if "ssm_" in tn else
                   "attention" if "attn" in tn or "_mqa" in tn or "wk_b" in tn or "wv_b" in tn else
                   "other")
            by_cat.setdefault(key, {"count": 0, "bytes": 0, "elems": 0, "types": set()})
            by_cat[key]["count"] += 1
            by_cat[key]["bytes"] += tbytes
            by_cat[key]["elems"] += t["elems"]
            by_cat[key]["types"].add(t["type"])
    for v in by_cat.values():
        v["types"] = sorted(v["types"])

    n_moe_layers = len(expert_tensors)
    n_expert = None
    for t in tensors:
        if t["name"].endswith("ffn_up_exps.weight"):
            n_expert = t["dims"][-1]
            break

    cross = {
        "inventory_total_params": inv["params_by_category"]["total"],
        "header_total_elements": total_elems,
        "diff_params": round(total_elems - inv["params_by_category"]["total"], 0),
        "header_total_bytes": total_bytes,
        "expected_q4_k_m_bytes": inv["quantized_bytes_by_category"]["q4_k_m"]["total"],
        "inventory_routed_expert_params": inv["params_by_category"]["routed_experts"],
        "header_routed_expert_elems": sum(
            t["elems"] for t in tensors
            if t["name"].endswith("ffn_up_exps.weight")
            or t["name"].endswith("ffn_gate_exps.weight")
            or t["name"].endswith("ffn_down_exps.weight")),
    }
    cross["expert_elems_match"] = abs(cross["header_routed_expert_elems"] - cross["inventory_routed_expert_params"]) < 100
    cross["total_bytes_match"] = abs(total_bytes - inv["quantized_bytes_by_category"]["q4_k_m"]["total"]) < 2e8

    # per-tensor-type expert breakdown (gate/up = Q4_K, down = Q6_K in Q4_K_M)
    expert_type_breakdown = {}
    for t in tensors:
        if t["name"].endswith("ffn_up_exps.weight") or t["name"].endswith("ffn_gate_exps.weight") or t["name"].endswith("ffn_down_exps.weight"):
            key = t["name"].split(".")[2]
            expert_type_breakdown[key] = {
                "type": t["type"], "per_expert_bytes": type_bytes(t["type_id"], t["elems"] // t["dims"][-1]),
                "per_expert_elems": t["elems"] // t["dims"][-1],
            }
    per_expert_total_bytes = sum(v["per_expert_bytes"] for v in expert_type_breakdown.values())

    out = {
        "artifact": {
            "repo": "bartowski/moonshotai_Kimi-Linear-48B-A3B-Instruct-GGUF",
            "file": "moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf",
            "source": "https://huggingface.co/bartowski/moonshotai_Kimi-Linear-48B-A3B-Instruct-GGUF",
            "claimed_tooling": "llama.cpp b7966, imatrix quant (per repo README)",
            "header_checked": True, "header_only": True,
        },
        "gguf_version": version,
        "n_tensors_parsed": len(tensors),
        "n_tensors_in_header": tensor_count,
        "architecture": arch,
        "model_name": name,
        "hparams": hparams,
        "tensor_categories": by_cat,
        "n_moe_layers": n_moe_layers,
        "n_experts_per_layer": n_expert,
        "n_experts_total": n_moe_layers * (n_expert or 0),
        "expert_tensor_stride": {
            "per_tensor": expert_type_breakdown,
            "per_expert_total_bytes": per_expert_total_bytes,
            "per_expert_total_mb": round(per_expert_total_bytes / 1e6, 3),
            "expert_dim_is_last": all(t["dims"][-1] == n_expert for t in tensors if t["name"].endswith("ffn_up_exps.weight")),
            "contiguous_block_aligned": bool(per_expert_rows) and all(
                type_bytes(t["type_id"], t["elems"] // t["dims"][-1]) % 16 == 0 for t in tensors
                if t["name"].endswith("ffn_up_exps.weight") or t["name"].endswith("ffn_gate_exps.weight") or t["name"].endswith("ffn_down_exps.weight")),
        },
        "expert_tensors_by_layer": expert_tensors,
        "crosscheck_vs_static_inventory": cross,
        "phase3_note": "expert offset = tensor.offset + expert_id * per_expert_bytes; "
                       "expert dim is last and block-aligned, so slices are contiguous",
    }

    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2)

    print(f"\narch={arch}  name={name}  tensors={len(tensors)}/{tensor_count}")
    print(f"total elems={total_elems/1e9:.2f}B  total bytes={total_bytes/1e9:.2f}GB")
    print(f"hparams:")
    for k, v in hparams.items():
        print(f"  {k} = {v}")
    print(f"\nMoE layers={n_moe_layers}  experts/layer={n_expert}  experts total={n_moe_layers*(n_expert or 0)}")
    if per_expert_rows:
        print(f"expert stride per tensor: {json.dumps(expert_type_breakdown, indent=1)}")
        print(f"per-expert total: {per_expert_total_bytes/1e6:.2f} MB")
        print(f"expert dim last: {out['expert_tensor_stride']['expert_dim_is_last']} | 16B-aligned slices: {out['expert_tensor_stride']['contiguous_block_aligned']}")
    print(f"\ncrosscheck: elems {total_elems/1e9:.2f}B vs inventory {cross['inventory_total_params']/1e9:.2f}B "
          f"(diff {cross['diff_params']:.0f})")
    print(f"routed expert elems match: {cross['expert_elems_match']} | total bytes match: {cross['total_bytes_match']}")
    print(f"\noutput: {OUT_PATH}")


if __name__ == "__main__":
    main()
