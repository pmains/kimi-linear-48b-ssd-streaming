// Phase 11 dequantization isolation probe — CPU vs Metal, identical raw blocks.
//
// Reads a small deterministic subset of raw quantized blocks from the GGUF at a
// given tensor offset, then dequantizes the SAME bytes two ways:
//   CPU:  exact replication of ggml-quants.c dequantize_row_q8_0 /
//         dequantize_row_mxfp4 (function bodies copied verbatim; cited below)
//   Metal: a real Metal compute kernel running the EXACT dequant functions from
//         ggml-metal.metal (dequantize_q8_0 / dequantize_mxfp4, e8m0_to_fp32,
//         kvalues tables) compiled at runtime from the embedded MSL source
// and reports whether the resulting f32 values are bit-identical, with the
// first mismatch and representative values.
//
// Usage:
//   phase11_dequant_probe <model.gguf> <tensor_offset> <q8_0|mxfp4> [n_blocks]
//
// Deterministic subset: the first n_blocks blocks of the tensor (default 8).
//
// Build (diagnostic-only; no llama.cpp changes):
//   xcrun -sdk macosx clang -fobjc-arc -framework Foundation -framework Metal \
//       tools/phase11_dequant_probe.m -o /tmp/phase11_dequant_probe
//
// Note on tensor types (GGUF census, 2026-08-31): the layer-0 KDA projection
// tensors localized as the first CPU/Metal divergence (blk.0.attn_q/k/v,
// attn_output, ssm_f_a/f_b, ssm_g_a/g_b, ssm_beta) are ALL q8_0, not MXFP4.
// Only the MoE expert tensors (blk.N.ffn_*_exps) and MLA K/V bias tensors are
// mxfp4. Run this probe on blk.0.attn_q.weight (q8_0, the actual localized
// op) as primary and on an expert tensor (mxfp4) as secondary.

#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <stdio.h>
#import <stdlib.h>
#import <string.h>
#import <fcntl.h>
#import <unistd.h>
#import <stdint.h>

// ---- exact CPU dequant math (copied from llama.cpp/ggml/src/ggml-quants.c) ----

static float fp16_to_fp32(uint16_t h) { // GGML_FP16_TO_FP32
    uint32_t sign = (h & 0x8000) << 16;
    uint32_t exp  = (h & 0x7C00) >> 10;
    uint32_t mant = h & 0x03FF;
    uint32_t bits;
    if (exp == 0) {
        if (mant == 0) bits = sign;
        else { // subnormal
            int e = -1;
            do { e++; mant <<= 1; } while (!(mant & 0x0400));
            bits = sign | ((uint32_t)(127 - 15 - e) << 23) | ((mant & 0x03FF) << 13);
        }
    } else if (exp == 0x1F) {
        bits = sign | 0x7F800000 | (mant << 13); // inf/nan
    } else {
        bits = sign | ((exp + 127 - 15) << 23) | (mant << 13);
    }
    float f; memcpy(&f, &bits, 4); return f;
}

// ggml_e8m0_to_fp32 (full) and ggml_e8m0_to_fp32_half (full/2), from ggml-impl.h
static float e8m0_to_fp32_full(uint8_t x) {
    uint32_t bits = (x == 0) ? 0x00400000 : ((uint32_t) x << 23);
    float f; memcpy(&f, &bits, 4); return f;
}
static float e8m0_to_fp32_half(uint8_t x) {
    uint32_t bits = (x < 2) ? (0x00200000u << x) : ((uint32_t)(x - 1) << 23);
    float f; memcpy(&f, &bits, 4); return f;
}

// kvalues_fp4 == kvalues_mxfp4 (ggml-common.h:1126-1129) — CPU table
static const int8_t kvalues_fp4[16] = {
    0, 1, 2, 3, 4, 6, 8, 12, 0, -1, -2, -3, -4, -6, -8, -12
};

