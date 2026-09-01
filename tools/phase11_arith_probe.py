#!/usr/bin/env python3
"""Phase 11 — mul_mat arithmetic isolation probe (host-side, exact f32).

Reconstructs the layer-0 Q projection (q8_0) dot products on the host from
IDENTICAL inputs (raw q8_0 weights from the GGUF + the bit-identical
attn_norm activation vector from the retained localize traces), in three
arithmetic paths:

  f64ref  : f64 reference (near-exact ground truth)
  f32seq  : f32 sequential dequant(w)*x accumulation (CPU-style)
  metal   : exact replica of the production Metal kernel
            kernel_mul_mv_ext_q8_0_f32_r1_2 (nxpsg=16, chpt=4, per-thread
            f32 sumf over residue classes, simd_shuffle_down tree)

and compares each against the ACTUAL production outputs captured in the
traces (kda_Q_proj CPU and Metal). Reports, per output row:
  - final output (cpu_actual, metal_actual, f64ref, f32seq, metal_replica)
  - per-block contributions (72 q8_0 blocks of 32 elements) for f64ref,
    f32seq, and the Metal replica, plus block-level max|d| vs f64ref
  - cumulative partial sums after K = 64, 256, 1024, 2304
  - max|d| / mean|d| / rel@max / RMS / NaN-Inf for cpu-vs-metal,
    metal-replica-vs-metal-actual (validates the replica = Case C check),
    f32seq-vs-cpu-actual, and metal-replica block contributions vs f64

Usage:
  phase11_arith_probe.py <gguf> <attn_q_offset> <cpu_act.bin> <metal_act.bin> \
      [rows...]   (default rows 0..7)

All arithmetic is done with explicit numpy float32 scalars so rounding
matches the kernels' f32 accumulation (no numpy vectorized promotion).
"""
import struct, sys
import numpy as np

GGUF = sys.argv[1] if len(sys.argv) > 1 else 'models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf'
# Absolute data offset of blk.0.attn_q.weight in the MXFP4 GGUF, derived
# authoritatively via gguf_get_data_offset + gguf_get_tensor_offset
# (tools/phase11_repack_dump.cpp): padded data section 6,949,024 + GGUF
# relative offset 822,233,088 = 829,182,112. The GGUF offset field is
# RELATIVE to the (aligned) data section start — using it as an absolute
# file offset (the earlier bug) lands ~6.9 MB early and reads garbage
# (which happened to parse as q8_0 with NaN scales).
W_OFF = int(sys.argv[2]) if len(sys.argv) > 2 else 829182112
CPU_ACT = sys.argv[3] if len(sys.argv) > 3 else '/tmp/l0_cpu_attn_norm.npy'
MET_ACT = sys.argv[4] if len(sys.argv) > 4 else '/tmp/l0_metal_attn_norm.npy'
CPU_OUT = '/tmp/l0_cpu_kda_Q_proj.npy'
MET_OUT = '/tmp/l0_metal_kda_Q_proj.npy'
ROWS = [int(x) for x in sys.argv[5:]] or list(range(8))

K = 2304          # n_embd (Q projection K dimension)
BLK = 32          # q8_0 elements per block
BPR = K // BLK    # 72 blocks per row
BLK_BYTES = 34    # q8_0 block: 2-byte half scale + 32 int8
ROW_BYTES = BPR * BLK_BYTES

def fp16_to_fp32(h):
    s = (h & 0x8000) << 16
    e = (h >> 10) & 0x1F
    m = h & 0x03FF
    if e == 0:
        if m == 0:
            return np.float32(struct.unpack('<f', struct.pack('<I', s))[0])
        # subnormal: renormalize (ggml ggml_fp16_to_fp32 semantics)
        e2 = 127 - 15 + 1          # 113; NOT 111 (the old bug: e2+15+1 double-offset → 2^14 too large)
        while not (m & 0x0400):
            m <<= 1; e2 -= 1
        m &= 0x03FF
        return np.float32(struct.unpack('<f', struct.pack('<I', s | (e2 << 23) | (m << 13)))[0])
    if e == 0x1F:
        return np.float32(struct.unpack('<f', struct.pack('<I', s | 0x7F800000 | (m << 13)))[0])
    return np.float32(struct.unpack('<f', struct.pack('<I', s | ((e + 127 - 15) << 23) | (m << 13)))[0])

