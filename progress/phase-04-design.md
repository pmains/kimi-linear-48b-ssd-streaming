# Phase 4 Design — Uncached Expert Streaming

Status: PROPOSED (pre-implementation). Companion to `progress/phase-03-report.md`.
Roadmap: `ROADMAP.md` Phase 4 (revised per 2026-08-13 directive: no caching,
latency-decomposition-first).

## Problem

Phase 3 proved expert addressability (pread of individual Q4_K/Q6_K expert
slices, byte-exact) and per-expert Metal residency, but showed locality is
poor: ~90% LRU hit rate needs ~14.6 GB of expert cache, and OPT closes only
~3 pp of the gap. Viability therefore depends on the *cost of a miss*. Phase
4 builds the uncached streamer and measures the miss path component by
component:

    pread expert → Metal-accessible buffer → expert compute

## Architecture finding (from code inspection)

llama.cpp at `2606220d9` builds Kimi Linear graphs in
`src/models/kimi-linear.cpp` (`llama_model_kimi_linear::build_graph`). Each
MoE layer calls `build_moe_ffn` (`src/llama-graph.cpp:2001`) with the three
3D expert tensors (`ffn_gate_exps`, `ffn_up_exps`, `ffn_down_exps`,
shape `[n_embd, n_ff, 256]`), which internally:

1. routes: `logits = mul_mat(gate_inp, cur)` → probs → `topk =
   argsort_top_k(probs, 8)` → `weights = get_rows(probs, topk)` → normalize
   (Kimi: `norm_w=true`) → scale (`expert_weights_scale = 2.446`);
2. computes: `up/gate = mul_mat_id(w, cur, topk)` (Q4_K), `down =
   mul_mat_id(w, act, topk)` (Q4_K/Q6_K per layer), weighted sum.

Key facts:

- `ggml_mul_mat_id` already computes **only the selected experts**. The sole
  residency problem is that `w` must be a fully resident 256-expert 3D
  tensor.
- `build_moe_ffn` already accepts `selected_experts_in`, a clean seam for a
  compute-only path.
- The router must run **before** experts can be loaded, and layer L+1's
  router depends on layer L's FFN output → streaming is inherently
  sequential per layer: route(L) → load(L) → compute(L) → route(L+1).
- Conventional Metal decode OOMs (Phase 2: 30 GB model > 17.8 GiB working
  set). Streaming makes Metal feasible for the first time: experts are not
  resident. **Phase 4 runs Metal-first.**

## Design: per-layer two-phase streaming executor

New env-gated path (`KIMI_STREAM_EXPERTS=1`), Kimi Linear only, inert
otherwise. Replaces the single `build_graph` + one-shot compute in
`llama_context::process_ubatch` for streamed steps.

Per step (ubatch), per MoE layer `il`:

    Graph R_il  (route):  persistent act_in → attention (KV write) →
                          residual → ffn_norm → gate_inp logits → probs →
                          topk → weights.
                          Outputs (read back, then persisted):
                            ids[8, n_tokens], weights[8, n_tokens],
                            ffn_inp (persistent), attn_out (persistent)
    Load:       for each distinct (layer, expert) in ids, for gate/up/down:
                          pread slice from GGUF (Phase 3 offset table)
                          → per-expert shared MTLBuffer (expert scale)
    Graph C_il  (compute): ffn_inp → mul_mat_id(up_loaded, cur, 0..7) →
                          silu(gate) * up → mul_mat_id(down_loaded, …) →
                          weight-sum → + attn_out → persistent cur_next.

Dense lead layers (`n_layer_dense_lead`) compute inline in R_il (no
routing/loading). Final graph: output norm + lm_head. All subgraphs execute
on the Metal backend; the scheduler is reused across subgraphs.

Persistent tensors (`cur`, `inpSA`, `ffn_inp`, `attn_out`) follow the KV
cache pattern: own ggml context, allocated once via
`ggml_backend_alloc_ctx_tensors_from_buft` (Metal), referenced by each
subgraph as external inputs, updated via `ggml_cpy` inside the graphs. This
avoids O(L²) recompute per step (each subgraph would otherwise rerun layers
0..il-1).

`build_moe_ffn` is split (behavior-preserving for the normal path) into:

- `build_moe_ffn_route(...)` → logits/probs/topk/weights (existing code,
  moved);
- `build_moe_ffn_compute(cur, weights_in, ids_in, up/gate/down_loaded, …)`
  → expert matmuls + weighted sum (existing code, moved; expert tensors are
  the per-step `[n_embd, n_ff, n_selected]` loaded tensors, ids remapped to
  0..n_selected-1, weights passed in from the route graph).

Loaded expert tensors: allocated per step into per-expert Metal buffers via
`ggml_backend_alloc_buffer(metal_backend, bytes)` +
`ggml_backend_tensor_alloc` + `ggml_backend_tensor_set` (the RAM→Metal copy
being measured). No caching: every access is a fresh pread. Prefill is also
uncached (accept the full cold read; measure it — it quantifies cold-start
SSD traffic).