// dequantize_row_q8_0 (ggml-quants.c:556-567): block = {half d; int8 qs[32]},
// 34 bytes; QK8_0 = 32
static void cpu_dequant_q8_0(const uint8_t * blk, float * y, int n_blocks) {
    for (int i = 0; i < n_blocks; i++) {
        const uint8_t * b = blk + i * 34;
        uint16_t d16; memcpy(&d16, b, 2);
        const float d = fp16_to_fp32(d16);
        const int8_t * qs = (const int8_t *)(b + 2);
        for (int j = 0; j < 32; j++) y[i*32 + j] = qs[j] * d;
    }
}

// dequantize_row_mxfp4 (ggml-quants.c:569-585): block = {uint8 e; uint8 qs[16]},
// 17 bytes; QK_MXFP4 = 32; CPU uses e8m0_to_fp32_HALF scale with kvalues_fp4
static void cpu_dequant_mxfp4(const uint8_t * blk, float * y, int n_blocks) {
    for (int i = 0; i < n_blocks; i++) {
        const uint8_t * b = blk + i * 17;
        const float d = e8m0_to_fp32_half(b[0]); // GGML_E8M0_TO_FP32_HALF
        const uint8_t * qs = b + 1;
        for (int j = 0; j < 16; j++) {
            y[i*32 + j]      = kvalues_fp4[qs[j] & 0x0F] * d;
            y[i*32 + j + 16] = kvalues_fp4[qs[j] >> 4]    * d;
        }
    }
}

// ---- exact Metal dequant math (copied from ggml-metal.metal) ----
// kvalues_mxfp4_f (ggml-metal.metal:51-54) — Metal table (halved values)
static const char * metal_src =
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct block_q8_0 { half d; int8_t qs[32]; };\n"
"struct block_mxfp4 { uint8_t e; uint8_t qs[16]; };\n"
"constant static float kvalues_mxfp4_f[16] = {\n"
"    0, .5f, 1.f, 1.5f, 2.f, 3.f, 4.f, 6.f, -0, -.5f, -1.f, -1.5f, -2.f, -3.f, -4.f, -6.f\n"
"};\n"
"static inline float e8m0_to_fp32(uint8_t x) {\n"
"    uint32_t bits = (x == 0) ? 0x00400000 : ((uint32_t)x << 23);\n"
"    return as_type<float>(bits);\n"
"}\n"
// dequantize_q8_0 (ggml-metal.metal:658-670): d = xb->d (half->float),
// y[i] = qs[i + 16*il] * d; il=0 -> qs[0..15], il=1 -> qs[16..31]
"kernel void deq_q8_0(device const uchar * in, device float * out, uint gid [[thread_position_in_grid]]) {\n"
"    device const block_q8_0 * xb = (device const block_q8_0 *)(in + gid * 34);\n"
"    device const int8_t * qs = (device const int8_t *)xb->qs;\n"
"    const float d = (float)xb->d;\n"
"    for (int il = 0; il < 2; il++) {\n"
"        for (int i = 0; i < 16; i++) out[gid*32 + il*16 + i] = qs[i + 16*il] * d;\n"
"    }\n"
"}\n"
// dequantize_mxfp4 (ggml-metal.metal:682-695): d = e8m0_to_fp32(e) FULL,
// q2[4*i+k] >> shr & 0x0F; shr = il>=1 ? 4 : 0 (il 0..1)
"kernel void deq_mxfp4(device const uchar * in, device float * out, uint gid [[thread_position_in_grid]]) {\n"
"    device const block_mxfp4 * xb = (device const block_mxfp4 *)(in + gid * 17);\n"
"    device const uint8_t * q2 = (device const uint8_t *)xb->qs;\n"
"    const float d = e8m0_to_fp32(xb->e);\n"
"    for (int il = 0; il < 2; il++) {\n"
"        const uint8_t shr = il >= 1 ? 4 : 0;\n"
"        for (int i = 0; i < 4; i++) {\n"
"            out[gid*32 + il*16 + i*4 + 0] = d * kvalues_mxfp4_f[(q2[4*i + 0] >> shr) & 0x0F];\n"
"            out[gid*32 + il*16 + i*4 + 1] = d * kvalues_mxfp4_f[(q2[4*i + 1] >> shr) & 0x0F];\n"
"            out[gid*32 + il*16 + i*4 + 2] = d * kvalues_mxfp4_f[(q2[4*i + 2] >> shr) & 0x0F];\n"
"            out[gid*32 + il*16 + i*4 + 3] = d * kvalues_mxfp4_f[(q2[4*i + 3] >> shr) & 0x0F];\n"
"        }\n"
"    }\n"
"}\n";

