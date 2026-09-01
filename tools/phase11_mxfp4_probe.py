#!/usr/bin/env python3
"""Phase 11 — first MXFP4 expert mul_mat arithmetic isolation probe.

Reconstructs the FIRST streamed MXFP4 expert mul_mat (layer 1
blk.1.ffn_up_exps.weight, mul_mat_id, first prefill step, token-0 slab,
slot-0 column = the top-1 router-selected expert's up projection) from
identical retained inputs:

  - expert bytes: GGUF slice at tensor_abs_off + expert_id*per_expert_bytes
    (per_expert = 2304*1024/32*17 = 1,253,376 B; the SAME addressing the
    streamed path preads), dequantized with e8m0 scale + kvalues_mxfp4;
  - activation: captured ffn_normed (il=1, exec 2, token-0 column);

in five arithmetic paths:

  f64ref  : f64 reference (near-exact ground truth)
  f32seq  : f32 sequential dequant(w)*x accumulation
  half_w  : dequantized weights converted to fp16, f32 accumulate
  half_wa : weights AND activations to fp16, f32 accumulate
  cpu_q8  : exact replica of ggml_vec_dot_mxfp4_q8_0 (CPU quantizes the
            activation to q8_0 per 32-block, int32 kvalues dot, per-block
            scale product) — the documented CPU path

and compares each against the ACTUAL production captures (moe_up CPU and
Metal from the retained localize traces), plus per-block localization of
the first CPU-vs-Metal divergence.

Usage:
  phase11_mxfp4_probe.py <gguf> <cpu_act.bin> <metal_act.bin>
"""
import struct, sys
import numpy as np

GGUF = sys.argv[1] if len(sys.argv) > 1 else 'models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf'
CPU_ACT = sys.argv[2] if len(sys.argv) > 2 else 'benchmarks/results/phase-k1/localize/cpu/act.bin'
MET_ACT = sys.argv[3] if len(sys.argv) > 3 else 'benchmarks/results/phase-k1/localize/metal/act.bin'

# blk.1.ffn_up_exps.weight: mxfp4 [ne0=2304 (K), ne1=1024 (n_ff), ne2=256 (n_expert)]
UP_ABS_OFF = 1608189216
K = 2304
N_FF = 1024
QK = 32              # mxfp4 elements per block
BLK_BYTES = 17       # 1 e8m0 scale byte + 16 nibble bytes
BPR = K // QK        # 72 blocks per output row
PER_EXP_BYTES = K * N_FF // QK * BLK_BYTES   # 1,253,376

# CPU kvalues_mxfp4 (int8); Metal kvalues_mxfp4_f are half of these with the
# e8m0 scale doubled — identical products (dequant probe verified bit-identical).
KV = np.array([0,1,2,3,4,6,8,12, 0,-1,-2,-3,-4,-6,-8,-12], dtype=np.int32)

def e8m0_to_fp32_half(x):
    if x < 2:
        bits = 0x00200000 << x
    else:
        bits = (x - 1) << 23
    return np.float32(struct.unpack('<f', struct.pack('<I', bits))[0])

def e8m0_to_fp32(x):
    bits = 0x00400000 if x == 0 else (x << 23)
    return np.float32(struct.unpack('<f', struct.pack('<I', bits))[0])

def read_tcat(path):
    recs = []
    with open(path, 'rb') as f:
        while True:
            hdr = f.read(4)
            if len(hdr) < 4: break
            magic, = struct.unpack('<I', hdr)
            if magic != 0x54434154: break
            exec_id, = struct.unpack('<I', f.read(4))
            il, = struct.unpack('<i', f.read(4))
            n_embd, = struct.unpack('<I', f.read(4))
            n_tokens, = struct.unpack('<I', f.read(4))
            start_pos, = struct.unpack('<I', f.read(4))
            phase, = struct.unpack('<I', f.read(4))
            name_len, = struct.unpack('<I', f.read(4))
            name = f.read(name_len).decode('utf-8', 'replace')
            data = np.array(struct.unpack(f'<{n_embd}f', f.read(4*n_embd)), dtype=np.float32)
            recs.append((exec_id, il, n_embd, n_tokens, start_pos, phase, name, data))
    return recs