def load_rows():
    """Return {row: np.float32[2304] dequantized weights}."""
    fd = open(GGUF, 'rb')
    out = {}
    for r in ROWS:
        fd.seek(W_OFF + r * ROW_BYTES)
        raw = fd.read(ROW_BYTES)
        w = np.zeros(K, dtype=np.float32)
        nan_blocks = []
        for b in range(BPR):
            blk = raw[b*BLK_BYTES:(b+1)*BLK_BYTES]
            d16 = struct.unpack('<H', blk[:2])[0]
            d = fp16_to_fp32(d16)
            if d != d:  # NaN scale: wrong offset or corrupt read
                nan_blocks.append(b)
            qs = struct.unpack('<32b', blk[2:])
            for j in range(BLK):
                w[b*BLK + j] = np.float32(qs[j]) * d
        if nan_blocks:
            print(f"WARNING: row {r} has NaN-scale q8_0 blocks {nan_blocks} — "
                  f"W_OFF={W_OFF} is likely NOT the tensor data start; "
                  f"results for this row are meaningless")
        out[r] = w
    fd.close()
    return out

def load_act(path):
    return np.load(path).astype(np.float32)

def f64_ref(w, x):
    return np.float64(w).dot(np.float64(x))

def f32_seq(w, x):
    acc = np.float32(0.0)
    for k in range(K):
        acc = np.float32(acc + np.float32(w[k]) * np.float32(x[k]))
    return acc

def f64_blocks(w, x):
    """Per-block contributions (32-elem) in f64 (near-exact)."""
    out = np.zeros(BPR, dtype=np.float64)
    for b in range(BPR):
        s = np.float64(0.0)
        for j in range(BLK):
            s = s + np.float64(np.float32(w[b*BLK+j]) * np.float32(x[b*BLK+j]))
        out[b] = s
    return out

# ---- CPU vec_dot variant: quantize activations to q8_0, then q8_0 x q8_0 dot ----
# replicates quantize_row_q8_0 + ggml_vec_dot_q8_0_q8_0 (f32 accumulate)
def cpu_q8_blocks(w, x):
    """Per-32-elem-block contributions of the CPU vec_dot path: quantize the
    activation block to q8_0 (d_x, qs_x), then dot dequantized w with
    dequantized x, f32-accumulated WITHIN the block (block sums are kept
    separate so cumulative behavior at K=64/256/1024/2304 can be compared)."""
    out = np.zeros(BPR, dtype=np.float64)
    for b in range(BPR):
        xb = x[b*BLK:(b+1)*BLK]
        amax = np.float32(np.max(np.abs(xb)))
        d_x = np.float32(amax / np.float32(127.0)) if amax > np.float32(0.0) else np.float32(0.0)
        qs_x = np.zeros(BLK, dtype=np.int32)
        for j in range(BLK):
            v = np.float32(xb[j]) / d_x if d_x > np.float32(0.0) else np.float32(0.0)
            qs_x[j] = int(np.floor(v + np.float32(0.5)))  # round-half-up like ggml
        acc = np.float32(0.0)
        for j in range(BLK):
            acc = np.float32(acc + np.float32(w[b*BLK+j]) * np.float32(np.float32(qs_x[j]) * d_x))
        out[b] = np.float64(acc)
    return out

def cpu_vec_dot_q8_0xq8_0(w, x):
    acc = np.float32(0.0)
    for b in range(BPR):
        # quantize x block: d_x = max(abs)/127, qs = round(x/d_x)
        xb = x[b*BLK:(b+1)*BLK]
        amax = np.float32(np.max(np.abs(xb)))
        d_x = np.float32(amax / np.float32(127.0)) if amax > np.float32(0.0) else np.float32(0.0)
        qs_x = np.zeros(BLK, dtype=np.int32)
        for j in range(BLK):
            v = np.float32(xb[j]) / d_x if d_x > np.float32(0.0) else np.float32(0.0)
            qs_x[j] = int(np.floor(v + np.float32(0.5)))  # round-half-up like ggml
        # dequant x block (f32) and dot with dequantized w block
        for j in range(BLK):
            acc = np.float32(acc + np.float32(w[b*BLK+j]) * np.float32(np.float32(qs_x[j]) * d_x))
    return acc

