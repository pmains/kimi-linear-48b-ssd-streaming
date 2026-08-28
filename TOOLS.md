# TOOLS.md

# Tool Policy

Use the simplest tool that provides reliable, reproducible evidence.

Prefer existing runtime functionality over new implementation.

---

## llama.cpp / ggml

`llama.cpp` / `ggml` is the presumptive implementation base for this
project.

Use it for:

- Kimi Linear model execution;
- GGUF loading and tensor handling;
- MoE routing and expert execution;
- quantized tensor operations;
- inference and generation;
- Apple Silicon execution through Metal.

Before modifying runtime behavior, inspect the existing implementation
and identify the smallest integration point that satisfies the current
roadmap phase.

Prefer localized, upstream-compatible changes over parallel
implementations.

Do not rewrite functionality already provided by `llama.cpp` or `ggml`
without measured evidence that it prevents the required behavior.

Record the exact upstream commit used for significant experiments.

---

## Metal

Use the existing `llama.cpp` / `ggml` Metal backend for Apple Silicon
acceleration.

Do not write custom Metal kernels during the initial streaming
experiment.

Custom Metal work is appropriate only when:

1. the roadmap authorizes it;
2. profiling identifies a specific bottleneck;
3. existing ggml/Metal operations cannot adequately address it.

---

## kimi-k3-in-c

Use:

    FareedKhan-dev/kimi-k3-in-c

as the architectural reference for expert paging and memory management.

Relevant areas include:

- expert storage and lookup;
- bounded expert caching;
- cache eviction;
- batch prefetch;
- trunk streaming;
- explicit memory budgeting;
- inference-loop integration;
- correctness testing;
- memory/performance benchmarking.

Do not treat `kimi-k3-in-c` as the implementation base.

Do not port its model kernels, tokenizer, or complete runtime when
equivalent functionality already exists in `llama.cpp` / `ggml`.

Use it to understand the memory architecture we are adapting.

Record the exact repository commit when implementation details from it
influence project decisions.

---

## Python

Use Python for supporting analysis and tooling, including:

- checkpoint inspection;
- tensor inventories;
- GGUF/model analysis where appropriate;
- benchmark analysis;
- correctness comparisons;
- experiment orchestration;
- structured result generation.

Prefer reproducible scripts committed under `tools/` or `benchmarks/`
over one-off interactive commands when results matter to later phases.

Do not implement the primary inference or expert-streaming runtime in
Python unless a roadmap phase explicitly calls for an isolated prototype.

---

## MLX

MLX is a reference tool, not the presumptive implementation runtime.

Use MLX where useful for:

- establishing known-working Kimi Linear behavior;
- correctness comparisons;
- inspecting Apple Silicon model behavior;
- validating outputs independently of the modified runtime.

Do not build a parallel MLX streaming implementation unless the roadmap
changes the selected implementation strategy.

Record the exact MLX version and model revision when used for reference
results.

---

## Hugging Face

Use the official Kimi Linear model repository as the authoritative source
for:

- checkpoint files;
- model configuration;
- tokenizer/configuration metadata;
- architecture metadata supplied with the model.

Target model:

    moonshotai/Kimi-Linear-48B-A3B-Instruct

Record exact model/checkpoint revisions used in significant experiments.

Do not silently substitute:

- another checkpoint;
- another model revision;
- another quantization;
- another conversion.

Any substitution must be documented in the relevant phase report.

---

## C / C++

C and C++ are normal implementation languages for this project because
the primary runtime is native.

Use them for modifications within the selected `llama.cpp` / `ggml`
integration surface.

Prefer:

    existing runtime abstraction
        ↓
    minimal modification
        ↓
    tests
        ↓
    measurement

over creating new subsystems unnecessarily.

Do not build a standalone inference runtime merely because native code
might theoretically be faster.

---

## Shell

Use shell commands for:

- repository inspection;
- builds;
- file operations;
- environment inspection;
- running inference;
- running tests;
- running benchmarks;
- profiling and measurement.

Long-running operations should write logs and results to durable files.

Do not repeatedly poll long-running processes when they can run
independently and leave artifacts for later inspection.

Preserve commands needed to reproduce important results.

---

## Git

Use Git to preserve experimental state and make changes auditable.

Record upstream commits for:

- `llama.cpp`;
- `kimi-k3-in-c`;
- other implementation dependencies when relevant.

Commit at meaningful phase boundaries.

Recommended commit format:

    phase-01: validate runtime architecture
    phase-02: inventory model memory
    phase-03: implement expert addressability
    phase-04: implement uncached expert streaming

Do not combine unrelated roadmap phases in one commit.

Avoid large unrelated refactors that make comparison with upstream
difficult.

---

## Benchmarking and Profiling

Performance and memory claims require recorded measurements.

Store machine-readable benchmark results under:

    benchmarks/results/

Prefer JSON or CSV for data used in comparisons.

Record enough environment information to reproduce significant results,
including where relevant:

- hardware;
- total unified memory;
- macOS version;
- `llama.cpp` commit;
- compiler and build configuration;
- Metal/backend configuration;
- model/checkpoint revision;
- GGUF/quantization;
- context size;
- cache/memory budget;
- relevant runtime arguments.

Measure before optimizing.

Use profiling to identify bottlenecks rather than assuming where they
occur.

---

## Deferred Unless Authorized by the Roadmap

Do not introduce during the initial streaming experiment:

- new quantization formats;
- MXFP4 conversion;
- custom Metal kernels;
- specialized MXFP4 kernels;
- direct I/O;
- major ggml architectural changes;
- a standalone inference runtime;
- an alternative primary inference framework.

These are not prohibited permanently.