## Instrumentation (acceptance 3)

Per component, per expert access, wall-clock (mach_absolute_time /
clock_gettime):

| Component | Where | Measured |
|---|---|---|
| storage read | pread() around the GGUF fd | bytes, µs, effective GB/s |
| buffer prep / copy | ggml_backend_tensor_set | µs, bytes |
| Metal sync | ggml_backend_synchronize / metal flush | µs |
| kernel exec | isolated micro-benchmark (4B): mul_mat_id over streamed Q4_K/Q6_K expert buffers, n_used=8 | µs/expert, µs/layer |
| total expert-access | per-step sum | µs, ms/step |

Totals per step + per-phase (prefill/decode) to JSON:
`benchmarks/results/phase-04-streaming-*.json`. Resident memory via
`currentAllocatedSize` + task RSS at step boundaries.

## Correctness checkpoint (4A gate) — three claims

The conventional CPU path is a permanent regression oracle: TCAT
(activation/logits) + router CSV, both now carrying semantic identity
(phase, start_pos, n_tokens) per record so executions align by semantics,
not exec index. The streamed path must satisfy, on the same deterministic
prompt, in order:

**A. Retrieval equivalence** — the expert byte ranges the streamed
executor requests for every `(layer, expert_id, tensor)` match the GGUF
slices (loader memcmps pread bytes against the mmap view at runtime; also
verifies requested ids == route-graph ids).

**B. Layer equivalence** — router IDs bit-identical at every MoE layer;
per-layer l_out activation max|d| <= 1e-5 at token 0; first divergent
layer reported automatically.

**C. Model equivalence** — final logits max|d| <= 1e-5, mean|d| <= 1e-6,
top-1 and top-5 agreement 100% (predetermined, mechanism-justified:
identical kernels on identical bytes; f32 weight readback is exact on
CPU).

Diagnostic value: A-fail ⇒ loader/index wiring; A-pass + B-fail ⇒ graph
split. Comparator: `tools/phase04_compare.py`.

Metal then becomes a clean fourth question: does the SAME streamed expert
representation (per-expert buffers + loaded ggml tensors) that passes
A/B/C execute through Metal while retaining expert-scale residency?

## Backend-agnostic expert representation

The CPU gate and the eventual Metal path must share the same expert
representation, so 4A does not validate a structure that gets replaced in
4B/Metal. The loader therefore: preads slices → fills per-expert buffers
allocated via `ggml_backend_alloc_ctx_tensors_from_buft` with the buft of
the CURRENT backend (CPU buft for the gate, Metal buft later) → creates
one `[n_embd, n_ff, n_selected]` ggml tensor per (layer, tensor-kind) with
data in that buffer, consumed directly by `ggml_mul_mat_id` (ids 0..n-1).
No CPU-only structures enter the compute path; only the buffer type
changes between the gate and Metal.

## Work order

- **4A** streamer core: split `build_moe_ffn`; per-layer executor in
  `llama_context` (env-gated); persistent tensors; CPU backend first for a
  correctness smoke (matches non-streaming logits on a few tokens), then
  Metal.
- **4B** expert-kernel micro-benchmark (extend `tools/metal_expert_probe.m`
  or a ggml harness): mul_mat_id kernel time per expert type at n_used=8.
- **4C** projection: combine 4A pread/upload/sync per access, 4B kernel,
  Phase 3 trace hit rates (LRU ladder 1–12 GB), and normal-run trunk time →
  expected tok/s per ladder capacity, with and without an overlap
  assumption. Script under `tools/`.
- Correctness gate: streamed output logits/tokens vs non-streamed run for a
  short prompt (Phase 5 will formalize); acceptance 1 requires coherent
  generation only.
- Acceptance 5 (no caching) is enforced by construction: single-use expert
  buffers, no reuse across steps.

## Expected magnitude (uncached worst case)

~208 expert accesses/step (26 layers × 8) × ~4.36 MB avg ≈ **~900 MB
SSD/step** → at ~2–3 GB/s effective read, a ~0.3–0.45 s storage floor per
decode step (~2–3 tok/s ceiling) before any compute. This is the number
Phase 4 exists to measure precisely; it defines the minimum miss latency
Phase 6 prefetch/overlap must hide.

## Risks / open items

- Scheduler semantics for external persistent tensors across many
  subgraphs (KV pattern is single-graph; verify `ggml_backend_sched` treats
  pre-set-data tensors as inputs in every subgraph).
- Graph rebuild vs reuse per step: expert tensors change every step →
  rebuild every step; expected, but scheduler alloc overhead must be
  separated from the measured components (accounted as "graph build" in
  totals, not in the decomposition).
- Weights fidelity: the route phase must reproduce norm_w + scale exactly;
  by construction it reuses the same code path.
- 4B must use the same Metal kernel/quant types as 4A (Q4_K gate/up, Q4_K
  or Q6_K down per layer) to make the decomposition consistent.