static int run_metal_dequant(const char * kind, const uint8_t * raw, int n_blocks,
                             float * out) {
    id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
    if (!dev) { fprintf(stderr, "no Metal device\n"); return 1; }
    NSError * err = nil;
    id<MTLLibrary> lib = [dev newLibraryWithSource:[NSString stringWithUTF8String:metal_src]
                                           options:nil error:&err];
    if (!lib) { fprintf(stderr, "metal compile failed: %s\n", err.localizedDescription.UTF8String); return 1; }
    const int block_bytes = strcmp(kind, "q8_0") == 0 ? 34 : 17;
    const char * kname = strcmp(kind, "q8_0") == 0 ? "deq_q8_0" : "deq_mxfp4";
    id<MTLFunction> fn = [lib newFunctionWithName:[NSString stringWithUTF8String:kname]];
    if (!fn) { fprintf(stderr, "kernel %s not found\n", kname); return 1; }
    id<MTLComputePipelineState> pipe = [dev newComputePipelineStateWithFunction:fn error:&err];
    if (!pipe) { fprintf(stderr, "pipeline failed: %s\n", err.localizedDescription.UTF8String); return 1; }
    id<MTLBuffer> inb  = [dev newBufferWithLength:(NSUInteger)(block_bytes * n_blocks)
                                          options:MTLResourceStorageModeShared];
    id<MTLBuffer> outb = [dev newBufferWithLength:(NSUInteger)(32 * n_blocks * sizeof(float))
                                          options:MTLResourceStorageModeShared];
    memcpy(inb.contents, raw, (size_t)(block_bytes * n_blocks));
    id<MTLCommandQueue> q = [dev newCommandQueue];
    id<MTLCommandBuffer> cb = [q commandBuffer];
    id<MTLComputeCommandEncoder> enc = [cb computeCommandEncoder];
    [enc setComputePipelineState:pipe];
    [enc setBuffer:inb  offset:0 atIndex:0];
    [enc setBuffer:outb offset:0 atIndex:1];
    MTLSize tg = MTLSizeMake(1, 1, 1);
    MTLSize grid = MTLSizeMake((NSUInteger)n_blocks, 1, 1);
    [enc dispatchThreadgroups:grid threadsPerThreadgroup:tg];
    [enc endEncoding];
    [cb commit];
    [cb waitUntilCompleted];
    memcpy(out, outb.contents, (size_t)(32 * n_blocks * sizeof(float)));
    return 0;
}

