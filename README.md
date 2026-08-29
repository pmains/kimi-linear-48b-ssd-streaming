# Kimi Linear SSD-Backed Expert Streaming

Storage-backed inference for
[`moonshotai/Kimi-Linear-48B-A3B-Instruct`](https://huggingface.co/moonshotai/Kimi-Linear-48B-A3B-Instruct)
(Q4_K_M, 48B total / 3B active) on Apple Silicon, built on
[llama.cpp](https://github.com/ggml-org/llama.cpp) / ggml / Metal.

Routed MoE experts are kept primarily on SSD and loaded on demand into a
bounded in-memory cache, so the ~28 GiB expert collection does not have
to be resident. The dense trunk (~1.2–1.5 GiB) plus a configurable expert
cache fit a 24 GB unified-memory Mac.

This is the **Phase 10 reproducible release** of the frozen Phase 9
scientific baseline (llama.cpp commit `caea707b7`). It packages the
validated implementation for installation, benchmarking, and validation
by someone who has not followed the project's development history.
Scientific history: `progress/phase-XX-report.md`; protocol:
`TOOLS.md` → "Phase 9G Benchmark Protocol".

## Quick start

    # 1. clone (see "Repository layout" for what this includes)
    git clone <this-repo-url>
    cd <repo>

    # 2. build (see "Build" — requires macOS + Xcode CLT + cmake + Homebrew)
    cd llama.cpp
    cmake -B build-release -DCMAKE_BUILD_TYPE=Release \
        -DGGML_ACCELERATE=ON -DGGML_BACKEND_DL=ON \
        -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=Apple \
        -DGGML_CPU=ON -DGGML_CPU_ALL_VARIANTS=ON -DGGML_CPU_REPACK=ON \
        -DGGML_METAL=ON -DGGML_NATIVE=OFF -DGGML_OPENMP=ON \
        -DLLAMA_BUILD_APP=ON -DLLAMA_BUILD_COMMON=ON -DLLAMA_BUILD_EXAMPLES=ON \
        -DLLAMA_BUILD_IS_DEV=ON -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TESTS=ON \
        -DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_UI=ON
    cmake --build build-release -j $(sysctl -n hw.ncpu)

    # 3. obtain the model (see "Model setup" — 28.00 GiB, sha256 below)
    mkdir -p models/kimi-linear
    # ... place moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf there ...

    # 4. verify the installation (exit 0 = PASS; ~2–4 min)
    tools/phase10_verify.sh

    # 5. run (server, OpenAI-compatible API) or benchmark (see below)

## Supported hardware / OS

- Apple Silicon Mac (arm64); developed and validated on an Apple M5,
  24 GB unified memory, macOS 26.5.2, 10 cores.
- CPU execution (`-ngl 0`) is the validated path. The Metal backend
  builds, but the storage-backed expert path is CPU-validated; see
  "Known limitations".
- 24 GB unified memory is the target. Smaller machines will need a
  smaller expert-cache budget (and may page under load); larger machines
  can raise `KIMI_EXPERT_CACHE_MB`.

## Requirements

- macOS with Xcode Command Line Tools (`xcode-select --install`)
- cmake ≥ 3.x (tested 4.4.2)
- Homebrew with `openssl@3` (llama-server and llama-cli link
  `/opt/homebrew/opt/openssl@3/lib/libssl.3.dylib` and
  `libcrypto.3.dylib`)
- ~30 GB free disk for the model, plus ~1–2 GB for the build
- 24 GB unified memory recommended

## Build

The build is the standard llama.cpp CMake flow from the pinned fork
commit. No source edits are required on a clean checkout.

    cd llama.cpp
    git checkout caea707b7d216c8685cf85db1a2682e26deb47d9   # the release baseline
    cmake -B build-release -DCMAKE_BUILD_TYPE=Release \
        -DGGML_ACCELERATE=ON -DGGML_BACKEND_DL=ON \
        -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=Apple \
        -DGGML_CPU=ON -DGGML_CPU_ALL_VARIANTS=ON -DGGML_CPU_REPACK=ON \
        -DGGML_METAL=ON -DGGML_NATIVE=OFF -DGGML_OPENMP=ON \
        -DLLAMA_BUILD_APP=ON -DLLAMA_BUILD_COMMON=ON -DLLAMA_BUILD_EXAMPLES=ON \
        -DLLAMA_BUILD_IS_DEV=ON -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TESTS=ON \
        -DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_UI=ON
    cmake --build build-release -j $(sysctl -n hw.ncpu)

Key options:

- `GGML_CPU_REPACK=ON` — required (the streamed expert path repacks
  quantized expert slices into CPU backend-ready layout).
- `GGML_BACKEND_DL=ON` + `GGML_CPU_ALL_VARIANTS=ON` — builds the
  loadable backend plugins (`libggml-cpu-apple_m*.so`, `libggml-metal.so`)
  that the bundle needs.
- `GGML_METAL=ON` — Metal backend builds; the streamed path is
  CPU-validated (see limitations).

A clean out-of-tree build of this configuration takes ~2 minutes on the
reference machine (10 cores).

## Repository layout

- `llama.cpp/` — the pinned llama.cpp fork (23 project commits on top of
  upstream master, ending at `caea707b7`). The storage-backed expert
  streaming implementation lives here: `src/llama-expert-stream.cpp`,
  `src/llama-expert-stream-exec.cpp`, model-load virtualization, and the
  MoE routing/retrieval instrumentation.
- `runtime/release-9f/` — frozen release bundle (binary + dylibs +
  backend plugins, rpath rewritten to `@loader_path`, provenance in
  `runtime/release-9f/COMMIT`). Built from `caea707b7` by
  `tools/freeze_live_runtime.sh`. Using the bundle avoids building;
  building from source avoids trusting the bundle.
- `runtime/live/` — the pre-Phase-10 OpenClaw serving bundle (older
  commit `cad716035`). Retained for operational continuity; NOT the
  scientific release baseline. Do not use it for reproduction.
- `models/kimi-linear/` — model GGUF (gitignored; see Model setup).
- `tools/` — runner, verifier, analyzers, benchmark harness.
- `benchmarks/` — prompts, results, `STREAMING_RESULTS.md`.

## Model setup

- **Model**: `moonshotai/Kimi-Linear-48B-A3B-Instruct`
  (Hugging Face; repo sha `e1df551a447157d4658b573f9a695d57658590e9`).
- **Supported quantization**: Q4_K_M (`general.file_type = 15`). The
  storage layer is quantization-agnostic (per-expert byte ranges are
  pread from the GGUF), but Q4_K_M is the validated release
  configuration.
- **Acquisition**: the GGUF is not distributed by this project. Obtain a
  Q4_K_M conversion of the official safetensors (e.g., via llama.cpp's
  `convert_hf_to_gguf.py` + `llama-quantize`, or a community conversion
  with matching tensor layout), and place it at
  `models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`.
  Verify the checksum:
- **Size / checksum** (validated reference artifact):

      size:   30061058720 bytes (28.00 GiB)
      sha256: a1a7d865370652221f937163f7e94c99e1f114861335ba4f8666606843f1620f

- **Context / cache**: context 4096 is the benchmark convention
  (`CTX=4096`); the serving default is 65536 (KV is cheap — 7 attention
  layers ≈ 0.23 GB f16 at 8k). Expert cache 4096 MiB is the validated
  code-workload default; 8192 MiB for reasoning-heavy workloads.

## Run

### Server (OpenAI-compatible API)

    KIMI_STREAM_EXPERTS=naive \
    KIMI_EXPERT_CACHE_MB=4096 \
    KIMI_EXPERT_CACHE_MODE=zerocopy \
    KIMI_EXPERT_READ_WORKERS=4 \
    runtime/release-9f/bin/llama-server \
        -m models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf \
        -ngl 0 --no-mmap --ctx-size 65536 \
        --host 127.0.0.1 --port 18080 --parallel 1

### CLI (deterministic single-turn generation)

    KIMI_STREAM_EXPERTS=naive \
    KIMI_EXPERT_CACHE_MB=4096 \
    KIMI_EXPERT_CACHE_MODE=zerocopy \
    KIMI_EXPERT_READ_WORKERS=4 \
    runtime/release-9f/bin/llama-cli \
        -m models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf \
        -ngl 0 --no-mmap --ctx-size 4096 \
        -p "Write a one-line Python function that returns the sum of a list." \
        -n 64 --temp 0 --seed 7 \
        --no-display-prompt --no-conversation --single-turn

**The master switch**: `KIMI_STREAM_EXPERTS=naive` activates
storage-backed expert execution. **Without it, the runtime silently runs
the conventional fully-resident path** — same output, but no SSD paging
and ~28 GiB resident. Every run that is meant to exercise the streaming
architecture must set it.

### Configuration knobs

| Env var | Default | Meaning |
|---|---|---|
| `KIMI_STREAM_EXPERTS` | (unset) | `naive` = storage-backed streaming ON. Unset = conventional resident execution. |
| `KIMI_EXPERT_CACHE_MB` | 4096 | Expert-cache budget in MiB (0 = uncached). Validated: 4096 code, 8192 reasoning. |
| `KIMI_EXPERT_CACHE_MODE` | `zerocopy` | `zerocopy` = Phase 6B validated mode (hits feed `mul_mat_id` with 0 placement bytes); `placement` = legacy Phase 6. |
| `KIMI_EXPERT_READ_WORKERS` | 1 | Parallel expert-read workers. 4 = the Phase 9F/9G validated pipelined repack (positive control). |
| `CTX` | (library default) | Context size for the benchmark runner (4096 validated). |
| `KIMI_PHASE7_INSTR` | 0 | `1` = full Phase 7 instrumentation (mem.csv, cache_layers.csv). |
| `KIMI_STREAM_RETR_FILE` / `KIMI_STREAM_STATS_FILE` / `KIMI_TRACE_MOE_FILE` | — | Trace outputs (retrieval log, per-step stats, MoE routing). |

## Verify

`tools/phase10_verify.sh` runs a short deterministic streamed capture and
checks all five requirements (exit 0 = PASS):

1. model loads and the run completes;
2. storage-backed expert execution is active (retrieval trace emitted,
   pread present in stats);
3. routing/retrieval invariants hold (Phase 7 per-step invariant check,
   0 violations);
4. generated output is valid (no error markers, generation completes);
5. expected instrumentation is produced (retr.csv, stats.csv, moe.csv,
   act.bin, manifest.json, mem.csv, cache_layers.csv).

    tools/phase10_verify.sh            # defaults: 64 tokens, seed 7, 4 GiB cache
    KIMI_EXPERT_CACHE_MB=8192 tools/phase10_verify.sh /tmp/my-verify

## Benchmark (Phase 9 performance reproduction)

The frozen Phase 9G paired-bracket protocol is the only valid way to
compare configurations. One bracket = A-before → B → A-after,
contemporaneous, candidate placement randomized; paired speedup
`S_i = tok/s(B_i) / mean(tok/s(A1,i), A2,i)`; report the distribution of
S_i, not a point comparison. Archived-baseline comparisons are not valid
optimization gates.

    # positive control: B = W4 pipelined repack vs A = W1 frozen (10 brackets, ~30 min)
    CONFIG=coding-cap4 MODE=positive SEED=<s> tools/phase09g_run_brackets.sh \
        benchmarks/results/phase-09g/<session-dir>/positive 10 128

    # null protocol: B = sham A (same machinery/labels)
    CONFIG=coding-cap4 MODE=null SEED=<s> tools/phase09g_run_brackets.sh \
        benchmarks/results/phase-09g/<session-dir>/null 10 128

    # analysis (exit 0 = PASS)
    python3 tools/phase09g_analyze.py benchmarks/results/phase-09g/<session-dir>/positive

Configs: `coding-cap4` (4 GiB cache, coding prompt), `reasoning-cap8`
(8 GiB, reasoning prompt), `uncached` (cache 0). See TOOLS.md for the
full frozen protocol.

## Expected performance (reference machine, Q4_K_M, ctx 4096)

From the frozen Phase 8 ladder (in-session uncached control per
workload; absolute tok/s varies with thermal state — always compare
within a session):

- Decode throughput: 4.5–5.4 tok/s at the 4–8 GiB cache plateau
  (coding 5.39 tok/s at 4 GiB; reasoning 4.96 tok/s at 8 GiB); uncached
  ≈ 2.5–2.6 tok/s.
- Cache hit rate: 0.56–0.79 (4–8 GiB) on the coding/reasoning
  workloads.
- SSD traffic: ≈ 286–372 MB/token at 4 GiB, ≈ 177–210 MB/token at
  8 GiB, vs ≈ 850 MB/token uncached.
- Resident memory: ≈ 5.5–5.6 GB total at the recommended 4 GiB cache
  (baseline + cache); conventional resident load is ≈ 28 GiB.
- Prefill is slower than decode (cache is cold; every expert is a
  compulsory miss). Expect ~1.5–5 tok/s on prompt processing depending
  on prompt length.

## Known limitations

1. **CPU-only validated path.** `-ngl 0` is the release configuration.
   Metal builds and loads, but storage-backed expert execution is
   CPU-validated; GPU offload of the streamed path is not part of the
   Phase 10 release.
2. **Silent fallback.** Missing `KIMI_STREAM_EXPERTS=naive` silently
   selects conventional resident execution (same outputs). Always set it
   when the intent is streaming; `phase10_verify.sh` asserts the streamed
   path is actually active.
3. **Memory pressure above ~10 GiB cache.** On 24 GB, cache budgets
   ≥ 10 GiB push the machine into memory-pressure compression and
   compute slows; the practical plateau is 4–8 GiB.
4. **Thermal drift.** The reference machine is fanless; absolute tok/s
   drifts with temperature (up to ~17% observed). Never compare
   cross-session absolute numbers; use the bracketed protocol.
5. **OpenSSL dependency.** The bundle binaries link Homebrew
   `openssl@3` at `/opt/homebrew/opt/openssl@3/lib`. A machine without
   Homebrew OpenSSL must install it (or rebuild with a vendored SSL).
6. **Model weights are not redistributed** by this project; obtain the
   GGUF per Model setup and verify the checksum.
7. **Prefill cache bypass** (known Phase 9A finding): oversized prefill
   steps can exceed the per-layer zero-copy slot capacity and fall back
   to the legacy placement path; decode is unaffected.

## Troubleshooting

- **"no such file or directory" for the bundle binary** — the bundle is
  self-contained (`@loader_path`) but must be executed from anywhere;
  if you copied only `llama-server` without the dylibs/plugins, copy the
  whole `runtime/release-9f/bin/` directory.
- **Server runs but is slow / no pread in stats.csv** — check
  `KIMI_STREAM_EXPERTS=naive` is set (silent fallback, see limitations).
- **Model fails to load** — verify the GGUF path and sha256; only Q4_K_M
  is validated.
- **`phase10_verify.sh` fails the invariants check** — the Phase 7
  analyzer must report "invariants: PASS". If retrieval ranges differ,
  re-verify the model file checksum (a corrupted GGUF breaks byte-range
  retrieval).
- **Memory pressure / swap during benchmark** — lower
  `KIMI_EXPERT_CACHE_MB`; the 4–8 GiB plateau is the validated range.
- **OpenSSL errors at load** — `brew install openssl@3` (or rebuild with
  vendored SSL).

## Repository status / provenance

- Release baseline: llama.cpp `caea707b7d216c8685cf85db1a2682e26deb47d9`
  ("phase-09f: per-kind pipelined repack").
- Frozen bundle: `runtime/release-9f/` (COMMIT file inside).
- Prior scientific baseline (pre-Phase-9G): `runtime/live/`
  (`cad716035`), retained for operational continuity, not for
  reproduction.
- Reference results: `benchmarks/STREAMING_RESULTS.md`, Phase 8/9 reports
  in `progress/`.
