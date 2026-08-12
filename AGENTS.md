# AGENTS.md

## Project

Build a routing-aware, SSD-backed expert streaming implementation for
`Kimi-Linear-48B-A3B-Instruct` on Apple Silicon.

The project adapts the memory-management strategy demonstrated by
`FareedKhan-dev/kimi-k3-in-c` to a smaller MoE model intended for
productive local inference.

The presumptive implementation base is `llama.cpp` / `ggml` with its
Metal backend.

Do not build a new inference engine unless the roadmap and measured
evidence eventually justify doing so.

Read this file, `ROADMAP.md`, and `TOOLS.md` before beginning work.

---

## Reference Project

This project is explicitly inspired by:

`FareedKhan-dev/kimi-k3-in-c`

https://github.com/FareedKhan-dev/kimi-k3-in-c

Study that implementation before designing or modifying the streaming
architecture.

The relevant design pattern is:

    model weights
          ↓
    existing MoE routing
          ↓
    required experts
          ↓
    bounded expert cache
       ↙          ↘
     hit          miss
                    ↓
              backing storage
                    ↓
              expert loaded
          ↓
      computation

In particular, study how `kimi-k3-in-c` handles:

- model configuration;
- tensor/checkpoint loading;
- locating individual experts in storage;
- routed-expert caching;
- cache eviction;
- batch expert prefetch;
- dense-trunk handling and streaming;
- explicit memory budgeting;
- inference-loop integration;
- benchmarking;
- correctness verification.

Relevant components include:

    include/k3/
        k3.h
        k3_cfg.h

    src/
        core/k3_ops.c
        io/k3_st.c
        io/k3_load.c
        io/k3_trunk.c
        cache/k3_cache.c
        model/k3_bind.c
        tokenizer/k3_tok.h
        cli/k3_run.c

    tools/
    benchmarks/
    tests/

Do not copy this architecture mechanically.

`kimi-k3-in-c` and this project solve different problems:

    kimi-k3-in-c
        → purpose-built Kimi K3 runtime
        → extremely large model
        → severe memory constraint
        → make execution possible

    this project
        → established llama.cpp / ggml runtime
        → Kimi-Linear-48B-A3B-Instruct
        → 24GB Apple Silicon target
        → make local inference practical

Reuse the architectural ideas that apply, especially:

- expert paging;
- bounded caching;
- storage indexing;
- prefetching;
- explicit memory budgeting.

Reuse existing `llama.cpp`, `ggml`, and Metal functionality wherever it
already solves the problem.

The objective is not to port `kimi-k3-in-c`.

The objective is to adapt its memory architecture to Kimi Linear using
an established native inference runtime.

---

## Architecture Principle

The project is not a new Kimi Linear implementation.

The intended division of responsibility is:

    Reference architecture:
        kimi-k3-in-c

    Implementation base:
        llama.cpp / ggml / Metal

    Target model:
        Kimi-Linear-48B-A3B-Instruct

    Target hardware:
        24GB Apple Silicon

    Objective:
        productive inference under a bounded memory budget

Reuse the established runtime for:

- Kimi Linear model architecture;
- KDA and attention;
- MoE routing;
- tensor operations;
- GGUF handling;
- tokenization and generation;
- Metal execution.

Modify only the storage and residency behavior necessary to make routed
experts pageable.

Conceptually:

    existing Kimi Linear runtime
              ↓
         existing router
              ↓
        selected experts
              ↓
      paging / cache layer
          ↙             ↘
      resident           SSD
          ↓
       ggml / Metal

Keep project-specific changes isolated from upstream runtime
functionality where practical.

---

## Operating Rules

Work on one roadmap phase at a time.

Do not proceed to the next phase until:

1. the current phase's acceptance criteria pass;
2. required tests and measurements have been run;
3. `progress/phase-XX-report.md` has been written or updated.

Do not begin future roadmap work opportunistically.

Prefer the smallest change that satisfies the current phase.

Preserve established runtime behavior wherever possible.

Do not:

- change model mathematics;
- change quantization unless the current roadmap phase requires it;
- rewrite working `llama.cpp`, `ggml`, or Metal functionality unnecessarily;
- build custom kernels without measured need and roadmap authorization;
- optimize before establishing correctness;
- claim performance or memory improvements without measurements;
- hide failures behind silent fallbacks;
- proceed on assumptions that can reasonably be verified from code,
  tests, documentation, or measurements.

---

## Before Coding

For the current phase:

1. Read the relevant section of `ROADMAP.md`.
2. Read the most recent relevant `progress/phase-XX-report.md` files.
3. Read `TOOLS.md`.
4. Inspect the existing implementation.
5. Verify important architectural assumptions against the actual code.
6. Identify the smallest implementation surface required.
7. Define how the phase's acceptance criteria will be tested.

Do not assume a planned implementation detail is correct merely because
it appears in the roadmap.

The roadmap is a plan.

The code and measurements determine reality.

---

## During Work

Keep changes scoped to the current phase.

Prefer modifying an existing integration point over creating a parallel
implementation.

Run relevant tests after meaningful changes.

Preserve a usable reference path for comparison.

When an approach fails:

1. preserve the useful evidence;
2. determine why it failed;
3. record the failure in the phase report;
4. choose the next approach based on that evidence.

Do not silently replace failed approaches and erase their history.

For long-running operations, write output and measurements to durable
files rather than relying on session context.

---

## Evidence

Treat measurements and reproducible tests as authoritative.

Performance claims require benchmarks.

Memory claims require measurements.

Correctness claims require comparison against an established reference
where practical.

Record enough environment information to reproduce significant results.

Do not treat successful text generation alone as proof of correctness.

---

## Phase Completion

Every phase MUST end with:

    progress/phase-XX-report.md

A phase is not complete until this report exists.

Use the following structure:

    # Phase XX Report

    ## Status

    PASS / PARTIAL / FAIL

    ## Objective

    What this phase attempted.

    ## Changes

    Files created or modified and why.

    ## Results

    Measured results and test outcomes.

    ## Problems

    Failures, unexpected behavior, limitations, or unresolved questions.

    ## Decisions

    Important architectural or implementation decisions made.

    ## Next Phase

    Information the next phase needs.

    ## Reproduction

    Exact commands required to reproduce important tests or benchmarks.

---

## Phase Transitions

A `PASS` permits work to proceed to the next roadmap phase.

A `PARTIAL` or `FAIL` does not automatically permit progression.

If acceptance criteria cannot be met, document why and stop unless the
roadmap explicitly provides an alternate path.

Later phases must use prior phase reports as project state rather than
assuming the original roadmap expectations proved correct.

---

## STOP Point

After the roadmap's Memory Ladder phase, STOP.

Produce the required streaming results and Phase 8 report.

Do not proceed automatically into:

- advanced streaming optimization;
- MXFP4;
- custom Metal kernels;
- specialized native operations;
- direct I/O;
- a new inference runtime.

Further work must be selected from the measured results of the initial
streaming experiment.
