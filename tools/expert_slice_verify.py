#!/usr/bin/env python3
"""Phase 3 expert-addressability verification.

Demonstrates that an arbitrary routed expert can be located directly from
(layer, expert_id, tensor) using GGUF header metadata, and that its quantized
bytes can be retrieved independently via byte-range pread — without reading or
materializing the complete routed-expert collection.

Steps:

1. Parse the GGUF header directly from the model file (metadata only).
2. Extract the 78 routed-expert tensors (26 MoE layers x gate/up/down) with
   their data offsets, shapes, quantization types, and per-expert strides.
3. Cross-check offsets and per-expert byte counts against the Phase 2 header
   analysis (benchmarks/results/phase-02-gguf-header-analysis.json).
4. For sampled (layer, expert_id) across all tensor types, retrieve the expert
   slice via pread() and verify byte-for-byte equality against the mmap view.
5. Report PASS/FAIL and write machine-readable results.

Usage:
    python3 tools/expert_slice_verify.py
"""

import json
import mmap
import os
import struct
import sys

ROOT = "/Users/pmains/Code/openclaw/kimi"
MODEL_PATH = f"{ROOT}/models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf"
P2_TABLE = f"{ROOT}/benchmarks/results/phase-02-gguf-header-analysis.json"
OUT_PATH = f"{ROOT}/benchmarks/results/phase-03-addressability.json"

GGUF_PY = f"{ROOT}/llama.cpp/gguf-py"
sys.path.insert(0, GGUF_PY)
from gguf.constants import GGMLQuantizationType, GGML_QUANT_SIZES  # noqa: E402

HEADER_READ_BYTES = 16 * 1024 * 1024

VT = {0: "uint8", 1: "int8", 2: "uint16", 3: "int16", 4: "uint32", 5: "int32",
      6: "float32", 7: "bool", 8: "string", 9: "array", 10: "uint64",
      11: "int64", 12: "float64"}
QT_NAME = {v.value: v.name for v in GGMLQuantizationType}


class Reader:
    def __init__(self, data):
        self.d = data
        self.o = 0

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.o)[0]; self.o += 4
        return v

    def u64(self):
        v = struct.unpack_from("<Q", self.d, self.o)[0]; self.o += 8
        return v

    def string(self):
        n = self.u64()
        v = self.d[self.o:self.o + n].decode("utf-8", "replace")
        self.o += n
        return v

    def value(self, vt):
        if vt == 0:
            v = self.d[self.o]; self.o += 1; return v
        if vt == 1:
            v = struct.unpack_from("<b", self.d, self.o)[0]; self.o += 1; return v
        if vt == 2:
            v = struct.unpack_from("<H", self.d, self.o)[0]; self.o += 2; return v
        if vt == 3:
            v = struct.unpack_from("<h", self.d, self.o)[0]; self.o += 2; return v
        if vt == 4:
            return self.u32()
        if vt == 5:
            v = struct.unpack_from("<i", self.d, self.o)[0]; self.o += 4; return v
        if vt == 6:
            v = struct.unpack_from("<f", self.d, self.o)[0]; self.o += 4; return v
        if vt == 7:
            v = self.d[self.o]; self.o += 1; return bool(v)
        if vt == 8:
            return self.string()
        if vt == 9:
            atype = self.u32()
            n = self.u64()
            return [self.value(atype) for _ in range(n)]
        if vt == 10:
            return self.u64()
        if vt == 11:
            v = struct.unpack_from("<q", self.d, self.o)[0]; self.o += 8; return v
        if vt == 12:
            v = struct.unpack_from("<d", self.d, self.o)[0]; self.o += 8; return v
        raise ValueError(f"unknown value type {vt}")


def parse_header(path):
    with open(path, "rb") as f:
        data = f.read(HEADER_READ_BYTES)
    assert data[:4] == b"GGUF", "not a GGUF file"
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
    return version, tensor_count, fields, tensors


def type_bytes(qtype, elems):
    blk_elems, blk_bytes = GGML_QUANT_SIZES[GGMLQuantizationType(qtype)]
    if blk_elems == 1:
        return elems * blk_bytes
    n_blocks = (elems + blk_elems - 1) // blk_elems
    return n_blocks * blk_bytes