def pick(recs, il, name, exec_id=2):
    cand = [r for r in recs if r[1] == il and r[6] == name and r[0] == exec_id]
    return cand[0] if cand else None

def dequant_expert(fd, abs_off):
    """Dequantize the expert slice -> W [N_FF, K] f32 (row-major blocks).
    mxfp4 nibble layout (dequantize_row_mxfp4, ggml-quants.c): byte j low
    nibble = element j, high nibble = element j+16 (j=0..15)."""
    fd.seek(abs_off)
    raw = fd.read(PER_EXP_BYTES)
    W = np.zeros((N_FF, K), dtype=np.float32)
    for b in range(N_FF * BPR):
        blk = raw[b*BLK_BYTES:(b+1)*BLK_BYTES]
        d = e8m0_to_fp32_half(blk[0])     # CPU semantics == Metal semantics (verified)
        qs = blk[1:]
        r = b // BPR
        c = (b % BPR) * QK
        for j in range(16):
            W[r, c + j]      = np.float32(KV[qs[j] & 0x0F]) * d
            W[r, c + j + 16] = np.float32(KV[qs[j] >> 4])    * d
    return W

# ---------- arithmetic paths ----------
def f64_ref(W, x):
    return W.astype(np.float64).dot(x.astype(np.float64))

def f32_seq(W, x):
    out = np.zeros(N_FF, dtype=np.float32)
    for r in range(N_FF):
        acc = np.float32(0.0)
        for kk in range(K):
            acc = np.float32(acc + np.float32(W[r, kk]) * np.float32(x[kk]))
        out[r] = acc
    return out

def half_ops(W, x, half_w, half_a):
    Wc = W.astype(np.float16).astype(np.float32) if half_w else W
    xc = x.astype(np.float16).astype(np.float32) if half_a else x
    return f32_seq(Wc, xc)

def cpu_q8_vec_dot(raw, x):
    """Exact replica of ggml_vec_dot_mxfp4_q8_0: q8_0-quantize activation
    per 32-block (d=amax/127 stored fp16, round-half-up), int32 kvalues dot
    per block (byte j low nibble = weight elem j, high = elem j+16),
    scale = fp16(q8.d) * e8m0_half, f32 accumulate over blocks."""
    out = np.zeros(N_FF, dtype=np.float32)
    for r in range(N_FF):
        sumf = np.float32(0.0)
        for b in range(BPR):
            xb = x[b*QK:(b+1)*QK]
            amax = np.float32(np.max(np.abs(xb)))
            d_q8 = np.float32(amax / np.float32(127.0)) if amax > 0 else np.float32(0.0)
            # quantize_row_q8_0 stores d as fp16; vec_dot reads it back
            d_q8 = np.float32(np.float16(d_q8))
            qs = np.zeros(QK, dtype=np.int32)
            for j in range(QK):
                v = np.float32(xb[j]) / d_q8 if d_q8 > 0 else np.float32(0.0)
                qs[j] = int(np.floor(v + np.float32(0.5)))
            blk = raw[(r*BPR + b)*BLK_BYTES:(r*BPR + b+1)*BLK_BYTES]
            d_w = e8m0_to_fp32_half(blk[0])
            sumi1 = 0; sumi2 = 0
            for j in range(16):
                sumi1 += qs[j]      * KV[blk[1+j] & 0x0F]
                sumi2 += qs[j + 16] * KV[blk[1+j] >> 4]
            sumf = np.float32(sumf + np.float32(d_q8 * d_w * np.float32(sumi1 + sumi2)))
        out[r] = sumf
    return out

