# Step 14B Report — Characterize Server-Side Reuse Mechanism (2026-09-08)

## Status

**14B PASS.** Inspection/documentation of the server-side reuse
mechanism, per SERVICE-ROADMAP.md §14B. Read-only: no production
changes, no patches, no config edits. Source of truth = llama.cpp at
the exact commit of the live server binary (`a895f6826`, build 10447),
plus the live llama-server /props and /slots state. Evidence:
`benchmarks/results/service-step-14/14b/evidence-notes.md`.

## Objective

Resolve the mechanism questions 14A left uncertain: how llama-server
slot reuse, KV/prefix reuse, KDA recurrent-state reuse, invalidation
boundaries, and persistence semantics actually work in the running
stack (llama.cpp commit `a895f6826` + OpenClaw gateway accounting).

## Changes

- `benchmarks/results/service-step-14/14b/evidence-notes.md` — the
  full code-traced mechanism write-up with source file/line anchors.
- `service-progress/step-14b-server-reuse-mechanism.md` — this report.
- No driver needed: this is a source + live-state characterization of
  behavior 14A already measured.

## Results

### The reuse chain (end to end)

1. **OpenClaw** re-sends the full assembled prompt on every model call
   (same-session turns grow the transcript; the gateway serializes the
   complete message list each fetch).
2. **llama-server slot selection** (`get_available_slot`,
   server-context.cpp:1485): with `slot_prompt_similarity` default 0.1,
   it compares each idle slot's *retained prompt tokens* against the
   incoming prompt by longest-common-prefix:
   `f_sim_cur = lcp_len/task.tokens.size()`; picks the best slot above
   threshold. Single slot here (`--parallel 1`) => always slot 0, and
   the LCP logic determines how much of the retained prompt is reused.
   If reuse would discard >half the cached prompt (`f_keep < 0.5`) the
   slot prompt is saved/loaded through the RAM prompt cache.
3. **Per-request prefix reuse** (`cache_prompt` defaults true,
   server-task.h:53; SLOT_STATE_STARTED, server-context.cpp:3112):
   `n_past = slot.prompt.tokens.get_common_prefix(input_tokens)` —
   llama evaluates only the tokens after the LCP; the prefix KV/state
   stays. This is the mechanism behind 14A's measured 99.2–99.9%
   reuse on same-session suffix turns.
4. **KDA / hybrid memory**: `LLM_ARCH_KIMI_LINEAR` is classified
   **hybrid** (llama-arch.cpp:977), so `llama_model::create_memory`
   builds `llama_memory_hybrid` (llama-model.cpp:2444): attention
   layers in a normal KV cache (`mem_attn`) + recurrent KDA layers in
   `llama_memory_recurrent` (`mem_recr`, F32 state, n_rs_seq rollback
   snapshots). Layer split by `!is_recr(il)` / `is_recr(il)` defaults.
   seq_rm/seq_cp/seq_keep act on both parts coherently. Consequence
   consistent with 14A: on *pure prefix extensions* (consecutive
   same-session turns appending user/tool content) no checkpoint
   restore is needed — the recurrent state already covers the prefix —
   so no KDA-specific reuse boundary appears at up to 48K assembled.
5. **Mid-context edits (invalidation boundary)**: if a new prompt
   diverges mid-context rather than extending the prefix, the server
   restores the nearest saved *context checkpoint* (partial tgt/dft
   state; cadence `checkpoint_min_step=8192`, max
   `n_ctx_checkpoints=32`, server-context.cpp:2240/3521); checkpoints
   with `pos_max > pos_next` are erased. If no checkpoint covers the
   divergence it forces full re-processing (`do_reset`, n_past=0) —
   the explicit "SWA or hybrid/recurrent memory" path
   (server-context.cpp:3272). No such event occurred in the 14A legs
   (all suffix turns extended the prefix).
6. **Accounting** (`usage_json_oaicompat`, server-task.cpp:366):
   `prompt_tokens_details.cached_tokens = n_prompt_tokens_cache` (=
   `stats.n_prompt_cached` = `n_past` of the just-processed task).
   OpenClaw maps that field to `usage.cacheRead` and derives
   `usage.input = prompt_tokens − cached` (openclaw
   usage-BpC2Ujh-.mjs:56). This closes the loop with 14A's records
   (legC: input 48 / cacheRead 48,054 / promptTokens 48,102).
7. **Persistence**: KV + recurrent state persist in the slot in memory
   between requests (that is the reuse substrate); `--slot-save-path
   runtime/state/slot-cache` additionally enables /slots save/load of
   slot state to disk.

### Resolved 14A uncertainties

- *Why did /slots show `n_prompt_tokens_cache: 0` after a task that
  reused 48K?* The field is per-*task* accounting
  (`stats.n_prompt_cached`), cleared by `release()->reset()`
  (`stats = {}`, server-context.cpp:351). The retained prompt + KV
  remain; the *next* request's `n_past`/cacheRead reports the reuse.
- *What is the KDA boundary?* Kimi-Linear is hybrid: attention layers
  (KV cache) + KDA recurrent layers (state). Reuse works across both
  for prefix extensions; mid-context divergence needs checkpoints and
  can force full re-processing. No extra boundary on the common
  same-session suffix pattern (matches 14A measurement).
- *Mechanism name for the report:* llama.cpp slot KV/state prefix
  reuse via LCP with retained slot prompt tokens + per-task
  `cached_tokens` accounting surfaced through OpenAI-compat usage.

## Problems

- Source-tree inspection required matching the live binary exactly;
  verified HEAD == `a895f6826` (the live `runtime/live/bin` build).
- Some server code paths (checkpoint restore on speculative/draft
  contexts, `update_cache` prompt-cache file behavior) were traced but
  not exercised live — they only trigger on mid-context edits or
  multi-slot eviction, neither of which occurred in 14A. Noted as
  residual 14B/14C questions rather than blockers.

## Decisions

- 14B is a documentation step: the authoritative source is the exact
  running llama.cpp commit, and live /props,/slots confirm config
  (n_ctx 262144, single slot, hybrid model). No new measurements were
  needed beyond the retained 14A records.
- Classified as PASS: the mechanism is now fully described with code
  anchors, and every 14A observation maps to a specific code path.

## Next Phase

14C ("stabilize automatic same-session reuse") is conditional on 14A/14B
demonstrating *avoidable* same-session misses. 14A found none (99.2–99.9%
reuse on the real path), and 14B shows prefix reuse is the designed,
default-on behavior (`cache_prompt=true`, LCP n_past, hybrid memory with
checkpoints). A 14C remediation therefore has no demonstrated target;
the next useful substep is 14D (cross-session / persistent reuse) or
14E (production qualification benchmark) if the owner authorizes
progression. STOPPED at the 14B gate per the roadmap — 14C/14D/14E
require separate authorization.

## Reproduction

```bash
# Source: llama.cpp at commit a895f6826 == live server binary.
git -C llama.cpp rev-parse HEAD            # a895f6826…
curl -s http://127.0.0.1:18080/props       # n_ctx 262144, kimi-linear MXFP4
curl -s http://127.0.0.1:18080/slots       # single slot state
# Evidence: benchmarks/results/service-step-14/14b/evidence-notes.md
```
