# Progress Reports

This directory is the persistent record of what actually happened during
each roadmap phase.

`ROADMAP.md` describes the plan.

The files in `progress/` describe the observed state of the project.

Later phases must use these reports as project state rather than assuming
that earlier roadmap expectations proved correct.

---

## Required Report

Every roadmap phase must produce:

    progress/phase-XX-report.md

Examples:

    phase-01-report.md
    phase-02-report.md
    phase-03-report.md

A phase is not complete until its report has been written or updated.

Reports must describe what actually happened, including failures,
unexpected results, and deviations from the roadmap.

Do not rewrite history to match the original plan.

---

## Report Template

Use the following structure.

# Phase XX Report — [Phase Name]

## Status

`PASS`, `PARTIAL`, or `FAIL`

Include a brief explanation when status is not `PASS`.

## Objective

State what this phase attempted to establish or build.

Keep this concise and consistent with `ROADMAP.md`.

## Environment

Record information needed to interpret or reproduce the work where
relevant, such as:

- hardware;
- operating system;
- runtime/library versions;
- repository commits;
- model/checkpoint revision;
- quantization;
- build configuration;
- important runtime arguments.

Do not repeat irrelevant environment information.

## Changes

Document the implementation work performed.

Include:

- files created;
- files modified;
- important functions/classes/components changed;
- why each significant change was necessary.

Do not use this section as a raw commit log.

## Tests

Document tests performed and their results.

For each important test, record:

- what was tested;
- expected result;
- observed result;
- pass/fail status.

Reference machine-readable results or logs when available.

## Measurements

Record quantitative results relevant to the phase.

Examples include:

- model size;
- resident memory;
- peak memory;
- cache hit rate;
- SSD traffic;
- latency;
- tokens/sec;
- correctness differences.

Use tables where they improve comparison.

Do not make performance or memory claims without measurements.

## Results

Summarize what the evidence established.

Distinguish between:

- demonstrated facts;
- reasonable interpretations;
- unresolved hypotheses.

Do not treat successful text generation alone as proof of correctness.

## Problems

Record:

- failures;
- unexpected behavior;
- limitations;
- unresolved bugs;
- assumptions that proved incorrect;
- measurements that could not be obtained.

Failed approaches are useful project knowledge and should not be erased.

## Decisions

Record architectural or implementation decisions made during the phase.

For significant decisions, include:

    Decision:
    Reason:
    Evidence:
    Consequence:

Only record decisions that affect later work.

## Open Questions

List unresolved questions that materially affect later phases.

Do not fill this section with speculative future improvements.

## Next Phase

State what the next phase needs to know from this phase.

Include:

- established constraints;
- relevant interfaces;
- important measurements;
- known hazards;
- unresolved dependencies.

This section should allow a new agent session to resume the project
without reconstructing the previous phase from conversation history.

## Reproduction

Provide exact commands needed to reproduce significant:

- builds;
- tests;
- benchmarks;
- model inspection;
- generated artifacts.

Prefer commands that can be run from the repository root.

Where results depend on external repositories or models, record the
specific revision used.

## Artifacts

List important artifacts produced by the phase.

Examples:

    benchmarks/results/phase-06-8gb.json
    benchmarks/model_inventory.json
    tests/test_streaming_correctness.cpp

Do not list routine or irrelevant files.

---

## Status Rules

### PASS

Use `PASS` only when the phase's acceptance criteria in `ROADMAP.md`
have been satisfied with evidence.

A `PASS` normally permits progression to the next phase.

### PARTIAL

Use `PARTIAL` when meaningful progress was made but one or more
acceptance criteria remain unmet.

Document exactly what remains unresolved.

Do not automatically proceed to the next phase.

### FAIL

Use `FAIL` when the phase's objective or acceptance criteria could not
be achieved with the attempted approach.

A failed phase is not a failed project.

Document the evidence and stop unless `ROADMAP.md` explicitly provides
an alternate path.

---

## Reporting Principles

### Record evidence, not narrative

Reports are engineering records, not diaries.

Prefer:

    8GB cache: 7.3 tok/s, 84% hit rate

over:

    Performance improved significantly with the larger cache.

### Preserve failures

Do not delete failed experiments from the report merely because a later
approach worked.

Record enough information to prevent later phases from repeating the
same failure.

### Separate observation from interpretation

Prefer:

    Observed:
    Resident memory peaked at 11.4GB.

    Interpretation:
    The 8GB cache budget does not imply an 8GB process footprint because
    additional runtime allocations remain resident.

rather than presenting the interpretation as a measured fact.

### Keep reports concise

Document information that affects reproducibility, correctness,
architecture, performance, or later phases.

Do not dump complete logs into reports.

Store large logs and machine-readable benchmark results elsewhere and
reference them from the report.

---

## Project State

When beginning a new phase, read:

1. `AGENTS.md`;
2. the current phase in `ROADMAP.md`;
3. `TOOLS.md`;
4. this file;
5. the immediately preceding phase report;
6. any earlier report specifically relevant to the current work.

Do not load every progress report by default.

Use earlier reports when the current phase depends on their decisions,
measurements, or unresolved problems.