def main():
    version, tensor_count, fields, tensors = parse_header(MODEL_PATH)
    by_name = {t["name"]: t for t in tensors}

    # expert tensors: blk.{layer}.ffn_{gate,up,down}_exps
    expert_tensors = {}
    for name, t in by_name.items():
        parts = name.split(".")
        if len(parts) == 4 and parts[0] == "blk" and parts[2] in (
                "ffn_gate_exps", "ffn_up_exps", "ffn_down_exps"):
            layer = int(parts[1])
            kind = parts[2]
            expert_tensors.setdefault(layer, {})[kind] = t

    assert len(expert_tensors) == 26, f"expected 26 MoE layers, got {len(expert_tensors)}"

    # per-expert byte counts per (layer, kind): the quant mix is NOT uniform
    # across layers (ffn_down_exps is Q4_K on 13 layers, Q6_K on the other 13),
    # so the per-expert stride must be taken from each tensor's own type.
    per_tensor = {}
    total_expert_bytes = 0
    for layer in range(1, 27):
        per_tensor[layer] = {}
        for kind in ("ffn_gate_exps", "ffn_up_exps", "ffn_down_exps"):
            t = expert_tensors[layer][kind]
            n_experts = t["dims"][2]
            per_expert_elems = t["elems"] // n_experts
            per_expert_bytes = type_bytes(t["type_id"], per_expert_elems)
            per_tensor[layer][kind] = {
                "type": t["type"],
                "n_experts": n_experts,
                "per_expert_elems": per_expert_elems,
                "per_expert_bytes": per_expert_bytes,
                "aligned_16": per_expert_bytes % 16 == 0,
            }
            total_expert_bytes += n_experts * per_expert_bytes
    per_expert_avg_bytes = total_expert_bytes / (26 * 256)
    quant_mix = {}
    for layer in range(1, 27):
        q = per_tensor[layer]["ffn_down_exps"]["type"]
        quant_mix.setdefault(q, []).append(layer)

    # cross-check vs Phase 2 table
    p2 = json.load(open(P2_TABLE))
    p2_layers = p2["expert_tensors_by_layer"]
    offsets_match = True
    for layer in range(1, 27):
        for kind in ("ffn_gate_exps", "ffn_up_exps", "ffn_down_exps"):
            t = expert_tensors[layer][kind]
            p2e = p2_layers[str(layer)][kind]
            if t["offset"] != p2e["offset"] or per_tensor[layer][kind]["per_expert_bytes"] != p2e["per_expert_bytes"]:
                offsets_match = False

    # byte-range retrieval: pread vs mmap
    samples = []
    sample_experts = [0, 1, 63, 127, 128, 200, 254, 255]
    sample_layers = [1, 4, 7, 13, 20, 26]
    all_match = True
    with open(MODEL_PATH, "rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            for layer in sample_layers:
                for kind in ("ffn_gate_exps", "ffn_up_exps", "ffn_down_exps"):
                    t = expert_tensors[layer][kind]
                    for eid in sample_experts:
                        off = t["offset"] + eid * per_tensor[layer][kind]["per_expert_bytes"]
                        n = per_tensor[layer][kind]["per_expert_bytes"]
                        # pread: independent byte-range read (does not touch the
                        # rest of the file)
                        os.lseek(f.fileno(), off, os.SEEK_SET)
                        pread_data = os.read(f.fileno(), n)
                        assert len(pread_data) == n, f"short pread at {off}"
                        mmap_data = mm[off:off + n]
                        ok = pread_data == mmap_data
                        all_match = all_match and ok
                        samples.append({
                            "layer": layer, "kind": kind, "expert_id": eid,
                            "offset": off, "bytes": n,
                            "match": ok,
                            "first_bytes": pread_data[:16].hex(),
                        })

    result = {
        "artifact": MODEL_PATH,
        "header": {
            "version": version,
            "tensors": tensor_count,
            "n_moe_layers": len(expert_tensors),
        },
        "per_expert_avg_bytes": per_expert_avg_bytes,
        "down_exps_quant_mix": quant_mix,
        "per_tensor": per_tensor,
        "offsets_match_phase2": offsets_match,
        "n_byte_range_checks": len(samples),
        "all_byte_range_checks_match": all_match,
        "samples": samples,
    }
    with open(OUT_PATH, "w") as fp:
        json.dump(result, fp, indent=2)

    print("=== Phase 3 expert addressability ===")
    print(f"artifact: {MODEL_PATH}")
    print(f"header: GGUF v{version}, {tensor_count} tensors, {len(expert_tensors)} MoE layers")
    print("per-expert slice (expert dim last, contiguous, per-layer stride):")
    for layer in [1, 4, 26]:
        for kind in ("ffn_gate_exps", "ffn_up_exps", "ffn_down_exps"):
            info = per_tensor[layer][kind]
            print(f"  l{layer:>2} {kind:>15}: {info['type']:>5}  {info['per_expert_bytes']:>9} B/expert  "
                  f"aligned16={info['aligned_16']}")
    print(f"down_exps quant mix: { {k: len(v) for k, v in quant_mix.items()} } layers "
          f"({ {k: v[:6] for k, v in quant_mix.items()} }...)")
    print(f"average expert bytes (all layers): {per_expert_avg_bytes:.0f} B = {per_expert_avg_bytes/1e6:.3f} MB")
    print(f"offsets match Phase 2 table: {offsets_match}")
    print(f"byte-range checks (pread vs mmap): {len(samples)}, all match: {all_match}")
    if not all_match:
        bad = [s for s in samples if not s["match"]]
        print("MISMATCHES:", bad[:3])


if __name__ == "__main__":
    main()