int main(int argc, const char * argv[]) {
    if (argc < 4) {
        fprintf(stderr, "usage: %s <model.gguf> <tensor_offset> <q8_0|mxfp4> [n_blocks]\n", argv[0]);
        return 2;
    }
    const char * path = argv[1];
    const uint64_t off = strtoull(argv[2], NULL, 10);
    const char * kind = argv[3];
    const int n_blocks = argc > 4 ? atoi(argv[4]) : 8;
    const int block_bytes = strcmp(kind, "q8_0") == 0 ? 34 : 17;
    if (strcmp(kind, "q8_0") != 0 && strcmp(kind, "mxfp4") != 0) {
        fprintf(stderr, "kind must be q8_0 or mxfp4\n"); return 2;
    }
    int fd = open(path, O_RDONLY);
    if (fd < 0) { perror("open"); return 1; }
    uint8_t * raw = malloc((size_t)(block_bytes * n_blocks));
    ssize_t n = pread(fd, raw, (size_t)(block_bytes * n_blocks), (off_t)off);
    if (n != (ssize_t)(block_bytes * n_blocks)) {
        fprintf(stderr, "short pread: %zd\n", n); return 1;
    }
    close(fd);

    float * cpu = calloc((size_t)(32 * n_blocks), sizeof(float));
    float * met = calloc((size_t)(32 * n_blocks), sizeof(float));
    if (strcmp(kind, "q8_0") == 0) cpu_dequant_q8_0(raw, cpu, n_blocks);
    else                           cpu_dequant_mxfp4(raw, cpu, n_blocks);
    if (run_metal_dequant(kind, raw, n_blocks, met)) return 1;

    int first_mismatch = -1;
    int n_mismatch = 0;      // raw bit mismatches (includes NaN payload diffs)
    int n_numeric = 0;       // numeric mismatches (non-NaN values differing)
    int n_nan_payload = 0;   // both NaN but different payload bits
    int first_numeric = -1;
    double max_abs = 0, sum_abs = 0;
    for (int i = 0; i < 32 * n_blocks; i++) {
        uint32_t cb_, cm_; memcpy(&cb_, &cpu[i], 4); memcpy(&cm_, &met[i], 4);
        if (cb_ != cm_) {
            n_mismatch++;
            if (first_mismatch < 0) first_mismatch = i;
            int cnan = isnan(cpu[i]), mnan = isnan(met[i]);
            if (cnan && mnan) n_nan_payload++;
            else {
                n_numeric++;
                if (first_numeric < 0) first_numeric = i;
            }
        }
        if (!isnan(cpu[i]) && !isnan(met[i])) {
            double d = fabs((double)cpu[i] - (double)met[i]);
            if (d > max_abs) max_abs = d;
            sum_abs += d;
        }
    }

    printf("kind=%s offset=%llu blocks=%d  (block_bytes=%d)\n", kind, off, n_blocks, block_bytes);
    printf("elements compared: %d\n", 32 * n_blocks);
    printf("bit-identical: %s\n", n_mismatch == 0 ? "YES" : "NO");
    printf("raw bit mismatches: %d  (numeric: %d, NaN-payload-only: %d)\n",
           n_mismatch, n_numeric, n_nan_payload);
    if (n_numeric > 0) {
        printf("NUMERIC mismatches: %d  first at element %d (block %d)\n",
               n_numeric, first_numeric, first_numeric / 32);
        printf("max|d| (non-NaN) = %.6e  mean|d| (non-NaN) = %.6e\n",
               max_abs, sum_abs / (32 * n_blocks - (n_nan_payload)));
        int e = first_numeric;
        printf("first numeric mismatch: cpu=%.9e metal=%.9e\n", cpu[e], met[e]);
    } else if (n_nan_payload > 0) {
        int e = first_mismatch;
        printf("first NaN-payload diff at element %d: cpu=0x%08x metal=0x%08x (both NaN)\n",
               e, *(uint32_t*)&cpu[e], *(uint32_t*)&met[e]);
        printf("=> representation decoding AGREES for all non-NaN values; only NaN payload encoding differs.\n");
    }
    printf("\nfirst block raw bytes:");
    for (int i = 0; i < (block_bytes > 16 ? 16 : block_bytes); i++) printf(" %02x", raw[i]);
    printf("\nfirst block scale: ");
    if (strcmp(kind, "q8_0") == 0) { uint16_t d16; memcpy(&d16, raw, 2); printf("d(half)=0x%04x -> %g", d16, fp16_to_fp32(d16)); }
    else { printf("e8m0=0x%02x  full=%g  half=%g", raw[0], e8m0_to_fp32_full(raw[0]), e8m0_to_fp32_half(raw[0])); }
    printf("\n");
    printf("first 8 values cpu :"); for (int i = 0; i < 8; i++) printf(" %.6g", cpu[i]); printf("\n");
    printf("first 8 values metal:"); for (int i = 0; i < 8; i++) printf(" %.6g", met[i]); printf("\n");

    free(raw); free(cpu); free(met);
    return n_mismatch == 0 ? 0 : 3;
}