def metal_replica(w, x):
    """Exact replica of kernel_mul_mv_ext_q8_0_f32_impl with the parameters
    used for this op: nxpsg=16, chpt=4, chpb=8 (q8_0: epb=32), r1ptg=2.
    Returns (final, per_thread_partials[16], per_block_contribs[72]).
    The per-block contribution is computed by grouping the per-chunk dots
    back to their 32-element q8_0 block (chunk c belongs to block c//8)."""
    nxpsg, chpt, chpb = 16, 4, 8
    # thread tx handles float4 chunks {tx + 16*m} (residue class mod 16)
    # chunk index c (float4) -> element 4*c ; block = (4*c)//32 = c//8
    block_acc = np.zeros(BPR, dtype=np.float64)
    partials = [np.float32(0.0) for _ in range(nxpsg)]
    for tx in range(nxpsg):
        acc = np.float32(0.0)
        m = 0
        while True:
            c = tx + chpt*nxpsg*m          # first chunk of this iteration
            if 4*c >= K: break
            for ch in range(chpt):
                cch = c + ch*nxpsg          # float4 chunk index
                if 4*cch >= K: break
                # dot(float4, float4) in f32, left-to-right, no fma
                d = np.float32(0.0)
                for i in range(4):
                    d = np.float32(d + np.float32(w[4*cch+i]) * np.float32(x[4*cch+i]))
                acc = np.float32(acc + d)
                block_acc[cch // 8] += np.float64(d)
            m += 1
        partials[tx] = acc
    # simd_shuffle_down tree for nxpsg=16: shuffles 8,4,2,1
    lanes = [np.float32(p) for p in partials]
    for delta in (8, 4, 2, 1):
        for i in range(nxpsg):
            if i + delta < nxpsg:
                lanes[i] = np.float32(lanes[i] + lanes[i + delta])
    return lanes[0], partials, block_acc

def stats(name, a, b):
    a = np.asarray(a, dtype=np.float64); b = np.asarray(b, dtype=np.float64)
    n = a.size
    d = np.abs(a - b)
    nan = int(np.isnan(a).sum() + np.isnan(b).sum())
    inf = int(np.isinf(a).sum() + np.isinf(b).sum())
    valid = ~(np.isnan(a) | np.isnan(b))
    dv = d[valid]
    maxd = float(dv.max()) if dv.size else float('nan')
    meand = float(dv.mean()) if dv.size else float('nan')
    rms = float(np.sqrt((dv**2).mean())) if dv.size else float('nan')
    denom = np.maximum(np.abs(a[valid]), np.abs(b[valid]))
    rel = float((dv / np.maximum(denom, 1e-30)).max()) if dv.size else float('nan')
    print(f"    {name:34s} max|d|={maxd:.3e} mean|d|={meand:.3e} rel@max={rel:.3e} RMS={rms:.3e} NaN={nan} Inf={inf}")

def main():
    w = load_rows()
    x_cpu = load_act(CPU_ACT)
    x_met = load_act(MET_ACT)
    assert np.array_equal(x_cpu.view(np.uint32), x_met.view(np.uint32)), "activations differ!"
    y_cpu = np.load(CPU_OUT).astype(np.float32)
    y_met = np.load(MET_OUT).astype(np.float32)
    print(f"rows={ROWS} K={K} blocks/row={BPR}  (activations bit-identical: yes)")
    for r in ROWS:
        wr = w[r]
        x = x_cpu
        ref = f64_ref(wr, x)                       # f64 ground truth
        s32 = f32_seq(wr, x)                       # f32 sequential
        mfin, mpart, mblocks = metal_replica(wr, x)
        cpu_q8 = cpu_vec_dot_q8_0xq8_0(wr, x)   # CPU vec_dot path (activations quantized)
        cpu_a = np.float64(y_cpu[r]); met_a = np.float64(y_met[r])
        print(f"\n== row {r} ==")
        print(f"  actuals: cpu={cpu_a:.9e} metal={met_a:.9e}  |cpu-metal|={abs(cpu_a-met_a):.3e} rel={(abs(cpu_a-met_a)/max(abs(cpu_a),1e-30)):.3e}")
        print(f"  f64ref ={ref:.9e}  f32seq={s32:.9e}  cpu_q8vec={cpu_q8:.9e}  metal_replica={mfin:.9e}")
        print(f"  f64ref vs cpu_actual  : d={abs(ref-cpu_a):.3e} rel={(abs(ref-cpu_a)/max(abs(ref),1e-30)):.3e}")
        print(f"  f64ref vs metal_actual: d={abs(ref-met_a):.3e} rel={(abs(ref-met_a)/max(abs(ref),1e-30)):.3e}")
        print(f"  f32seq vs cpu_actual  : d={abs(s32-cpu_a):.3e} rel={(abs(s32-cpu_a)/max(abs(s32),1e-30)):.3e}")
        print(f"  cpu_q8vec vs cpu_actual: d={abs(cpu_q8-cpu_a):.3e} rel={(abs(cpu_q8-cpu_a)/max(abs(cpu_q8),1e-30)):.3e}")
        print(f"  metal_replica vs metal_actual: d={abs(mfin-met_a):.3e} rel={(abs(mfin-met_a)/max(abs(mfin),1e-30)):.3e}  [Case C validation]")
        # cumulative partial sums at K=64,256,1024,2304 (in units of 32-elem blocks)
        print("  cumulative sums (blocks: 2,8,32,72 => K: 64,256,1024,2304):")
        f64b = f64_blocks(wr, x)
        cum_f64 = np.cumsum(f64b)
        cum_met = np.cumsum(mblocks)
        for nb in (2, 8, 32, 72):
            print(f"    K={nb*32:5d}  f64blocks={cum_f64[nb-1]:.9e}  metal_replica={cum_met[nb-1]:.9e}  |d|={abs(cum_f64[nb-1]-cum_met[nb-1]):.3e}")
        # per-block: compare f64 block contribs vs metal replica block contribs
        stats("blocks: f64 vs metal_replica", mblocks, f64b)
        dev = np.abs(mblocks - f64b)
        worst = np.argsort(dev)[::-1][:3]
        print(f"    worst block deviations (f64 vs metal replica): " +
              ", ".join(f"b={b} d={dev[b]:.3e} (f64={f64b[b]:+.6e} metal={mblocks[b]:+.6e})" for b in worst))

        # ---- CPU-path vs Metal-path block/cumulative comparison (classification) ----
        cpu_b = cpu_q8_blocks(wr, x)          # CPU vec_dot path per-block (q8-quantized activations)
        cum_cpu = np.cumsum(cpu_b)
        print("  CPU path (q8-quantized act) vs Metal path (f32 act) — per block:")
        stats("blocks: CPU path vs Metal replica", cpu_b, mblocks)
        dblk = np.abs(cpu_b - mblocks)
        worst2 = np.argsort(dblk)[::-1][:3]
        print(f"    worst block deviations (CPU path vs Metal): " +
              ", ".join(f"b={b} d={dblk[b]:.3e} (cpu={cpu_b[b]:+.6e} metal={mblocks[b]:+.6e})" for b in worst2))
        print("  cumulative sums — CPU path vs Metal path (blocks: 2,8,32,72 => K: 64,256,1024,2304):")
        for nb in (2, 8, 32, 72):
            print(f"    K={nb*32:5d}  cpu_path={cum_cpu[nb-1]:.9e}  metal_replica={cum_met[nb-1]:.9e}  |d|={abs(cum_cpu[nb-1]-cum_met[nb-1]):.3e}")
        stats("cumul K=64: cpu_path vs metal", cum_cpu[1:2], cum_met[1:2])
        stats("cumul K=256: cpu_path vs metal", cum_cpu[7:8], cum_met[7:8])
        stats("cumul K=1024: cpu_path vs metal", cum_cpu[31:32], cum_met[31:32])
        stats("cumul K=2304: cpu_path vs metal", cum_cpu[71:72], cum_met[71:72])

if __name__ == '__main__':
    main()
