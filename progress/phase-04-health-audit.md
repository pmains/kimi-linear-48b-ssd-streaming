# Phase 04 — Project-Health Audit

Status: COMPLETE (2026-08-13). Companion to `phase-04-report.md` and
`phase-04-modifications.md`.

Trigger: before further row-13 debugging, verify the repository still
documents exactly what was built, which code produced which trace, and
how to reproduce the current failure.

## Findings

### 1. ROADMAP.md vs reality — reconciled

Phase 4 was `PARTIAL` in the report but the roadmap itself carried no
status annotation. Added a "Status vs Plan" table to the Phase 4
section with evidence pointers:

- 4A.1 refactor equivalence: PASS
- 4A.2 retrieval equivalence: PASS (36,288/36,288 byte-exact)
- 4A.2 numerical equivalence: OPEN/FAIL
- 4B latency decomposition: PARTIAL (stats accounting invalid)
- 4C cache-ladder projection: PENDING
- Acceptance 2 (resident memory): NOT YET MEASURED
- Acceptance 5 (no caching): PASS by construction

### 2. phase-04-report.md — now self-contained

Rewritten so a fresh engineer can read it without chat history:
baseline → architecture → tests → what passes → what fails →
hypothesis → next diagnostic. Includes the precise failing-oracle
spec (36,288 retrieval rows byte-exact; occurrence-aware duplicates;
first router diff at row 13 = layer 7, token 1, same expert set with
215↔138 order swap; first activation divergence layer 3; logits
max|d|=1.41 while top-1/top-5 still agree) and labels the
~4.0 t/s prefill / ~1.3 t/s decode figures as provisional
observations, not validated results.

### 3. llama.cpp modification inventory — created

`progress/phase-04-modifications.md`: file-by-file table of every
local change, env gate, purpose, phase, and permanent-vs-debug-only
classification, plus the 7-env-var quick reference and the known
stats-accounting bug.

### 4. Reproduction path — made boring

- `tools/phase04_run_capture.sh` (conventional oracle; now also
  records `llama_worktree_dirty` in the manifest so provenance cannot
  silently rot).
- `tools/phase04_run_streamed.sh` (new; streamed oracle, naive or
  coalesced mode).
- `tools/phase04_compare.py` now ends with a compact VERDICT block
  (A/B/C shape below), and the frozen current verdict lives at
  `benchmarks/results/phase-04-compare-current.txt`.

```text
=== VERDICT ===
A Retrieval:      PASS  (36288/36288 byte ranges exact)
B Routing:        FAIL
   first mismatch: row 13
   same expert set: yes   ordering differs: [7, 113, 138, 215, ...] vs [7, 113, 215, 138, ...]
B Activations:    FAIL
   first divergence: layer 3
C Logits:         FAIL  (max|d|=1.412e+00 mean|d|=2.343e-01 top1=True top5=True)
OVERALL:          FAIL
```

### 5. Repository state — cleaned

**Critical finding:** the entire streaming implementation
(`llama-expert-stream.{h,cpp}`, `llama-expert-stream-exec.cpp`, and
the 12 modified source files) existed only as uncommitted working-tree
changes inside an untracked `llama.cpp/` directory. A single
`git clean -fd` or a lost directory would have destroyed the phase.
Committed now in the `llama.cpp` tree as `911055efd`; the committed
tree is byte-equivalent to the tree that produced the current traces
(the only post-capture change is gating of `[stream]` stderr noise,
which does not affect trace content).

Other hygiene:

- `.gitignore` now covers `models/`, `llama.cpp/`, `kimi-k3-in-c/`
  (nested repos, never to be tracked), default-output artifacts
  `kimi_retrieval.csv` / `kimi_stream_stats.csv` at repo root, and the
  superseded pre-fix trace dir.
- Superseded `benchmarks/results/traces/phase-04-stream-cpu/` (no
  manifest, no provenance) removed; the occurrence-aware run is the
  canonical oracle and its progression is documented in the report.
- `refs/kimi-linear/gguf-header-Q4_K_M.bin` (8 MB, first 8 MB of the
  pinned GGUF) is now committed so Phase 2/3 tooling runs without the
  28 GB model; regeneration: `dd if=<gguf> of=refs/kimi-linear/gguf-header-Q4_K_M.bin bs=1M count=8`.