They are deferred until earlier phases establish correctness and
measurements demonstrate whether they are necessary.

---

## Tool Selection Principle

Use each component for the problem it already solves:

    Kimi model/checkpoint     → Hugging Face
    native inference          → llama.cpp / ggml
    Apple GPU execution       → existing Metal backend
    paging architecture       → study kimi-k3-in-c
    reference validation      → MLX where useful
    analysis/tooling          → Python
    implementation            → C/C++ within llama.cpp
    experiments/builds        → Shell
    history/reproducibility   → Git
    decisions                 → progress/phase-XX-report.md

Do not introduce another layer unless the existing stack cannot satisfy
a measured requirement.

---

## Serving Kimi Linear to OpenClaw Agents (dev/live split)

The streamed model is registered in OpenClaw as provider `kimi-local`
(model `kimi-local/kimi-linear-48b`), assigned to agents: `kimi`,
`poliscopic`, `aristotle` (each with cloud fallbacks).

### Live runtime (frozen)

    runtime/live/          self-contained bundle (binary + dylibs + backend .so,
                           rpath rewritten to @loader_path)
    runtime/live/COMMIT    pinned llama.cpp revision serving live
    tools/serve_kimi_local.sh [start|stop|status]
        KIMI_CACHE_MB=4096   expert cache budget (Phase 8 default)
        KIMI_PORT=18080      server port
        KIMI_CTX=8192        context size

The live server runs ONLY from `runtime/live/bin/llama-server`. Dev
rebuilds of `llama.cpp/build-metal` can never change what agents are
served.

### Dev tree

    llama.cpp/             dev checkout — rebuild freely for optimization work

### Promotion workflow

1. Develop + validate in `llama.cpp/` (A/B against live with
   `KIMI_BIN=llama.cpp/build-metal/bin/llama-server KIMI_PORT=18081`).
2. Rebuild `build-metal`, run `tools/freeze_live_runtime.sh` (copies
   binary + dylibs + .so plugins, rewrites rpath, records COMMIT).
3. Restart the server: `tools/serve_kimi_local.sh restart`.
4. Verify: `openclaw infer model run --model kimi-local/kimi-linear-48b`.

---

## Phase 9G Benchmark Protocol (frozen 2026-08-28)

Frozen after the harness pilot PASS (`progress/phase-09g-harness-pilot-report.md`).
Do NOT change this protocol without a new methodology phase. Performance
and memory claims after Phase 9F must be evaluated with this protocol;
archived-baseline comparisons are NOT valid optimization gates.

### Experimental unit and estimator

One bracket = three labeled runs executed back-to-back in one session:

    A-before → B → A-after

- A = frozen workers=1 control (`KIMI_EXPERT_READ_WORKERS=1`, the Phase
  8/9D/9F byte-identical path).
- B = candidate: `MODE=positive` → `B_WORKERS` (default 4, the W4
  pipelined repack); `MODE=null` → sham B (the middle slot runs the A
  config under identical machinery/labels).
- Paired speedup per bracket:

      S_i = tok/s(B_i) / mean(tok/s(A_before,i), tok/s(A_after,i))

  Report the DISTRIBUTION of S_i (median, IQR, bootstrap 95% CI) plus
  the bracketed A spread (mandatory noise disclosure). A gate passes
  only if the bootstrap CI of the median S_i excludes the threshold AND
  the A spread is reported.

### Randomization (mandatory)

Within each bracket the three labeled runs execute in a seeded uniform
random permutation (candidate placement/order randomized across
brackets). `SEED` env (default epoch-s) is recorded in `harness.json`;
pass `SEED` explicitly to reproduce an order set. The analyzer verifies
the recorded order against driver-log mtimes.

### Usage

    # null protocol (B ≡ A): 10 brackets, ~30 min
    CONFIG=coding-cap4 MODE=null SEED=<s> tools/phase09g_run_brackets.sh \
        benchmarks/results/phase-09g/<session-dir>/null 10 128

    # positive control (W4 vs W1): ~10 brackets
    CONFIG=coding-cap4 MODE=positive SEED=<s> tools/phase09g_run_brackets.sh \
        benchmarks/results/phase-09g/<session-dir>/positive 10 128

    # analysis + harness validation (exit 0 = PASS; null diagnostics are
    # reported, not exit gates — they are judged in the phase report)
    python3 tools/phase09g_analyze.py benchmarks/results/phase-09g/<session-dir>/null
    python3 tools/phase09g_analyze.py benchmarks/results/phase-09g/<session-dir>/positive

Env: `CONFIG` coding-cap4|reasoning-cap8|uncached; `A_WORKERS`=1;
`B_WORKERS`=4; `CTX`=4096; `PILOT`=true only for pilot metadata/summary
filename. Every run retains the full Phase 4/7/9 artifact set; env
covariates (memory pressure, vm_stat, loadavg, top CPU, thermal, live
llama-server health) are snapshotted before/after every bracket.

### Scheduling rule

Benchmarks share this MacBook Air with the live server (port 18080,
serving kimi/poliscopic/aristotle). Run full-phase brackets only in idle
windows (live server healthy + low loadavg); record live-server load as
a covariate. Prefer 3 shorter sessions separated in time over one
marathon — between-session variation (including variation in the
response to parallel I/O) is part of the phenomenon.

### Analysis conventions

- Robust summaries (median/IQR, bootstrap CI). Swing brackets (e.g.,
  pilot bracket 4's 18.96% A spread) are data — do not delete them
  unless a documented external event invalidated the run.
- Statistical outcomes (null centering, false-positive rate) are phase
  acceptance criteria evaluated in `progress/phase-09g-report.md`, not
  harness exit gates.
