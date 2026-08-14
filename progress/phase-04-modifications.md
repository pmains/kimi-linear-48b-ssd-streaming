# Phase 4 — Custom llama.cpp Modification Inventory

Authoritative list of every local change to the `llama.cpp` tree,
what it does, how it is gated, and whether it is permanent
infrastructure or temporary debugging instrumentation.

Tree: `llama.cpp/` (local git, branch `master`).
Upstream base recorded in `progress/phase-01-report.md` (`2606220d9`);
local phase-04a commits `48092e67e`, `10dae8341`, `646723879`; streaming
work committed as `KIMI-STREAM` commit recorded in
`progress/phase-04-report.md`.

Legend for **Env gate**:

- `KIMI_STREAM_EXPERTS` — master switch for the streamed executor.
  Off when unset / empty / `0`. `naive` (or `1`) = one pread per expert
  slice; `coalesced` = merged-range preads.
- `KIMI_STREAM_RETR_FILE` — retrieval log path (default `kimi_retrieval.csv`).
- `KIMI_STREAM_STATS_FILE` — per-step stats CSV (default `kimi_stream_stats.csv`).
- `KIMI_STREAM_DEBUG` — verbose per-layer progress + resident-vs-file
  byte probes (writes `/tmp/dump_*.bin`, `/tmp/resvfile_*.bin`).
- `KIMI_TRACE_MOE` / `KIMI_TRACE_MOE_FILE` — router trace (phase 3
  tracer, reused by phase 4 as the routing oracle).
- `KIMI_TRACE_ACT` / `KIMI_TRACE_ACT_FILE` — activation/logits trace
  (TCAT v2 format, the activation oracle).
- `LLM_STREAM_EXPERTS` — nothing; the runtime symbol is
  `llm_stream_experts_enabled()` (misnamed, do not confuse with the env var).

---

## Permanent infrastructure (intended to stay)

| File | Modification | Env gate | Purpose | Phase | Classification |
|---|---|---|---|---|---|
| `src/CMakeLists.txt` | Adds `llama-expert-stream.cpp`, `llama-expert-stream-exec.cpp`, `llama-expert-stream.h` to the `llama` library | — (build) | Compile the streamer | 4 | permanent |
| `src/llama-expert-stream.h` | New: `llama_stream_persist` (persistent backend tensors: act, ffn_inp, normed, weights, logits, embd) + `llama_expert_streamer` (pread retrieval, slot dedupe, occurrence-aware log, timing stats) | — | Shared contract between executor and loader | 4 | permanent |
| `src/llama-expert-stream.cpp` | New: streamer implementation. `pread()` of expert byte ranges from the backing GGUF at loader-recorded offsets; dedupe occurrences → compact slots (first-occurrence order); naive and coalesced read modes; retrieval log rows `il,kind,occ,expert_id,slot,offset,bytes,ok,mode`; pread/copy/sync µs counters | `KIMI_STREAM_EXPERTS` (constructed only on streamed path) | Retrieval half of streaming; the object of claim A | 4 | permanent |
| `src/llama-expert-stream-exec.cpp` | New: `llama_context::process_ubatch_streamed` — per-ubatch, per-layer loop: route subgraph → readback ids → `load_layer` → compute subgraph → epilogue. All stateful trunk work (attention, KDA/SSM, MLA KV writes) in route subgraphs; compute subgraphs stateless. **4A.2: stats.csv storage counters are per-step deltas (step-start snapshot); `build_us` residual = total − (pread+copy+sync+route+expert)** | `KIMI_STREAM_EXPERTS`; debug probes additionally gated by `KIMI_STREAM_DEBUG` | Execution half of streaming | 4 | permanent |
| `src/llama-context.cpp` | `process_ubatch` dispatch: streamed path taken when `KIMI_STREAM_EXPERTS` set AND not warmup AND `LLM_GRAPH_TYPE_DEFAULT` AND arch == KIMI_LINEAR AND `n_tokens > 0`. Conventional path untouched otherwise (incl. warmup) | `KIMI_STREAM_EXPERTS` | Route the ubatch to the streamed executor | 4 | permanent |
| `src/llama-context.cpp` | Conventional-path act-dump call site now stamps the LIVE `(start_pos, phase)` at dump time (graphs reused across decode steps would otherwise carry stale build-time positions) | `KIMI_TRACE_ACT` | Semantic identity of oracle records (format v2) | 4 | permanent |
| `src/llama-context.h` | Declares `process_ubatch_streamed`, `stream_persist_`, `streamer_`, `stream_stats_file_` members | — | State for the streamed executor | 4 | permanent |
| `src/llama-graph.cpp` | `llm_trace_moe_dump` gains `clear` flag; `llm_trace_act_dump` gains `clear`, `exec_id_override`, live `start_pos`/`phase` stamping; act capture records semantic keys; **4A.2: `router_logits` (pre-activation) and `router_weights` (normalized+scaled, reshaped 2D for full readback) captures in `build_moe_ffn_route`** | `KIMI_TRACE_MOE=1` / `KIMI_TRACE_ACT=1` | Oracle trace format v2 (semantic alignment) + first-divergence boundaries | 3/4 | permanent instrumentation (inert when unset) |
| `src/llama-graph.h` | New input setters for per-layer subgraphs: `set_input_idxs_only`, `set_input_allocated` on `llm_graph_input_attn_k` and `llm_graph_input_mem_hybrid_k` (set only the tensors a given subgraph allocated) | — | Per-layer route subgraphs reference subsets of the memory inputs | 4 | permanent |
| `src/llama-impl.h` | Declares `llm_stream_experts_enabled()`; extends trace-dump signatures | — | Interface | 4 | permanent |
| `src/llama-model-loader.h` | `get_tensor_offs(name)` accessor into `weights_map` — authoritative absolute GGUF data offsets recorded at load | — | Expert slice addressing (replaces re-parsing) | 4 | permanent |
| `src/llama-model.h` | `tensor_offsets` map (tensor name → absolute file offset, populated at load); `set_file_path` / `file_path` / `mmap_base` accessors; `pimpl` made mutable | — | Streamer needs file path + mmap base + offsets | 4 | permanent |
| `src/llama.cpp` | After model load: `model->set_file_path(path_model)` | — | Streamer can open the backing GGUF | 4 | permanent |
| `src/models/models.h` | `graph`: `skip_build` constructor + `build()` split; declares `stream_init_persist`, `stream_begin/close`, `stream_graph`, `stream_build_preamble`, `stream_build_layer_route`, `stream_build_layer_compute`, `stream_build_epilogue`, `stream_ffn_cols_` | — | Streamed subgraph builders | 4 | permanent |
| `src/models/kimi-linear.cpp` | Conventional ctor refactored to `graph(model, params, skip_build=false)` + `build()` — bit-identical for the conventional path (verified by 4A.1 oracle). Adds the six `stream_build_*` subgraph builders: preamble (embd → persistent act), route (attention + FFN routing, persists ffn_inp/normed/weights, ids become graph output), compute (expert matmuls over loaded slot tensors), epilogue (output norm + lm_head → persistent logits/embd). **4A.2: `l_in`, `attn_out`, `ffn_inp` captures in `build_layer_attn`; `moe_out` capture in `build_layer_ffn_compute`** (shared builders → both paths emit) | `KIMI_STREAM_EXPERTS` (builders only invoked from streamed executor); captures gated by `KIMI_TRACE_ACT` | Streaming graph surgery + first-divergence boundaries | 4 | permanent |