- Stale committed oracle updated: `phase-04-conv-ref/` act.bin +
  manifest + run.log now reflect the post-refactor capture (was
  captured at `10dae8341`, before the 4A.1 refactor commit).
- Debug-noise gating: unconditional `[stream]` progress prints (2,457
  of 2,492 run.log lines) now require `KIMI_STREAM_DEBUG=1`.
- Not deleted (flagged): ~12 GB of root-level smoke logs
  (`smoke*.log`, `smoke_notrace.log`, `trace_*.csv`, `perf.*`) —
  gitignored since phase 3, superseded by `benchmarks/results/traces/`.
  Safe to delete: `rm smoke*.log smoke*.err trace_*.csv trace_t.err perf.out perf.err`.
- `/tmp/dump_*.bin` and `/tmp/resvfile_*.bin` written by
  `KIMI_STREAM_DEBUG` probes live outside the repo.

## New facts surfaced by the audit (not previously documented)

1. **Stats accounting is invalid** (blocks 4B): `stats.csv` mixes
   cumulative storage counters with per-step compute timers; `build_us`
   is a meaningless negative residual. Fix = emit counter deltas.
2. **Activation divergence precedes router divergence in the same
   execution**: activations diverge at layer 3, router first differs at
   layer 7 (row 13). The router swap is therefore plausibly
   **symptomatic**, not causal — this sharpens the row-13 question from
   "why does routing differ" to "what drifts at layer 3".
3. **Semantic-key misalignment on 6 of 13 execs**: the conventional
   act trace emits duplicate early-exec records (two `(0,0,2)`-keyed
   execs), so the comparator flags those execs as alignment-broken.
   Suspected warmup double-trace on the conventional path; open.
4. The row-13 router diff is an **ordering swap of an identical expert
   set** (215↔138), consistent with a top-k near-tie reordering rather
   than wrong expert selection.

## Decisions

- Commit the streaming implementation into the `llama.cpp` local tree
  and record the commit in manifests (done: `911055efd`).
- Do not re-capture oracles during this audit (no new inference
  experiments per directive); existing traces remain valid because the
  committed tree matches the producing tree except for stderr gating.
- Keep the 8 MB GGUF header snapshot committed (reproducibility of
  Phase 2/3 tooling without the 28 GB model).
- Delete the superseded pre-fix trace dir; keep the canonical
  failing oracle intact as the regression baseline.

## One-paragraph reassessment (as requested)

> We have successfully modified llama.cpp to execute Kimi Linear with
> experts retrieved individually from the GGUF rather than keeping the
> routed-expert collection resident. Retrieval and compact-slot mapping
> are independently verified (36,288 byte-exact ranges). The system
> completes inference at roughly 1.3 tok/s uncached, but it does not
> yet reproduce conventional inference numerically: routing first
> differs at trace row 13 (layer 7 — same expert set, ordering
> swapped) and activations first diverge at layer 3, which precedes the
> router difference. Phase 4 therefore remains PARTIAL. The next
> technical task is to determine whether the routing mismatch is
> causal or symptomatic — the evidence now favors symptomatic
> (activation drift at layer 3 precedes the layer-7 router swap), so
> the first diagnostic is per-layer router-input (gate logits)
> comparison from layer 0.

## Repository state after this audit

- Project repo `main`: one new commit (`phase-04b: project-health
  audit`) containing the docs above, repro scripts, comparator verdict
  block, gitignore update, updated oracle, and header snapshot.
- `llama.cpp` tree `master`: clean at `911055efd`.
- No untracked source files; untracked runtime artifacts only
  (model file, nested repos) — all gitignored.
- Canonical failing oracle: `benchmarks/results/traces/phase-04-stream-cpu-occ/`.
- Frozen verdict: `benchmarks/results/phase-04-compare-current.txt`.

## Next steps (when row-13 debugging resumes)

1. Fix `stats.csv` counter deltas (4B prerequisite).
2. Diagnose layer-3 activation drift via per-layer gate-logits capture
   on both paths (determines causal vs symptomatic).
3. Root-cause the conventional-path duplicate early-exec trace records.
4. Re-capture oracles only when trace formats change; update manifests
   (worktree-dirty flag now guards provenance).
