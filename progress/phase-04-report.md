# Phase 04 Report — Uncached Expert Streaming

## Status

`PARTIAL`

The streamed executor is implemented, runs end-to-end, and its
retrieval path is independently verified byte-exact — but streamed
inference does not yet reproduce conventional inference numerically.
Phase 4 remains open pending the routing/activation investigation
below.

Phase-4 sub-item states (also recorded in `ROADMAP.md`):

| Item | State |
|---|---|
| 4A.1 refactor equivalence (route/compute split bit-identical) | PASS |
| 4A.2 retrieval equivalence | PASS |
| 4A.2 numerical equivalence | OPEN/FAIL |
| 4B latency decomposition | PARTIAL (accounting bug, see Problems) |
| 4C cache-ladder projection | PENDING |
| Acceptance 5 (no caching) | PASS by construction |

## Objective

Execute Kimi Linear while routed experts stay on SSD: the existing
router selects experts normally, but expert weights are retrieved from
the backing GGUF on demand, uncached — deliberately exposing the
worst-case miss path so it can be measured and decomposed.

Baseline (established Phase 1–3): llama.cpp at `2606220d9` supports
Kimi Linear; conventional Metal execution OOMs on 24 GB (30 GB model);
individual experts are addressable via `(layer, expert_id, tensor)` →
GGUF offset → `pread`; routing locality is poor (~90% LRU hit needs
~14.6 GB cache), so miss-path latency is the load-bearing number.

## Environment

- Host: Apple Silicon MacBook Air, 24 GB unified memory
- Project repo: `/Users/pmains/Code/openclaw/kimi`, branch `main`
- `llama.cpp` tree: `llama.cpp/`, branch `master`
  - upstream base: `2606220d9` (recorded `progress/phase-01-report.md`)
  - phase-04a refactor commits: `48092e67e`, `10dae8341`, `646723879`
  - streaming executor commit: `911055efd` (this phase; previously
    uncommitted working-tree changes — see `progress/phase-04-health-audit.md`)
- Model: `models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`
  (bartowski Q4_K_M; sha256 pinned in `refs/kimi-linear/gguf-q4-k-m.sha256`)
- Config facts: 27 layers, 256 experts/layer, 8 experts/token,
  `n_layer_dense_lead` layers route-free; per-expert Q4_K/Q6_K slices
  ≈ 4.36 MB (Phase 2)
- Prompt: `benchmarks/prompts/phase-04-ref.md` (49 tokens, coding task)
- Generation: 10 tokens, `--temp 0 --seed 1`
- Streamed capture: `KIMI_STREAM_EXPERTS=naive`, `-ngl 0` (CPU backend
  gate; Metal integration is the later 4B question)
- Oracle traces (both paths, same prompt/seed):
  `benchmarks/results/traces/phase-04-conv-ref/` (conventional) and
  `benchmarks/results/traces/phase-04-stream-cpu-occ/` (streamed,
  occurrence-aware; `phase-04-stream-cpu/` is the superseded
  pre-fix run and is no longer referenced)

## Architecture

The streamed path replaces the single `build_graph` + one-shot compute
in `llama_context::process_ubatch` when
`KIMI_STREAM_EXPERTS` is set (Kimi Linear, non-warmup, default graph
type). Per ubatch, per layer:

    route subgraph  (attention + KDA/SSM/MLA KV + residual + ffn_norm
                     + routing logits/probs/topk/weights)
          ↓  ids[8, n_tokens] read back to host (only data crossing)
    load_layer      (dedupe occurrences → compact slots; pread each
                     unique (layer, expert, {gate,up,down}) slice from
                     GGUF at loader-recorded offsets; byte-verify vs
                     mmap; host → backend copy)
          ↓
    compute subgraph (mul_mat_id over loaded [.., .., n_slots] expert
                     tensors with slot ids; weighted sum; residual)

Activations (`act`), FFN input, normed inputs, router weights, logits
and embeddings persist on the backend between subgraphs
(`llama_stream_persist`); only routing ids and final logits cross to
host. Every expert access is a deliberate miss: single-use buffers, no
reuse across steps. No cache, no prefetch, no eviction.

Conventional inference is untouched: the refactor of
`src/models/kimi-linear.cpp` into `build_layer_attn` /
`build_layer_ffn_route` / `build_layer_ffn_compute` is behavior-
preserving for the conventional path (4A.1 PASS — the conventional
oracle re-captured at the refactored commit reproduces the pre-refactor
traces).

## Tests

Build:

    cmake --build llama.cpp/build-metal --target llama-cli -j4   → PASS

Correctness oracle (A/B/C), conventional-CPU vs streamed-CPU, same
prompt/seed/temperature, `tools/phase04_compare.py`:

- **A. Retrieval equivalence** — PASS:
  36,288/36,288 requested expert ranges byte-exact vs the GGUF mmap
  view (occurrence-aware rows; duplicates preserved, not collapsed).
- **B. Router IDs** — FAIL: 1,512 vs 1,512 rows, structurally
  identical; first content difference at row 13 (0-indexed):
  layer 7, token 1, prefill. Same expert set
  `{7,113,138,215,163,74,52,137}`, ordering differs
  (positions 2–3 swapped: `138,215` → `215,138`).
- **B. Layer activations** — FAIL: first divergence at layer 3
  (max|d| = 4.34e-3; layers 0–2 within 1.25e-6). Divergence grows
  monotonically to layer 26 (max|d| = 2.50) and logits
  (max|d| = 1.41, mean|d| = 2.34e-1), yet top-1/top-5 still agree.
- **C. Logits** — FAIL within phase-4 tolerances (see above).
- Duplicate-selection sanity: 78/78 `(layer, kind)` groups contain
  duplicate router selections, preserved by the occurrence-aware log.

Full current verdict preserved at
`benchmarks/results/phase-04-compare-current.txt`.

## Results (what passes / what fails)

Demonstrated facts:

1. The streamed executor runs complete inference (prefill + decode)
   with every routed expert retrieved from the GGUF by pread.
2. Retrieval equivalence holds: 36,288/36,288 ranges byte-exact, using
   loader-recorded authoritative offsets (not recomputed guesses).
3. The route/compute refactor is bit-identical for conventional
   inference (4A.1).
4. The router CSV and activation trace are structurally identical
   between paths (same ubatch segmentation, same layer/token grid);
   the differences are content-level, not structural.
5. No caching exists (acceptance 5, by construction).

Observations (not yet explained):

6. First router difference at layer 7 (row 13) is an ordering swap of
   an identical expert set — consistent with a top-k tie-break /
   ordering divergence rather than wrong expert selection.
7. Activation divergence begins at layer 3 — four layers *before* the
   first router difference in the same execution. If layer-3 drift is
   upstream of the router, the row-13 mismatch is **symptomatic**, not
   causal (see Hypothesis).
8. 6 of 13 executions fail semantic-key alignment between paths (e.g.
   conventional emits a duplicate `(0,0,2)`-keyed exec), flagged by the
   comparator as alignment breakage. Suspected warmup double-trace on
   the conventional path; not yet root-caused.

Provisional performance (NOT validated results — correctness fails,
so these are floor observations only):

- ~4.0 t/s prefill, ~1.3 t/s decode (reported by llama-cli from the
  streamed run; ~0.83 s/decode step in `stats.csv`).
- ~891 MB SSD per decode step (26 layers × 8 experts × 3 tensors ×
  ≈4.36 MB), ≈0.43 s storage time → ≈2.1 GB/s effective — consistent
  with the Phase 4 design estimate (~900 MB/step).

## Hypothesis (current)

The first activation divergence (layer 3) precedes the first router
difference (layer 7) in the same execution. Working hypothesis: the
streamed route graph produces a tiny activation drift from layer 3
onward (same kernels, same bytes — so likely a graph-structure
difference: e.g. an input that is set differently, a missing/extra
copy, or a masked-position subtlety in the per-layer subgraph), and
the layer-7 router ordering swap is a downstream consequence (top-k
near-ties reorder under small input drift), not the root cause.

Alternative hypothesis: routing is causal — a subtle difference in the
route graph's router inputs (e.g. norm input or gate logits) changes
top-k ordering at layer 7, and layer-3 activation drift is a separate,
earlier phenomenon.

Distinguishing diagnostic: compare per-layer `gate_inp` logits
(router input) between paths from layer 0 — if they diverge before
layer 3, the fault is in the route graph itself; if they match through
layer 6 and only the layer-7 top-k output reorders, the swap is a
near-tie artifact and the layer-3 activation drift is the primary bug.

## Problems

1. **Numerical equivalence not achieved** — the phase's core open
   item (see Hypothesis / Next Diagnostic).
2. **`stats.csv` accounting is invalid.** Storage counters
   (`pread_calls/bytes/us`, `copy_us`, `sync_us`) accumulate across
   steps inside `llama_expert_streamer`, while `route_compute_us` /
   `expert_compute_us` are per-step. The `build_us` residual is
   therefore meaningless and goes increasingly negative. Fix: snapshot
   counters at step start and emit deltas. Blocking 4B.
3. **Semantic-key misalignment on 6/13 execs** (observation 8 above).
   The comparator refuses to compare those execs — they contribute to
   OVERALL FAIL but the activation table is only shown for exec 0.
   Suspected: warmup double-trace on the conventional path.
