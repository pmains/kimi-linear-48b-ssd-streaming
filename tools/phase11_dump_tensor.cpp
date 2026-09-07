// Phase 11 — runtime tensor dump utility (weight-mapping verification).
//
// Determines the EXACT q8_0 weight bytes production consumes for a named
// tensor (default blk.0.attn_q.weight) by reading the tensor through the
// model loader API — the same addressing the runtime uses — instead of
// manual GGUF offset arithmetic.
//
// Backend-init fix (2026-09-01): llama_model_load_from_file requires at
// least one ggml backend loaded. ggml_backend_load_all() searches
// GGML_BACKEND_DIR / the executable dir / CWD for backend plugin dylibs
// (libggml-cpu.dylib etc.); for a tool built to /tmp that finds nothing.
// Pass the build dir explicitly (env KIMI_BACKEND_DIR, default
// llama.cpp/build-metal/bin relative to CWD).
//
// Build (from repo root):
//   c++ -std=c++17 -I llama.cpp/include -I llama.cpp/ggml/include \
//       tools/phase11_dump_tensor.cpp \
//       -L llama.cpp/build-metal/bin -Wl,-rpath,@loader_path \
//       -lllama -lggml -lggml-base -lggml-cpu \
//       -o /tmp/phase11_dump_tensor
// Run (DYLD_LIBRARY_PATH needed because @rpath resolves relative to the
// binary location, which is /tmp):
//   DYLD_LIBRARY_PATH=llama.cpp/build-metal/bin \
//       /tmp/phase11_dump_tensor models/kimi-linear/...MXFP4_MOE.gguf \
//       [tensor_name]
//
// Output per matching tensor: ggml type, ne[0..3], nb[0..3] (byte
// strides), data pointer, and for q8_0: the fp16 scale (d16) of the
// first N q8_0 blocks of the first M rows (row stride = nb[1]).
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <string>
#include <vector>
#include <utility>

#include "ggml-backend.h"
#include "llama.h"

// internal accessor (llama-model.h) — same tree, same ABI
std::vector<std::pair<std::string, ggml_tensor *>> llama_internal_get_tensor_map(const llama_model * model);

static void dump_tensor(const char * gguf_path, const char * want) {
    llama_model_params mp = llama_model_default_params();
    mp.n_gpu_layers = 0;   // CPU/mmap view: same bytes, avoids the 26 GB Metal mapping on a 24 GB host
    llama_model * m = llama_model_load_from_file(gguf_path, mp);
    if (!m) { fprintf(stderr, "load failed\n"); return; }
    auto tm = llama_internal_get_tensor_map(m);
    int found = 0;
    for (auto & kv : tm) {
        if (want && kv.first != want) continue;
        ggml_tensor * t = kv.second;
        found++;
        printf("name=%s type=%d ne=[%lld,%lld,%lld,%lld] nb=[%zu,%zu,%zu,%zu] data=%p\n",
            t->name, (int) t->type,
            (long long) t->ne[0], (long long) t->ne[1],
            (long long) t->ne[2], (long long) t->ne[3],
            t->nb[0], t->nb[1], t->nb[2], t->nb[3], (void *) t->data);
        if (t->type == GGML_TYPE_Q8_0 && t->ne[0] % 32 == 0) {
            const int nrows = t->ne[1] < 4 ? (int) t->ne[1] : 4;
            const int nblk  = (int) (t->ne[0] / 32);
            const unsigned char * base = (const unsigned char *) t->data;
            for (int r = 0; r < nrows; r++) {
                printf("  row %d (byte off %lld): block scales:", r, (long long) (r * t->nb[1]));
                for (int b = 0; b < nblk && b < 72; b++) {
                    uint16_t d16; memcpy(&d16, base + r * t->nb[1] + b * 34, 2);
                    int exp = (d16 >> 10) & 0x1F, mant = d16 & 0x3FF;
                    bool nan = (exp == 0x1F && mant != 0);
                    printf(" %04x%s", d16, nan ? "!" : "");
                }
                printf("\n");
            }
            // NaN census over the first 4 rows
            for (int r = 0; r < nrows; r++) {
                int nan = 0;
                for (int b = 0; b < nblk; b++) {
                    uint16_t d16; memcpy(&d16, base + r * t->nb[1] + b * 34, 2);
                    int exp = (d16 >> 10) & 0x1F, mant = d16 & 0x3FF;
                    if (exp == 0x1F && mant != 0) nan++;
                }
                printf("  row %d: NaN scales %d/%d\n", r, nan, nblk);
            }
        }
    }
    if (!found) printf("(no tensor matched '%s')\n", want ? want : "");
    llama_model_free(m);
}

int main(int argc, char ** argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <gguf> [tensor_name]\n", argv[0]); return 2; }
    const char * bd = getenv("KIMI_BACKEND_DIR");
    if (!bd) bd = "llama.cpp/build-metal/bin";
    ggml_backend_load_all_from_path(bd);
    dump_tensor(argv[1], argc > 2 ? argv[2] : "blk.0.attn_q.weight");
    return 0;
}