def main():
    cpu = read_tcat(CPU_ACT); metal = read_tcat(MET_ACT)
    c_in = pick(cpu, 1, 'ffn_normed'); m_in = pick(metal, 1, 'ffn_normed')
    c_rl = pick(cpu, 1, 'router_logits'); m_rl = pick(metal, 1, 'router_logits')
    c_up = pick(cpu, 1, 'moe_up'); m_up = pick(metal, 1, 'moe_up')
    for tag, r in (('cpu ffn_normed', c_in), ('metal ffn_normed', m_in),
                   ('cpu router_logits', c_rl), ('metal router_logits', m_rl),
                   ('cpu moe_up', c_up), ('metal moe_up', m_up)):
        print(f"record {tag}: exec={r[0]} il={r[1]} n_embd={r[2]} n_tokens={r[3]} pos={r[4]} ph={r[5]}")

    # router: slot-0 = argmax of logits (softmax is monotonic; argsort_top_k
    # sorts by probability, so slot-0 = highest logit)
    top8_c = np.argsort(c_rl[7])[::-1][:8]
    top8_m = np.argsort(m_rl[7])[::-1][:8]
    print(f"router top-8 cpu : {top8_c.tolist()}")
    print(f"router top-8 metal: {top8_m.tolist()}")
    assert np.array_equal(top8_c, top8_m), "CPU/Metal router disagree at il=1!"
    e0 = int(top8_c[0])
    print(f"slot-0 expert id = {e0}")

    # input bit-identity
    bit = np.array_equal(c_in[7].view(np.uint32), m_in[7].view(np.uint32))
    print(f"ffn_normed bit-identical: {bit}  max|d|={np.abs(c_in[7]-m_in[7]).max():.3e}")

    prod_cpu = c_up[7]; prod_metal = m_up[7]
    pd = np.abs(prod_cpu - prod_metal)
    print(f"PRODUCTION cpu vs metal moe_up: max|d|={pd.max():.6e} "
          f"mean|d|={pd.mean():.6e} rel@max={pd.max()/max(np.abs(prod_cpu).max(), np.abs(prod_metal).max(), 1e-30):.6e}")

    # load expert bytes at the router-selected slice
    off = UP_ABS_OFF + e0 * PER_EXP_BYTES
    fd = open(GGUF, 'rb')
    fd.seek(off); rawb = fd.read(PER_EXP_BYTES)
    fd.close()
    print(f"\nexpert slice abs_off={off} bytes={len(rawb)} (expect {PER_EXP_BYTES})")
    print(f"first block: e8m0={rawb[0]} nibbles={rawb[1:5].hex()}")
    W = np.zeros((N_FF, K), dtype=np.float32)
    for b in range(N_FF * BPR):
        blk = rawb[b*BLK_BYTES:(b+1)*BLK_BYTES]
        d = e8m0_to_fp32_half(blk[0])
        qs = blk[1:]
        r = b // BPR; c = (b % BPR) * QK
        for j in range(16):
            W[r, c + j]      = np.float32(KV[qs[j] & 0x0F]) * d
            W[r, c + j + 16] = np.float32(KV[qs[j] >> 4])    * d
    print(f"W dequantized: max|w|={np.abs(W).max():.6e} mean|w|={np.abs(W).mean():.6e}")

    x_c = c_in[7].astype(np.float32); x_m = m_in[7].astype(np.float32)

    # CPU path: exact vec_dot_mxfp4_q8_0 replica (uses raw nibbles)
    cpuq_c = cpu_q8_vec_dot(rawb, x_c)
    cpuq_m = cpu_q8_vec_dot(rawb, x_m)
    # Metal path reconstructions (from each path's own input)
    ref_c  = f64_ref(W, x_c);   ref_m  = f64_ref(W, x_m)
    f32_c  = f32_seq(W, x_c);   f32_m  = f32_seq(W, x_m)
    hw_c   = half_ops(W, x_c, True, False);  hw_m = half_ops(W, x_m, True, False)
    hwa_c  = half_ops(W, x_c, True, True);   hwa_m = half_ops(W, x_m, True, True)

    def cmp(tag, a, b):
        d = np.abs(a - b)
        i = int(np.argmax(d))
        denom = max(abs(a[i]), abs(b[i]), 1e-30)
        print(f"  {tag:40s} max|d|={d.max():.6e} mean|d|={d.mean():.6e} rel@max={d.max()/denom:.6e} worst=({a[i]:.6e},{b[i]:.6e})")

    print("\n=== CPU path (input: cpu ffn_normed) ===")
    cmp("prod_cpu vs f64ref", prod_cpu, ref_c)
    cmp("prod_cpu vs cpu_q8 replica", prod_cpu, cpuq_c)
    cmp("f64ref vs cpu_q8 replica", ref_c, cpuq_c)
    print("\n=== Metal path (input: metal ffn_normed) ===")
    cmp("prod_metal vs f64ref", prod_metal, ref_m)
    cmp("prod_metal vs f32seq", prod_metal, f32_m)
    cmp("prod_metal vs half_weights", prod_metal, hw_m)
    cmp("prod_metal vs half_w+a", prod_metal, hwa_m)
    cmp("f64ref vs f32seq", ref_m, f32_m)
    cmp("f64ref vs half_weights", ref_m, hw_m)
    cmp("f64ref vs half_w+a", ref_m, hwa_m)
    print("\n=== cross-path (common input = cpu ffn_normed; isolates op arithmetic) ===")
    cmp("cpu_q8 vs f32seq", cpuq_c, f32_c)
    cmp("cpu_q8 vs half_w+a", cpuq_c, hwa_c)
    cmp("f32seq vs half_w+a", f32_c, hwa_c)

    print("\n=== where CPU and Metal first diverge (per-block, cpu input) ===")
    # block contributions: cpu (q8_0 quantized act) vs metal (f32 act x dequant w)
    maxd = 0.0; worst = None
    for b in range(BPR):
        blk = rawb[b*BLK_BYTES:(b+1)*BLK_BYTES]
        d_w = e8m0_to_fp32_half(blk[0])
        xb = x_c[b*QK:(b+1)*QK]
        amax = np.float32(np.max(np.abs(xb)))
        d_q8 = np.float32(amax / np.float32(127.0)) if amax > 0 else np.float32(0.0)
        qs = np.zeros(QK, dtype=np.int32)
        for j in range(QK):
            v = np.float32(xb[j]) / d_q8 if d_q8 > 0 else np.float32(0.0)
            qs[j] = int(np.floor(v + np.float32(0.5)))
        s1 = 0; s2 = 0
        for j in range(16):
            s1 += qs[j]      * KV[blk[1+j] & 0x0F]
            s2 += qs[j + 16] * KV[blk[1+j] >> 4]
        cpu_blk = np.float32(d_w * np.float32(d_q8 * np.float32(s1 + s2)))
        met_blk = np.float32(0.0)
        for j in range(16):
            met_blk = np.float32(met_blk + np.float32(KV[blk[1+j] & 0x0F] * d_w) * xb[j])
            met_blk = np.float32(met_blk + np.float32(KV[blk[1+j] >> 4] * d_w)   * xb[j+16])
        dd = abs(cpu_blk - met_blk)
        if dd > maxd:
            maxd = dd; worst = (b, cpu_blk, met_blk)
    print(f"worst per-block (row 0) |d| = {maxd:.6e} at block {worst[0]} (K={worst[0]*32}) "
          f"cpu={worst[1]:+.6e} metal={worst[2]:+.6e}")

    print("\n=== classification ===")
    d_mf = np.abs(prod_metal - f32_m)
    d_mh = np.abs(prod_metal - hwa_m)
    d_cq = np.abs(prod_cpu - cpuq_c)
    print(f"  prod_metal vs f32seq    max|d|={d_mf.max():.3e}  rel={d_mf.max()/max(np.abs(prod_metal).max(),1e-30):.3e}")
    print(f"  prod_metal vs half_w+a  max|d|={d_mh.max():.3e}  rel={d_mh.max()/max(np.abs(prod_metal).max(),1e-30):.3e}")
    print(f"  prod_cpu  vs cpu_q8     max|d|={d_cq.max():.3e}  rel={d_cq.max()/max(np.abs(prod_cpu).max(),1e-30):.3e}")
    metal_ok = d_mf.max() < 1e-3 or d_mh.max() < 1e-3
    cpu_ok = d_cq.max() < 1e-3
    print(f"  => Metal reproduced by expected arithmetic: {metal_ok}; CPU reproduced: {cpu_ok}")

if __name__ == '__main__':
    main()
