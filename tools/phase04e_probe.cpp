// Phase 4E close — two-pass in-process memory probe.
//
// Loads the Kimi Linear GGUF once (same flags as the phase-04e capture:
// n_gpu_layers=0, load_mode=NONE i.e. --no-mmap, n_ctx=4096), then runs TWO
// full inference passes (prefill + N decode steps each) in ONE process,
// logging per-call process memory:
//
//   pass,step,phase,phys_footprint_mb,resident_mb,compressed_mb,
//   malloc_default_mb,malloc_other_mb,n_zones
//
// phys_footprint via task_info (includes compressed memory — the reliable
// macOS number), resident via task_info, compressed = phys - resident.
// malloc_default_mb is size_in_use of the default zone; malloc_other_mb sums
// size_in_use across all other malloc zones (n_zones total).
//
// Pass separation uses llama_memory_clear(mem, true) between passes (the same
// reset common.cpp performs after warmup), so pass 2 is a full fresh
// prefill+decode in the same process.
//
// Optional hold for external attribution (vmmap / malloc_history):
//   KIMI_PROBE_HOLD_MS=<ms>   sleep at pass 2, step N/2, after the mem row is
//                             written (pid printed to stderr).
//
// The streamed executor is exercised exactly as in llama-cli runs: it is
// enabled by the KIMI_STREAM_EXPERTS env var read by llama.cpp itself, and
// its own KIMI_STREAM_MEM_FILE rows are written in parallel for cross-check.
//
// Build (against the llama.cpp build tree):
//   g++ -O2 -std=c++17 -I llama.cpp/include -I llama.cpp/ggml/include \
//       tools/phase04e_probe.cpp \
//       -L llama.cpp/build-metal/bin -llama \
//       -Wl,-rpath,<abs path>/llama.cpp/build-metal/bin \
//       -o llama.cpp/build-metal/bin/phase04e_probe

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <unistd.h>

#include <mach/mach.h>
#include <malloc/malloc.h>

#include "llama.h"

static double g_phys = 0, g_resident = 0, g_compressed = 0;
static double g_malloc_def = 0, g_malloc_other = 0;
static int    g_n_zones = 0;

static void measure(void) {
    task_vm_info_data_t info;
    mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
    if (task_info(mach_task_self(), TASK_VM_INFO, (task_info_t) &info, &count) == KERN_SUCCESS) {
        g_phys      = (double) info.phys_footprint / (1024.0 * 1024.0);
        g_resident  = (double) info.resident_size   / (1024.0 * 1024.0);
        g_compressed = g_phys - g_resident;
    }

    g_malloc_def = g_malloc_other = 0;
    g_n_zones = 0;
    vm_address_t * zones = nullptr;
    unsigned n_zones = 0;
    if (malloc_get_all_zones(mach_task_self(), nullptr, &zones, &n_zones) == KERN_SUCCESS) {
        const char * def_name = malloc_get_zone_name(malloc_default_zone());
        for (unsigned i = 0; i < n_zones; ++i) {
            malloc_zone_t * z = (malloc_zone_t *) zones[i];
            malloc_statistics_t st;
            malloc_zone_statistics(z, &st);
            if (def_name && malloc_get_zone_name(z) &&
                    strcmp(malloc_get_zone_name(z), def_name) == 0) {
                g_malloc_def = (double) st.size_in_use / (1024.0 * 1024.0);
            } else {
                g_malloc_other += (double) st.size_in_use / (1024.0 * 1024.0);
            }
        }
        g_n_zones = (int) n_zones;
    }
}

// KIMI_PROBE_PURGE_GB=<n>: after both passes, vm_allocate + touch n GB to
// force the compressor to purge the process's own stale compressed pages,
// then deallocate and re-measure. Confirms freed-transient residue is
// purgeable OS accounting, not executor residency.
static double purge_test(int gb) {
    vm_address_t addr = 0;
    const vm_size_t bytes = (vm_size_t) gb * 1024 * 1024 * 1024;
    kern_return_t kr = vm_allocate(mach_task_self(), &addr, bytes, VM_FLAGS_ANYWHERE);
    if (kr != KERN_SUCCESS) { fprintf(stderr, "[probe] purge alloc failed kr=%d\n", kr); return -1; }
    volatile unsigned char * p = (volatile unsigned char *) addr;
    const size_t page = 4096;
    for (size_t off = 0; off < bytes; off += page) { p[off] = (unsigned char) off; }
    usleep(500 * 1000); // give the compressor a moment
    kr = vm_deallocate(mach_task_self(), addr, bytes);
    usleep(500 * 1000);
    measure();
    return g_phys;
}

static void snap(FILE * out, const char * pass, int step, const char * phase, bool hold) {
    if (hold) {
        const char * ms = std::getenv("KIMI_PROBE_HOLD_MS");
        if (ms && atoi(ms) > 0) {
            fprintf(stderr, "[probe] HOLD %d ms pass=%s step=%d pid=%d\n",
                    atoi(ms), pass, step, (int) getpid());
            fflush(stderr);
            usleep((useconds_t) atoi(ms) * 1000);
        }
    }
    measure();
    fprintf(out, "%s,%d,%s,%.1f,%.1f,%.1f,%.1f,%.1f,%d\n",
            pass, step, phase, g_phys, g_resident, g_compressed,
            g_malloc_def, g_malloc_other, g_n_zones);
    fflush(out);
}

