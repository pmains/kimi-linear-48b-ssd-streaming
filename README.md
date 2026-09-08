# Kimi Linear SSD-Backed Expert Streaming

Run **Kimi Linear 48B** on memory-constrained Apple Silicon by streaming sparse MoE experts from SSD instead of keeping the full expert set resident in memory.

This project modifies [llama.cpp](https://github.com/ggml-org/llama.cpp) to run [`moonshotai/Kimi-Linear-48B-A3B-Instruct`](https://huggingface.co/moonshotai/Kimi-Linear-48B-A3B-Instruct) using:

* SSD-backed sparse-expert streaming
* MXFP4 expert weights
* bounded in-memory expert caching
* parallel expert reads
* Metal execution on Apple Silicon
* long-context serving through `llama-server`

The current validated deployment runs on a **24 GB Apple M5 Mac**.

## What this achieves

Kimi Linear is a sparse Mixture-of-Experts model: roughly **48B total parameters** but only a small subset of experts is active for each token.

Instead of requiring the entire expert collection to fit in fast memory, this runtime treats model execution as a memory-hierarchy problem:

```text
                         Kimi Linear 48B
                               │
                         MoE router selects
                         active experts
                               │
                  ┌────────────┴────────────┐
                  │                         │
             cache hit                 cache miss
                  │                         │
           unified memory                  SSD
                  │                         │
                  └────────────┬────────────┘
                               │
                       streamed MXFP4
                               │
                             Metal
```

On the current 24 GB reference system, the validated configuration provides:

| Capability                             |           Current result |
| -------------------------------------- | -----------------------: |
| Expert representation                  |                    MXFP4 |
| Expert cache                           |                    8 GiB |
| Expert read workers                    |                        4 |
| Ordinary-context decode                |              ~9–11 tok/s |
| SSD traffic at current operating point |        ~140–170 MB/token |
| Expert-cache hit rate                  |                  ~76–81% |
| Context contract                       |           262,144 tokens |
| Largest real-agent test                | 140,708 assembled tokens |
| Deepest verified retrieval             |            token 136,190 |
| Peak llama-server RSS in that test     |                10.14 GiB |

The goal is not to outperform a multi-GPU inference server.

The goal is to make **large sparse models useful on hardware with far less accelerator memory than the total model would conventionally require**.

---

## Quick Start

### Requirements

Validated reference platform:

* Apple Silicon Mac
* 24 GB unified memory or more recommended
* macOS
* Xcode Command Line Tools
* CMake
* Homebrew `openssl@3`
* sufficient SSD capacity for the model and runtime

Install the build dependencies:

```bash
xcode-select --install
brew install cmake openssl@3
```

### 1. Clone

```bash
git clone https://github.com/pmains/kimi-linear-48b-ssd-streaming
cd kimi-linear-48b-ssd-streaming
```

### 2. Build the modified llama.cpp runtime

```bash
cd llama.cpp

cmake -B build-release \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_ACCELERATE=ON \
  -DGGML_BACKEND_DL=ON \
  -DGGML_BLAS=ON \
  -DGGML_BLAS_VENDOR=Apple \
  -DGGML_CPU=ON \
  -DGGML_CPU_ALL_VARIANTS=ON \
  -DGGML_CPU_REPACK=ON \
  -DGGML_METAL=ON \
  -DGGML_NATIVE=OFF \
  -DLLAMA_BUILD_APP=ON \
  -DLLAMA_BUILD_COMMON=ON \
  -DLLAMA_BUILD_SERVER=ON \
  -DLLAMA_BUILD_TOOLS=ON

cmake --build build-release -j "$(sysctl -n hw.ncpu)"
```

### 3. Obtain the model

The model weights are **not distributed with this repository**.

The current runtime uses an MXFP4 MoE GGUF derived from:

```text
moonshotai/Kimi-Linear-48B-A3B-Instruct
```

Expected artifact:

```text
models/kimi-linear/
  moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf
```

Before the current runtime is released for third-party reproduction, this section will contain:

```text
exact conversion procedure
exact artifact size
SHA-256 checksum
source model revision
```

Do not substitute the older Q4_K_M artifact when reproducing current performance results.

### 4. Run

The current validated operating point is:

```bash
export KIMI_STREAM_EXPERTS=naive
export KIMI_EXPERT_CACHE_MB=8192
export KIMI_EXPERT_CACHE_MODE=zerocopy
export KIMI_EXPERT_READ_WORKERS=4
export KIMI_STREAM_METAL_STAGE=1
export KIMI_STREAM_E2_DIRECT_PLACE=1
```

Start `llama-server`:

```bash
runtime/live/bin/llama-server \
  -m models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
  -ngl 999 \
  --no-mmap \
  --ctx-size 262144 \
  --host 127.0.0.1 \
  --port 18080 \
  --parallel 1
```

Check health:

```bash
curl http://127.0.0.1:18080/health
```

Then make an OpenAI-compatible request:

```bash
curl http://127.0.0.1:18080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kimi-linear-48b",
    "messages": [
      {"role": "user", "content": "Reply with exactly: OK"}
    ],
    "temperature": 0,
    "max_tokens": 8
  }'
```

### 5. Verify the streamed path

A successful response alone is not sufficient: the runtime can fall back to a conventional path if streaming is not enabled correctly.

The release verifier checks that:

1. the model loads;
2. SSD-backed expert retrieval is active;
3. routing/retrieval invariants hold;
4. Metal execution completes without numerical errors;
5. the configured cache and read-worker settings are active;
6. generated output is valid.

```bash
tools/verify_current_runtime.sh
```

A clean run should exit `0`.

> `verify_current_runtime.sh` should be treated as part of the release contract. The current internal qualification suite must be reduced to a clean third-party verifier before the next public release.

---

## How it works

### Sparse experts

Kimi Linear is a sparse MoE model. For each token, the router activates only a subset of the model's experts.

A conventional implementation can still require the complete expert collection to remain resident because any expert may be selected.

This project virtualizes that expert storage.

```text
GGUF expert weights
       │
       ▼
      SSD
       │
       │ pread on miss
       ▼
bounded expert cache
       │
       ▼
active expert tensors
       │
       ▼
Metal computation
```

Frequently selected experts remain in the cache. Cache misses are fetched from SSD and placed into the execution path on demand.

### Why MXFP4?

The original implementation used Q4_K_M experts.

A later controlled evaluation tested MXFP4 as the streamed representation. On the uncached workload, MXFP4 approximately doubled decode throughput while producing only a small measured perplexity change:

| Metric     |      Q4_K_M |       MXFP4 |
| ---------- | ----------: | ----------: |
| Decode     | ~2.45 tok/s | ~4.69 tok/s |
| Perplexity |      6.6681 |      6.7596 |

Median measured speed ratio:

```text
MXFP4 / Q4_K_M = 1.871×
95% CI = 1.468–2.077
```

Measured perplexity increase was approximately **1.37%**.

MXFP4 subsequently became the expert representation used by the current deployment.

### Expert cache

The present operating point uses an **8192 MiB expert cache** with four parallel read workers.

In live qualification through the agent serving path, this produced approximately:

```text
decode:       9.0–11.1 tok/s
cache hit:    76–81%
SSD traffic:  140–170 MB/token
RSS:          ~11–12 GB
```

For comparison, an earlier 4 GiB / single-reader configuration produced approximately:

```text
decode:       3.0–3.3 tok/s
cache hit:    ~60%
SSD traffic:  ~300 MB/token
```

The larger cache therefore buys throughput by reducing expert misses and SSD traffic.

---

## Long context

The current server is configured for a **262,144-token context window**.

This is a capacity result, not a claim that cold 256K prompts are interactive.

### Qualification

Direct 256K feasibility testing admitted a 145,824-token prompt and correctly retrieved a target beyond token 137,000.

The context setting was then promoted through the actual serving stack and tested through real agent prompt assembly.

The deepest completed test assembled:

```text
total prompt:       140,708 tokens
retrieval target:   token 136,190
resolved context:   262,144
result:             exact retrieval
```

### The cost of depth

Long-context performance degrades with depth.

Representative measurements:

| Context / workload                 |  Throughput |
| ---------------------------------- | ----------: |
| ordinary decode                    | ~9–11 tok/s |
| ~95K decode                        |  5.35 tok/s |
| ~146K decode                       |  3.61 tok/s |
| 95K prefill                        | 33.95 tok/s |
| ~140K real-agent new-token prefill | 26.03 tok/s |

The 140K real-agent qualification required roughly **82 minutes of cold prefill**.

So:

> **256K is currently a demonstrated capacity ceiling, not an interactive cold-prompt target.**

The next systems problem is prefix/state reuse.

A persistent session should eventually behave like:

```text
150K already processed
        +
  1K new tokens
        │
        ▼
reuse prior state
        │
        ▼
process ~1K suffix
```

rather than:

```text
reprocess all 151K tokens
```

This is the current high-priority optimization target.

---

## Reference hardware

Current validated machine:

```text
Apple M5
24 GB unified memory
10 CPU cores
Apple Metal GPU
NVMe SSD
macOS
```

The architecture is intended to generalize to larger memory hierarchies:

```text
Apple Silicon:
unified memory ↔ SSD

workstation:
GPU VRAM ↔ system RAM ↔ SSD

server:
GPU HBM ↔ large host RAM ↔ NVMe
```

The underlying principle is the same: **keep the hottest working set in the fastest memory and make the larger sparse model available through slower tiers.**

---

## What is actually modified?

This project is based on llama.cpp / ggml but adds model-specific infrastructure for storage-backed sparse execution, including work in:

```text
src/llama-expert-stream.cpp
src/llama-expert-stream-exec.cpp
```

and associated changes for:

* expert-storage virtualization
* routed-expert retrieval
* bounded expert caching
* parallel expert reads
* quantized expert repacking
* zero-copy/direct placement
* streamed Metal execution
* instrumentation and correctness validation

This is **not stock upstream llama.cpp behavior**.

---

## Validation

This project has been developed with explicit correctness and performance gates rather than throughput measurements alone.

Validation has included:

* routing/retrieval invariant checks
* cache accounting
* SSD traffic measurement
* randomized paired performance tests
* perplexity comparison
* CPU/Metal numerical comparison
* first-divergence localization
* sustained-generation tests
* restart/service qualification
* long-context retrieval
* real agent-path qualification
* memory-pressure monitoring
* fallback and failure-boundary testing

Raw and summarized evidence lives under:

```text
benchmarks/
progress/
service-progress/
```

The detailed development history is intentionally kept out of the main README. Those directories contain the experiment protocols and failure analyses behind the current configuration.

---

## Historical Q4_K_M baseline

The first reproducible release of this project used:

```text
Kimi Linear 48B
Q4_K_M experts
CPU execution
SSD-backed streaming
4–8 GiB cache
```

That work established the core result: the expert set could be virtualized onto SSD and executed from a bounded memory footprint.

It remains an important scientific baseline and should remain reproducible.

It is **not the current deployment configuration**.

See:

```text
docs/PHASE10_Q4_BASELINE.md
progress/
benchmarks/
```

for the original pinned commit, GGUF checksum, benchmark methodology and reproduction instructions.

---

## Current limitations

### Long cold prefill

Large context fits, but cold prefill at 100K+ tokens is slow. Prefix/state reuse is the primary current optimization target.

### Single active inference workload

The 24 GB reference machine is currently operated as a single inference stream. Multi-session concurrency is not a validated target on this hardware.

### Apple Silicon

The present implementation has been developed and qualified on Apple Silicon and Metal. Other accelerators are not yet release-qualified.

### Model specificity

Kimi Linear is the current target. The broader architecture should apply to other sparse MoE models, but those models require their own implementation and qualification.

### Custom MXFP4 Metal path

The current MXFP4 streamed Metal implementation includes project-specific llama.cpp changes and is not equivalent to a stock upstream configuration.

### Model distribution

The model weights are not included in this repository.

---

## Roadmap

The immediate priority is **incremental prefix/state reuse**.

The project has already demonstrated that a large sparse model can fit and operate at substantial context depth on a 24 GB machine.

The next question is:

> Can persistent long-context inference scale mainly with the newly appended tokens rather than repeatedly paying the cost of the entire accumulated prompt?

Beyond that, the same architecture can be explored on larger-memory machines and additional sparse MoE models.

See `ROADMAP.md` and `SERVICE-ROADMAP.md` for the engineering roadmap.

---

## Project philosophy

Large-model inference is usually framed as:

> How much accelerator memory is required to hold the model?

This project asks a different question:

> **What if sparse-model inference is treated as a memory-hierarchy and scheduling problem instead?**

For workloads that do not require hyperscale concurrency, trading some throughput for dramatically lower memory requirements may enable capable local and sovereign inference on hardware that would otherwise be excluded from running these models.

Kimi Linear 48B is the first test of that approach.

---

## Reproducibility

Published performance claims should always identify:

* runtime commit;
* model artifact and checksum;
* quantization;
* cache size;
* expert-read worker count;
* Metal/CPU path;
* context depth;
* cold vs warm state;
* reference hardware.

Do not compare absolute throughput across unrelated sessions without accounting for thermal state and cache state.

Historical experiments and their exact protocols are retained rather than rewritten to match later results.

---

## License and upstream

This project builds on [llama.cpp](https://github.com/ggml-org/llama.cpp) and uses [`moonshotai/Kimi-Linear-48B-A3B-Instruct`](https://huggingface.co/moonshotai/Kimi-Linear-48B-A3B-Instruct).

See the repository license and upstream project/model licenses for their respective terms.