4. Parser bug fixed this phase: `phase04_compare.py` assumed the
   retrieval status was the last CSV field; the occurrence-aware log
   put it penultimate. Fixed and re-verified (A passes).
5. Debug-noise hygiene fixed this phase: unconditional `[stream]`
   progress prints (2,457 of 2,492 run.log lines) are now gated
   behind `KIMI_STREAM_DEBUG=1`.

## Decisions

Decision: keep the persisted FFN boundary representation unchanged and
move only the routing decision across the host boundary.
Reason: preserve conventional graph semantics; avoid activation
round-trips (user 4A.2 constraint).
Evidence: streamed path persists activations/weights on the backend;
only `ids_host` / `load.slot_ids` cross.
Consequence: smaller debugging surface; carries into the Metal path.

Decision: explicit router-expert-id → compact-slot remap in the
retrieval trace.
Reason: the compute graph operates on the compact loaded set, not the
0..255 expert space.
Evidence: trace rows carry both expert id and slot; duplicates are
separate occurrence rows.
Consequence: routing/loading/compute errors are separable in Metal
debugging.

Decision: commit the streaming implementation into the `llama.cpp`
local tree (`911055efd`) and record it in manifests.
Reason: the work previously existed only as uncommitted working-tree
changes (project-health audit finding).
Consequence: manifests now identify an auditable tree state; the
conventional oracle must be re-captured whenever trace formats change.

## Open Questions

- Is the row-13 router swap causal or symptomatic? (primary)
- Why does the conventional act trace emit duplicate early-exec
  records, breaking semantic-key alignment at execs 2,4,6,8,10,12?
- What exactly drifts at layer 3 in the streamed route graph, given
  layers 0–2 match to ≤1.25e-6?
- How should `stats.csv` be restructured to be a trustworthy latency
  budget (deltas + per-component overlap accounting)?

## Next Phase

- Diagnose row-13 (see Hypothesis): capture per-layer `gate_inp`
  logits on both paths from layer 0; determine whether routing is
  causal or symptomatic.
- Fix the stats accounting (deltas) before treating 4B/4C numbers as
  valid.
- Re-run the A/B/C oracle after any fix and preserve the result as the
  regression oracle (`phase-04-stream-cpu-occ/` is currently the
  canonical failing oracle — do not overwrite without a manifest note).
- Only after router/activation equivalence is stable: move to the
  Metal-specific streaming question with the same slot-mapped expert
  representation.
- If layer-3 drift is a route-graph input bug, the Metal path is
  unaffected in design; if it is a scheduler/persist-tensor issue, it
  will reappear on Metal — fix on CPU first.

## Reproduction

All commands from the repository root. The 28 GB GGUF must be present
at `models/kimi-linear/` (sha256 pinned).

Capture the conventional oracle (A-side baseline):

```bash
tools/phase04_run_capture.sh \
  benchmarks/results/traces/phase-04-conv-ref \
  benchmarks/prompts/phase-04-ref.md 10 1
```

Capture the streamed oracle (B/C side, uncached):

```bash
tools/phase04_run_streamed.sh \
  benchmarks/results/traces/phase-04-stream-cpu-occ \
  benchmarks/prompts/phase-04-ref.md 10 1 naive
```

Compare:

```bash
python3 tools/phase04_compare.py \
  benchmarks/results/traces/phase-04-conv-ref/act.bin \
  benchmarks/results/traces/phase-04-conv-ref/moe.csv \
  benchmarks/results/traces/phase-04-stream-cpu-occ/act.bin \
  benchmarks/results/traces/phase-04-stream-cpu-occ/moe.csv \
  benchmarks/results/traces/phase-04-stream-cpu-occ/retr.csv
```

Expected: retrieval PASS; router/activation/logits FAIL with the
row-13 / layer-3 specifics above; exit code 1. Full verdict is echoed
and also written to `benchmarks/results/phase-04-compare-current.txt`.

## Artifacts

- `benchmarks/results/traces/phase-04-conv-ref/` — conventional oracle
  (act.bin, moe.csv, run.log, manifest.json; manifest records
  llama.cpp commit + capture time + worktree state)
- `benchmarks/results/traces/phase-04-stream-cpu-occ/` — streamed
  oracle (act.bin, moe.csv, retr.csv [36,288 rows], stats.csv,
  run.log, manifest.json)
- `benchmarks/results/phase-04-compare-current.txt` — frozen current
  A/B/C verdict
- `progress/phase-04-modifications.md` — inventory of every llama.cpp
  change (env gates, permanent vs debug-only)
- `progress/phase-04-health-audit.md` — project-health audit findings
- `tools/phase04_run_capture.sh`, `tools/phase04_run_streamed.sh`,
  `tools/phase04_compare.py` — reproduction tooling
