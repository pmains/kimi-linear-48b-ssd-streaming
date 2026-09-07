// Phase 11 — production-path repack dump for a single tensor.
//
// Resolves "GGUF storage -> runtime tensor -> kernel addressing" for the
// loader's load-time CPU_REPACK transformation (q8_0 -> q8_0_4x8) WITHOUT
// loading the whole model (which OOMs on a 24 GB host: repacking all
// tensors needs ~27 GB of output buffers on top of the 30 GB mmap).
//
// It exercises the EXACT production repack path for one tensor:
//   ggml_backend_tensor_set() on a CPU_REPACK buffer runs
//   ggml_backend_cpu_repack_buffer_set_tensor -> the same
//   ggml::cpu::repack::repack<block_q8_0, 8, 4> (q8_0_4x8) the loader uses.
//
// Usage:
//   phase11_repack_dump <gguf> [tensor_name] [out_bin]
//   (out_bin default /tmp/attn_q_repacked.bin; tensor default
//   blk.0.attn_q.weight)
//
// Build (from repo root):
//   c++ -std=c++17 -I llama.cpp/include -I llama.cpp/ggml/include \
//       tools/phase11_repack_dump.cpp \
//       -L llama.cpp/build-metal/bin -Wl,-rpath,llama.cpp/build-metal/bin \
//       -lllama -lggml -lggml-base -lggml-cpu \
//       -o /tmp/phase11_repack_dump
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

#include "ggml.h"
#include "gguf.h"
#include "ggml-backend.h"

// CPU_REPACK buft accessor lives in libggml-cpu (C++ linkage; declared
// here with the same signature so the mangled symbol matches).
ggml_backend_buffer_type_t ggml_backend_cpu_repack_buffer_type();

int main(int argc, char ** argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <gguf> [tensor] [out_bin]\n", argv[0]); return 2; }
    const char * gguf_path = argv[1];
    const char * want      = argc > 2 ? argv[2] : "blk.0.attn_q.weight";
    const char * out_path  = argc > 3 ? argv[3] : "/tmp/attn_q_repacked.bin";

    // No backend loading needed: the CPU_REPACK buft (and its alloc/set
    // iface) come from the linked libggml-cpu. Loading all backends here
    // drags in Metal, which segfaults at device/residency-set init on this
    // host (24 GB, live llama-server holding GPU resources).

    struct gguf_init_params ip = { /*no_alloc=*/ true, /*ctx=*/ nullptr };
    gguf_context * g = gguf_init_from_file(gguf_path, ip);
    if (!g) { fprintf(stderr, "gguf init failed\n"); return 1; }
    const int ti = gguf_find_tensor(g, want);
    if (ti < 0) { fprintf(stderr, "tensor '%s' not found\n", want); return 1; }

    const int64_t * ne = gguf_get_tensor_ne(g, ti);
    enum ggml_type type = gguf_get_tensor_type(g, ti);
    const size_t off_abs = gguf_get_data_offset(g) + gguf_get_tensor_offset(g, ti);
    const size_t nbytes  = gguf_get_tensor_size(g, ti);

    printf("tensor %s type=%d ne=[%lld,%lld,%lld,%lld] abs_off=%zu nbytes=%zu\n",
           want, (int) type,
           (long long) ne[0], (long long) ne[1], (long long) ne[2], (long long) ne[3],
           off_abs, nbytes);

    // raw file bytes (GGUF storage layout)
    std::vector<uint8_t> raw(nbytes);
    {
        FILE * f = fopen(gguf_path, "rb");
        if (!f) { fprintf(stderr, "open %s failed\n", gguf_path); return 1; }
        fseek(f, (long) off_abs, SEEK_SET);
        if (fread(raw.data(), 1, nbytes, f) != nbytes) { fprintf(stderr, "read failed\n"); return 1; }
        fclose(f);
    }

    // ggml tensor
    ggml_init_params gp = { ggml_tensor_overhead() + 1024, nullptr, /*no_alloc=*/ true };
    ggml_context * ctx = ggml_init(gp);
    if (!ctx) { fprintf(stderr, "ggml_init failed\n"); return 1; }
    ggml_tensor * t = ggml_new_tensor_2d(ctx, type, ne[0], ne[1]);
    ggml_set_name(t, want);
    if (ggml_nbytes(t) != nbytes) { fprintf(stderr, "size mismatch %zu vs %zu\n", ggml_nbytes(t), nbytes); return 1; }

    // production repack path: CPU_REPACK buffer + tensor_set
    ggml_backend_buffer_type_t buft = ggml_backend_cpu_repack_buffer_type();
    if (!buft) { fprintf(stderr, "CPU_REPACK buft unavailable\n"); return 1; }
    ggml_backend_buffer_t buf = ggml_backend_buft_alloc_buffer(buft, nbytes);
    if (!buf) { fprintf(stderr, "repack buffer alloc failed\n"); return 1; }
    ggml_backend_tensor_alloc(buf, t, ggml_backend_buffer_get_base(buf));
    ggml_backend_tensor_set(t, raw.data(), 0, nbytes);   // <- production repack runs here
    ggml_backend_buffer_set_usage(buf, GGML_BACKEND_BUFFER_USAGE_WEIGHTS);

    // read back the repacked bytes: the CPU_REPACK buffer has no get_tensor
    // iface (repacked data is kernel-consumed), but it is a host buffer, so
    // read directly from the buffer base (the tensor is the only one).
    std::vector<uint8_t> out(nbytes);
    memcpy(out.data(), ggml_backend_buffer_get_base(buf), nbytes);

    FILE * fo = fopen(out_path, "wb");
    if (!fo) { fprintf(stderr, "open %s failed\n", out_path); return 1; }
    fwrite(out.data(), 1, nbytes, fo);
    fclose(fo);
    printf("wrote %zu bytes to %s\n", nbytes, out_path);

    // first x4 block: d[0..3] (fp16) + first 16 qs bytes
    const uint8_t * b0 = out.data();
    for (int i = 0; i < 4; i++) {
        uint16_t d16; memcpy(&d16, b0 + i*2, 2);
        printf("  x4[0].d[%d]=0x%04x\n", i, d16);
    }
    printf("  x4[0].qs[0..15]:");
    for (int i = 0; i < 16; i++) printf(" %02x", b0[8 + i]);
    printf("\n");

    ggml_backend_buffer_free(buf);
    ggml_free(ctx);
    gguf_free(g);
    return 0;
}
