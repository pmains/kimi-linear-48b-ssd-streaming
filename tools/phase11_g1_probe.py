#!/usr/bin/env python3
"""Phase 11 — kda_g1 (delta-net decay gate) arithmetic isolation probe.

Decomposes the layer-0 kda_g1 computation into its production operations:

    f_a     = mul_mat(ssm_f_a, cur)          # q8_0 [2304,128] x f32 [2304,1]
    f_b_out = mul_mat(ssm_f_b, f_a)          # q8_0 [128,4096] x f32 [128,1]
    z       = f_b_out + ssm_dt.bias          # f32 [4096]
    s       = softplus(z)                    # f32 [4096]
    g1      = s * A[head]                    # A = ssm_a = -exp(A_log), per head

and reconstructs each stage in three arithmetic paths from IDENTICAL
retained inputs (raw q8_0 weights from the GGUF + the bit-identical
attn_norm activation from the retained localize traces):

  f64ref : f64 reference (near-exact ground truth)
  cpu    : CPU vec_dot path replica — activations quantized to q8_0 per
           32-element block (d = amax/127, round-half-up), f32 within-block
           accumulation (the mechanism the arith probe validated for Q/K/V)
  metal  : production Metal kernel replica — kernel_mul_mv_ext_q8_0_f32
           family (nxpsg=16, chpt=4, chpb=8), f32 activations x dequantized
           weights, residue-class accumulation + simd_shuffle_down tree

Compares each stage (f_a, f_b_out, z, s, g1) between paths and against the
ACTUAL production captures (kda_g1 CPU and Metal from the localize
traces), reporting value magnitudes so the 1.438 max|d| can be judged in
context (large-value small-relative vs small-value large-relative).

Usage:
  phase11_g1_probe.py <gguf> <cpu_act.bin> <metal_act.bin>
"""
import struct, sys
import numpy as np

GGUF = sys.argv[1] if len(sys.argv) > 1 else 'models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf'
CPU_ACT = sys.argv[2] if len(sys.argv) > 2 else 'benchmarks/results/phase-k1/localize/cpu/act.bin'
MET_ACT = sys.argv[3] if len(sys.argv) > 3 else 'benchmarks/results/phase-k1/localize/metal/act.bin'

DATA_BASE = 6949024          # padded data-section start (established via gguf_get_data_offset)
FA_OFF = 917219104           # blk.0.ssm_f_a.weight absolute (repack dump verified)
FB_OFF = 917532448           # blk.0.ssm_f_b.weight absolute
BLK = 32                     # q8_0 elements per block
BLK_BYTES = 34               # 2-byte half scale + 32 int8
N_EMBD = 2304
HEAD_DIM = 128
N_HEAD = 32
N_OUT = HEAD_DIM * N_HEAD    # 4096

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

# ---------- GGUF metadata parse (tensor info section) ----------
def gguf_tensor_infos(path):
    """Return {name: (type_code, dims, rel_offset)} + data_base."""
    with open(path, 'rb') as f:
        magic, = struct.unpack('<I', f.read(4))
        assert magic == 0x46554747, f"not a GGUF (magic {magic:#x})"
        version, = struct.unpack('<I', f.read(4))
        n_tensors, = struct.unpack('<Q', f.read(8))
        n_kv, = struct.unpack('<Q', f.read(8))
        for _ in range(n_kv):
            klen, = struct.unpack('<Q', f.read(8))
            f.read(klen)
            vtype, = struct.unpack('<I', f.read(4))
            if vtype == 8:      # string
                slen, = struct.unpack('<Q', f.read(8)); f.read(slen)
            elif vtype == 9:    # array
                etype, = struct.unpack('<I', f.read(4))
                cnt, = struct.unpack('<Q', f.read(8))
                for _ in range(cnt):
                    if etype == 8:
                        slen, = struct.unpack('<Q', f.read(8)); f.read(slen)
                    elif etype in (0, 1, 2, 3, 4, 5, 7):
                        f.read(1 if etype in (0, 1, 7) else 2 if etype in (2, 3) else 4)
                    elif etype in (6, 10, 11):
                        f.read(4)
                    elif etype == 12:
                        f.read(8)
                    else:
                        raise ValueError(f"unsupported array elem type {etype}")
            else:
                sizes = {0:1, 1:1, 2:2, 3:2, 4:4, 5:4, 6:4, 7:1, 10:8, 11:8, 12:8}
                f.read(sizes[vtype])
        infos = {}
        for _ in range(n_tensors):
            nlen, = struct.unpack('<Q', f.read(8))
            name = f.read(nlen).decode('utf-8', 'replace')
            nd, = struct.unpack('<I', f.read(4))
            dims = struct.unpack(f'<{nd}Q', f.read(8 * nd))
            ttype, = struct.unpack('<I', f.read(4))
            roff, = struct.unpack('<Q', f.read(8))
            infos[name] = (ttype, dims, roff)
        data_base = (f.tell() + 31) & ~31   # GGUF aligns the data section to 32 bytes
    return infos, data_base