## Temporary debugging instrumentation (safe to remove)

| File | Location | Gate | What it does | Status |
|---|---|---|---|---|
| `src/llama-expert-stream-exec.cpp` | eval-callback block | `KIMI_STREAM_DEBUG` | Names every node as it computes; dumps SET_ROWS ids at layer 26 | debug-only, keep behind gate or delete |
| `src/llama-expert-stream-exec.cpp` | `[CTRL]` tok_embd resident-vs-file memcmp | `KIMI_STREAM_DEBUG` | Probes whether the resident embedding equals the file bytes | debug-only |
| `src/llama-expert-stream-exec.cpp` | `[graph26]`, `[l26setrows]`, `[l26after]`, `[l26node]` dumps | `KIMI_STREAM_DEBUG` | Layer-26 SET_ROWS / k_idxs forensic dumps | debug-only |
| `src/llama-expert-stream-exec.cpp` | `[step]` ubatch metadata | `KIMI_STREAM_DEBUG` | n_tokens / start_pos / output diagnostics | debug-only |
| `src/llama-expert-stream-exec.cpp` | `[stream] layer …` progress lines | gated behind `KIMI_STREAM_DEBUG` (2026-08-13 audit) | Per-layer progress; previously unconditional (2,457 of 2,492 run.log lines) | debug-only |
| `src/llama-expert-stream.cpp` | `[load]` prints | `KIMI_STREAM_DEBUG` | Per-slot load progress | debug-only |
| `src/llama-expert-stream.cpp` | `DBGOFF` block (writes `/tmp/dump_resident.bin`, `/tmp/dump_mmap.bin`, `/tmp/dump_file.bin`) | `KIMI_STREAM_DEBUG` | 3-way resident/mmap/file byte comparison for offset validation | debug-only, delete when row-13 work closes |
| `src/llama-expert-stream.cpp` | `[RVF]` block (writes `/tmp/resvfile_*.bin`) | `KIMI_STREAM_DEBUG` | Resident-vs-file first-16-byte dumps at layer 1 | debug-only, delete when row-13 work closes |

## Known instrumentation bug (documented, not fixed)

`stats.csv` (written by `process_ubatch_streamed`) mixes **cumulative**
storage counters (`n_pread_calls`, `n_pread_bytes`, `pread_us`, `copy_us`,
`sync_us` — they accumulate across steps inside `llama_expert_streamer`)
with **per-step** compute timers (`route_compute_us`,
`expert_compute_us`). The `build_us` residual
(`total − pread − copy − sync − route − expert`) is therefore
meaningless and goes increasingly negative. Fix before treating the
latency decomposition (4B) as valid: snapshot the storage counters at
step start and emit deltas.

## Environment variable quick reference

| Var | Values | Effect |
|---|---|---|
| `KIMI_STREAM_EXPERTS` | unset/`0` = off; `naive`/`1`; `coalesced` | Master switch + read mode |
| `KIMI_STREAM_RETR_FILE` | path | Retrieval log (claim A evidence) |
| `KIMI_STREAM_STATS_FILE` | path | Per-step stats CSV |
| `KIMI_STREAM_DEBUG` | `1` | Verbose/debug probes (see above) |
| `KIMI_TRACE_MOE`, `KIMI_TRACE_MOE_FILE` | `1`, path | Router oracle trace (both paths) |
| `KIMI_TRACE_ACT`, `KIMI_TRACE_ACT_FILE` | `1`, path | Activation/logits oracle trace (both paths) |