int main(int argc, char ** argv) {
    if (argc < 5) {
        fprintf(stderr, "usage: %s MODEL PROMPT_FILE N_DECODE OUTDIR\n", argv[0]);
        return 2;
    }
    const char * model_path = argv[1];
    const char * prompt_file = argv[2];
    const int    n_decode = atoi(argv[3]);
    const char * outdir   = argv[4];
    const int    n_ctx    = 4096; // pinned, matching the phase-04e captures

    // read prompt
    FILE * pf = fopen(prompt_file, "rb");
    if (!pf) { perror("prompt"); return 2; }
    fseek(pf, 0, SEEK_END);
    long psize = ftell(pf);
    fseek(pf, 0, SEEK_SET);
    std::string prompt((size_t) psize, '\0');
    if (fread(&prompt[0], 1, (size_t) psize, pf) != (size_t) psize) { perror("read"); return 2; }
    fclose(pf);

    std::string mem_path = std::string(outdir) + "/probe_mem.csv";
    FILE * out = fopen(mem_path.c_str(), "w");
    if (!out) { perror("out"); return 2; }
    fprintf(out, "pass,step,phase,phys_footprint_mb,resident_mb,compressed_mb,"
                 "malloc_default_mb,malloc_other_mb,n_zones\n");

    llama_backend_init();

    llama_model_params mp = llama_model_default_params();
    mp.n_gpu_layers = 0;
    mp.load_mode    = LLAMA_LOAD_MODE_NONE; // --no-mmap equivalent

    llama_model * model = llama_model_load_from_file(model_path, mp);
    if (!model) { fprintf(stderr, "model load failed\n"); return 1; }

    llama_context_params cp = llama_context_default_params();
    cp.n_ctx = (uint32_t) n_ctx;
    llama_context * ctx = llama_init_from_model(model, cp);
    if (!ctx) { fprintf(stderr, "context init failed\n"); return 1; }

    const llama_vocab * vocab = llama_model_get_vocab(model);

    // tokenize prompt (same path as llama-cli: add_special=true)
    std::vector<llama_token> toks((size_t) prompt.size() + 64);
    int nt = llama_tokenize(vocab, prompt.c_str(), (int32_t) prompt.size(),
                            toks.data(), (int32_t) toks.size(), true, false);
    if (nt < 0) { fprintf(stderr, "tokenize failed: %d\n", nt); return 1; }
    toks.resize((size_t) nt);

    // replicate llama-cli warmup: decode BOS/EOS, then clear
    {
        std::vector<llama_token> tmp;
        llama_token bos = llama_vocab_bos(vocab);
        llama_token eos = llama_vocab_eos(vocab);
        if (bos != LLAMA_TOKEN_NULL) tmp.push_back(bos);
        if (eos != LLAMA_TOKEN_NULL) tmp.push_back(eos);
        if (tmp.empty()) tmp.push_back(0);
        llama_decode(ctx, llama_batch_get_one(tmp.data(), tmp.size()));
        llama_memory_clear(llama_get_memory(ctx), true);
        llama_synchronize(ctx);
    }

    snap(out, "load", 0, "load_done", false);

    llama_token gen_tok = 1; // fixed valid token id; no sampler needed

    for (int pass = 1; pass <= 2; ++pass) {
        llama_memory_clear(llama_get_memory(ctx), true);
        llama_synchronize(ctx);

        snap(out, pass == 1 ? "p1" : "p2", 0, "prefill_start", false);
        int rc = llama_decode(ctx, llama_batch_get_one(toks.data(), toks.size()));
        if (rc != 0) { fprintf(stderr, "pass %d prefill decode rc=%d\n", pass, rc); return 1; }
        snap(out, pass == 1 ? "p1" : "p2", 0, "prefill_end", false);

        const int hold_step = n_decode / 2;
        for (int s = 1; s <= n_decode; ++s) {
            rc = llama_decode(ctx, llama_batch_get_one(&gen_tok, 1));
            if (rc != 0) { fprintf(stderr, "pass %d step %d decode rc=%d\n", pass, s, rc); return 1; }
            snap(out, pass == 1 ? "p1" : "p2", s, "decode",
                 (pass == 2 && s == hold_step));
        }
        snap(out, pass == 1 ? "p1" : "p2", n_decode, "pass_end", false);
    }

    // optional purge test: does forced pressure reclaim the residue?
    const char * purge = std::getenv("KIMI_PROBE_PURGE_GB");
    if (purge && atoi(purge) > 0) {
        fprintf(stderr, "[probe] purge test %d GB\n", atoi(purge));
        fflush(stderr);
        const double after = purge_test(atoi(purge));
        fprintf(out, "purge,%d,%s,%.1f,%.1f,%.1f,%.1f,%.1f,%d\n",
                atoi(purge), "after_pressure", after, g_resident, g_compressed,
                g_malloc_def, g_malloc_other, g_n_zones);
        fflush(out);
    }

    fclose(out);
    llama_free(ctx);
    llama_model_free(model);
    llama_backend_free();
    fprintf(stderr, "[probe] done\n");
    return 0;
}