def load_q8_rows(fd, abs_off, n_rows, k):
    """Dequantize a q8_0 [k, n_rows] weight tensor (row-major blocks)."""
    bpr = k // BLK
    row_bytes = bpr * BLK_BYTES
    out = np.zeros((n_rows, k), dtype=np.float32)
    for r in range(n_rows):
        fd.seek(abs_off + r * row_bytes)
        raw = fd.read(row_bytes)
        for b in range(bpr):
            blk = raw[b*BLK_BYTES:(b+1)*BLK_BYTES]
            d = fp16_to_fp32(struct.unpack('<H', blk[:2])[0])
            qs = struct.unpack('<32b', blk[2:])
            for j in range(BLK):
                out[r, b*BLK + j] = np.float32(qs[j]) * d
    return out

# ---------- arithmetic paths ----------
def f64_matmul(W, x):
    """W [n_rows, K] f64, x [K] f64 -> [n_rows] f64."""
    return W.astype(np.float64).dot(x.astype(np.float64))

def cpu_q8_matmul(W, x):
    """CPU vec_dot path: quantize x to q8_0 per 32-block, f32 accumulate.
    W [n_rows, K] f32 (dequantized), x [K] f32. Returns [n_rows] f32."""
    n_rows, K = W.shape
    out = np.zeros(n_rows, dtype=np.float32)
    for r in range(n_rows):
        acc = np.float32(0.0)
        for b in range(K // BLK):
            xb = x[b*BLK:(b+1)*BLK]
            amax = np.float32(np.max(np.abs(xb)))
            d_x = np.float32(amax / np.float32(127.0)) if amax > np.float32(0.0) else np.float32(0.0)
            qs = np.zeros(BLK, dtype=np.int32)
            for j in range(BLK):
                v = np.float32(xb[j]) / d_x if d_x > np.float32(0.0) else np.float32(0.0)
                qs[j] = int(np.floor(v + np.float32(0.5)))
            bacc = np.float32(0.0)
            for j in range(BLK):
                bacc = np.float32(bacc + np.float32(W[r, b*BLK+j]) * np.float32(np.float32(qs[j]) * d_x))
            acc = np.float32(acc + bacc)
        out[r] = acc
    return out

def metal_ext_matmul(W, x, nxpsg=16, chpt=4):
    """Replica of kernel_mul_mv_ext_q8_0_f32_impl: f32 activations,
    per-thread f32 sum over residue classes mod nxpsg, simd_shuffle_down
    tree. W [n_rows, K] f32, x [K] f32 -> [n_rows] f32."""
    n_rows, K = W.shape
    out = np.zeros(n_rows, dtype=np.float32)
    for r in range(n_rows):
        partials = [np.float32(0.0) for _ in range(nxpsg)]
        for tx in range(nxpsg):
            acc = np.float32(0.0)
            m = 0
            while True:
                c = tx + chpt*nxpsg*m
                if 4*c >= K: break
                for ch in range(chpt):
                    cch = c + ch*nxpsg
                    if 4*cch >= K: break
                    d = np.float32(0.0)
                    for i in range(4):
                        d = np.float32(d + np.float32(W[r, 4*cch+i]) * np.float32(x[4*cch+i]))
                    acc = np.float32(acc + d)
                m += 1
            partials[tx] = acc
        lanes = [np.float32(p) for p in partials]
        for delta in (8, 4, 2, 1):
            for i in range(nxpsg):
                if i + delta < nxpsg:
                    lanes[i] = np.float32(lanes[i] + lanes[i + delta])
        out[r] = lanes[0]
    return out

def softplus_f32(x):
    # stable: max(x,0) + log1p(exp(-|x|)); naive exp overflows f32 at z>~88
    x = np.asarray(x, dtype=np.float32)
    return (np.maximum(x, 0.0) + np.log1p(np.exp(-np.abs(x)))).astype(np.float32)

def softplus_f64(x):
    x = np.asarray(x, dtype=np.float64)
    return np.maximum(x, 0.0) + np.log1p(np.exp(-np.abs(x)))

# ---------- trace read ----------
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
            data = struct.unpack(f'<{n_embd}f', f.read(4 * n_embd))
            recs.append((exec_id, il, n_embd, n_tokens, start_pos, phase, name, np.array(data, dtype=np.float32)))
    return recs

def pick(recs, il, name, ph=0, sp=0):
    """First prefill-step record with max n_tokens (analyzer convention)."""
    cand = [r for r in recs if r[1] == il and r[6] == name and r[5] == ph and r[4] == sp]
    if not cand: return None
    return max(cand, key=lambda r: r[3])

# ---------- main ----------
def main():
    infos, data_base = gguf_tensor_infos(GGUF)
    print(f"GGUF data_base parsed = {data_base} (expected ~{DATA_BASE})")
    for want in ('blk.0.ssm_f_a.weight', 'blk.0.ssm_f_b.weight', 'blk.0.ssm_dt.bias', 'blk.0.ssm_a'):
        if want not in infos:
            print(f"  MISSING tensor {want}; available blk.0:",
                  sorted(n for n in infos if n.startswith('blk.0.'))[:20])
            sys.exit(2)
        ttype, dims, roff = infos[want]
        print(f"  {want}: type_code={ttype} dims={dims} rel_off={roff} abs_off={data_base + roff}")

    fa_t, fa_dims, fa_roff = infos['blk.0.ssm_f_a.weight']
    fb_t, fb_dims, fb_roff = infos['blk.0.ssm_f_b.weight']
    db_t, db_dims, db_roff = infos['blk.0.ssm_dt.bias']
    sa_t, sa_dims, sa_roff = infos['blk.0.ssm_a']
    assert data_base + fa_roff == FA_OFF, f"f_a offset mismatch: {data_base+fa_roff} != {FA_OFF}"
    assert data_base + fb_roff == FB_OFF, f"f_b offset mismatch: {data_base+fb_roff} != {FB_OFF}"
    assert fa_dims == (2304, 128) and fb_dims == (128, 4096), f"unexpected dims {fa_dims} {fb_dims}"
    assert db_dims == (4096,) and db_t == 0, f"dt.bias: dims={db_dims} type={db_t} (want f32 [4096])"
    assert sa_dims == (1, 32) and sa_t == 0, f"ssm_a: dims={sa_dims} type={sa_t} (want f32 [1,32])"

    fd = open(GGUF, 'rb')
    W_fa = load_q8_rows(fd, FA_OFF, 128, 2304)          # [128, 2304]
    W_fb = load_q8_rows(fd, FB_OFF, 4096, 128)          # [4096, 128]
    fd.seek(data_base + db_roff); dt_bias = np.frombuffer(fd.read(4096*4), dtype=np.float32).copy()
    fd.seek(data_base + sa_roff); A = np.frombuffer(fd.read(32*4), dtype=np.float32).copy()
    fd.close()

    print(f"\n=== loaded constants ===")
    print(f"  dt_bias: amin={dt_bias.min():.6e} amax={np.abs(dt_bias).max():.6e}")
    print(f"  A (ssm_a): {A}")

    cpu = read_tcat(CPU_ACT); metal = read_tcat(MET_ACT)
    c_norm = pick(cpu, 0, 'attn_norm'); m_norm = pick(metal, 0, 'attn_norm')
    c_g1 = pick(cpu, 0, 'kda_g1');      m_g1 = pick(metal, 0, 'kda_g1')
    for tag, r in (('cpu attn_norm', c_norm), ('metal attn_norm', m_norm),
                   ('cpu kda_g1', c_g1), ('metal kda_g1', m_g1)):
        print(f"record {tag}: exec={r[0]} n_tokens={r[3]} pos={r[4]} phase={r[5]} n_embd={r[2]}")
    assert np.array_equal(c_norm[7].view(np.uint32), m_norm[7].view(np.uint32)), "attn_norm NOT bit-identical!"
    print("attn_norm input: bit-identical CPU vs Metal  (n_embd=%d)" % c_norm[2])

    x = c_norm[7].astype(np.float32)                    # [2304] token-0 column
    prod_cpu = c_g1[7]; prod_metal = m_g1[7]            # [4096] production outputs

    # ---- beta boundary self-check (independent captured matmul) ----
    # beta = sigmoid(mul_mat(ssm_beta, cur)), captured as kda_beta [n_head, N]
    if 'blk.0.ssm_beta.weight' in infos:
        bt, bdims, broff = infos['blk.0.ssm_beta.weight']
        print(f"\n=== beta self-check: blk.0.ssm_beta.weight type={bt} dims={bdims} abs_off={data_base+broff} ===")
        if bdims == (2304, 32):
            fdb = open(GGUF, 'rb')
            W_beta = load_q8_rows(fdb, data_base + broff, 32, 2304)   # [32, 2304]
            fdb.close()
            c_beta = pick(cpu, 0, 'kda_beta'); m_beta = pick(metal, 0, 'kda_beta')
            if c_beta and m_beta:
                def sigmoid_f32(v):
                    v = np.asarray(v, dtype=np.float32)
                    return (1.0 / (1.0 + np.exp(-v))).astype(np.float32)
                beta_cpu = sigmoid_f32(cpu_q8_matmul(W_beta, x))
                beta_met = sigmoid_f32(metal_ext_matmul(W_beta, x))
                pb = np.abs(c_beta[7] - m_beta[7])
                print(f"  production cpu vs metal kda_beta: max|d|={pb.max():.6e} mean|d|={pb.mean():.6e}")
                print(f"  prod_cpu  vs cpu  replica: max|d|={np.abs(c_beta[7]-beta_cpu).max():.6e}")
                print(f"  prod_metal vs metal replica: max|d|={np.abs(m_beta[7]-beta_met).max():.6e}")
                print(f"  prod_metal vs f64:           max|d|={np.abs(m_beta[7].astype(np.float64)-sigmoid_f32(f64_matmul(W_beta.astype(np.float64), x.astype(np.float64)))).max():.6e}")

    c_norm = pick(cpu, 0, 'attn_norm'); m_norm = pick(metal, 0, 'attn_norm')
    c_g1 = pick(cpu, 0, 'kda_g1');      m_g1 = pick(metal, 0, 'kda_g1')
    for tag, r in (('cpu attn_norm', c_norm), ('metal attn_norm', m_norm),
                   ('cpu kda_g1', c_g1), ('metal kda_g1', m_g1)):
        print(f"record {tag}: exec={r[0]} n_tokens={r[3]} pos={r[4]} phase={r[5]} n_embd={r[2]}")
    assert np.array_equal(c_norm[7].view(np.uint32), m_norm[7].view(np.uint32)), "attn_norm NOT bit-identical!"
    print("attn_norm input: bit-identical CPU vs Metal  (n_embd=%d)" % c_norm[2])

    x = c_norm[7].astype(np.float32)                    # [2304] token-0 column
    prod_cpu = c_g1[7]; prod_metal = m_g1[7]            # [4096] production outputs

    # production divergence sanity check (should reproduce ~1.438)
    pd = np.abs(prod_cpu - prod_metal)
    print(f"\nPRODUCTION cpu vs metal kda_g1: max|d|={pd.max():.6e} mean|d|={pd.mean():.6e} "
          f"rel@max={pd.max()/max(abs(prod_cpu[np.argmax(pd)]), abs(prod_metal[np.argmax(pd)]), 1e-30):.6e}")

    # ---- reconstruct the chain ----
    fa64  = f64_matmul(W_fa, x)
    fb64  = f64_matmul(W_fb, fa64)
    z64   = fb64 + dt_bias.astype(np.float64)
    s64   = softplus_f64(z64)
    g64   = s64 * np.repeat(A.astype(np.float64), HEAD_DIM)   # row = d + h*128

    fa_cpu = cpu_q8_matmul(W_fa, x)
    fb_cpu = cpu_q8_matmul(W_fb, fa_cpu)
    z_cpu  = (fb_cpu + dt_bias).astype(np.float32)
    s_cpu  = softplus_f32(z_cpu)
    g_cpu  = (s_cpu * np.repeat(A, HEAD_DIM)).astype(np.float32)

    fa_met = metal_ext_matmul(W_fa, x)
    fb_met = metal_ext_matmul(W_fb, fa_met)
    z_met  = (fb_met + dt_bias).astype(np.float32)
    s_met  = softplus_f32(z_met)
    g_met  = (s_met * np.repeat(A, HEAD_DIM)).astype(np.float32)

    print("\n=== stage magnitudes (amax / amin) ===")
    for tag, v in (("x (attn_norm)", x), ("f_a", fa64), ("f_b_out", fb64), ("z", z64),
                   ("s (softplus)", s64), ("g1", g64)):
        print(f"  {tag:14s} amax={np.abs(v).max():.6e}  amin={v.min():.6e}")

    def cmp(tag, a, b, ref=None):
        d = np.abs(a - b)
        i = int(np.argmax(d))
        denom = max(abs(a[i]), abs(b[i]), 1e-30)
        line = (f"  {tag:44s} max|d|={d.max():.6e} mean|d|={d.mean():.6e} "
                f"rel@max={d.max()/denom:.6e} worst=({a[i]:.6e},{b[i]:.6e})")
        if ref is not None:
            dr = np.abs(a - ref); ir = int(np.argmax(dr))
            denomr = max(abs(a[ir]), abs(ref[ir]), 1e-30)
            line += (f"  |{a}-ref|max={dr.max():.6e} (rel {dr.max()/denomr:.6e})")
        print(line)

    print("\n=== stage-by-stage: f64 reference vs cpu replica vs metal replica ===")
    for tag, v64, vc, vm in (("f_a", fa64, fa_cpu, fa_met),
                             ("f_b_out", fb64, fb_cpu, fb_met),
                             ("z (+bias)", z64, z_cpu, z_met),
                             ("s (softplus)", s64, s_cpu, s_met),
                             ("g1 (*A)", g64, g_cpu, g_met)):
        print(f"  --- {tag} ---")
        cmp("cpu replica vs metal replica", vc, vm)
        cmp("f64 vs cpu replica", v64, vc)
        cmp("f64 vs metal replica", v64, vm)

    print("\n=== final g1 vs PRODUCTION captures ===")
    cmp("prod_cpu  vs cpu replica", prod_cpu, g_cpu)
    cmp("prod_metal vs metal replica", prod_metal, g_met)
    cmp("prod_metal vs f64", prod_metal, g64.astype(np.float32))
    cmp("prod_cpu  vs f64", prod_cpu, g64.astype(np.float32))
    cmp("prod_cpu  vs metal replica", prod_cpu, g_met)

    print("\n=== per-head empirical A estimate (prod_metal / s_met) ===")
    s_met_safe = np.where(np.abs(s_met) > 1e-3, s_met, np.nan)
    a_est = []
    for h in range(N_HEAD):
        seg = prod_metal[h*HEAD_DIM:(h+1)*HEAD_DIM] / s_met_safe[h*HEAD_DIM:(h+1)*HEAD_DIM]
        seg = seg[np.isfinite(seg)]
        if len(seg):
            a_est.append((h, float(np.median(seg)), float(A[h])))
    for h, est, loaded in a_est:
        print(f"  head {h:2d}: A_est(median)={est:+.6e}  A_loaded={loaded:+.6e}  ratio={est/loaded if abs(loaded)>1e-30 else float('nan'):.4f}")

    print("\n=== mismatch localization (rows where production disagrees with replicas) ===")
    d_metal = np.abs(prod_metal - g_met)
    d_cpu = np.abs(prod_cpu - g_cpu)
    big = np.where((d_metal > 1.0) | (d_cpu > 1.0))[0]
    print(f"rows with |prod - replica| > 1: {len(big)} / 4096")
    for r in big[:20]:
        h = r // HEAD_DIM
        print(f"  row {r:4d} head {h:2d} A={A[h]:+.3f}  prod_cpu={prod_cpu[r]:+.6e} prod_metal={prod_metal[r]:+.6e} "
              f"g_cpu={g_cpu[r]:+.6e} g_met={g_met[r]:+.6e} z64={z64[r]:+.6e} s64={s64[r]:+.6e} "
              f"f_b64={fb64[r]:+.6e} dt_bias={dt_bias[r]:+.6e} f_a64max={np.abs(fa64).max():.3e}")
    # implied production z (softplus^-1 of prod/A), for the mismatching rows
    print("  implied production z (from prod_metal / A):")
    for r in big[:10]:
        h = r // HEAD_DIM
        if abs(A[h]) > 1e-30:
            s_impl = prod_metal[r] / A[h]
            # softplus^-1: z = log(expm1(s)) for s>0
            z_impl = np.log(np.expm1(max(float(s_impl), 1e-300))) if s_impl > 0 else float('nan')
            print(f"    row {r:4d}: prod/A = {s_impl:+.6e}  implied z = {z_impl:+.6e}  (my z64 = {z64[r]:+.6e})")

    # W_fb per-row max |weight| and block-scale distribution (huge-scale blocks?)
    print("\n=== W_fb weight scale audit ===")
    rowmax = np.abs(W_fb).max(axis=1)
    print(f"  W_fb [4096,128] dequantized: overall max|w| = {rowmax.max():.6e}")
    top = np.argsort(rowmax)[::-1][:8]
    for r in top:
        print(f"    row {r:4d}: max|w| = {rowmax[r]:.6e}")
    print(f"  rows with max|w| > 1: {int((rowmax > 1).sum())}   > 10: {int((rowmax > 10).sum())}   > 100: {int((rowmax > 100).sum())}")

    print("\n=== classification ===")
    d_cm = np.abs(g_cpu - g_met); d_pm = np.abs(prod_metal - g_met)
    d_pc = np.abs(prod_cpu - g_cpu); d_mf = np.abs(prod_metal - g64.astype(np.float32))
    print(f"  cpu-replica vs metal-replica  max|d| = {d_cm.max():.6e}")
    print(f"  prod_metal vs metal-replica   max|d| = {d_pm.max():.6e}   (reproduction of production Metal)")
    print(f"  prod_cpu  vs cpu-replica      max|d| = {d_pc.max():.6e}   (reproduction of production CPU)")
    print(f"  prod_metal vs f64             max|d| = {d_mf.max():.6e}   (Metal agreement with reference)")
    if d_pm.max() < 1e-5 and d_mf.max() < 1e-5:
        print("  => Metal is reproduced by expected Metal arithmetic AND agrees with f64.")
    else:
        print("  => Metal NOT reproduced by expected Metal arithmetic and/or disagrees with f64: candidate defect.")

if __name__ == '__main__':
    main()
