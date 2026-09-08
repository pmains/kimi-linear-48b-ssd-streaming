# 14B evidence notes — Server-side reuse mechanism (2026-09-08)

Source: llama.cpp at /Users/pmains/Code/openclaw/kimi/llama.cpp
commit a895f6826 = the live server binary (llama-server 0.1.0-dev,
build 10447, `runtime/live/bin/llama-server`; verified identical HEAD).
No production changes; read-only source inspection + live /props,/slots.

## Runtime configuration (launchd + serve script)

- `--parallel 1` (single slot), `--ctx-size 262144`, `--no-mmap`,
  `-ngl 999`, `--slot-save-path runtime/state/slot-cache`.
- Boot banner: `load_model: initializing, n_slots = 1, n_ctx_slot =
  262144, kv_unified = 'false'`.
- Expert-streaming env (KIMI_STREAM_*) wraps llama.cpp but does not
  change the reuse paths below.

## 1. Slot state / retained prompt

- Each slot retains `prompt.tokens` (the tokens of the last processed
  request) and its KV/state so a later request can reuse them
  (tools/server/server-context.cpp: `slot.prompt.tokens`,
  `prompt_clear()`, release keeps the prompt for non-child slots).
- `release()` -> `reset()` clears `stats = {}` (server-context.cpp:351),
  which is why /slots snapshots taken right after a task show
  `n_prompt_tokens_cache: 0` even though the retained KV made the NEXT
  request's reuse ~48K tokens (14A observation explained: the field is
  the *last task's* accounting, cleared on release; the cache itself is
  the retained slot prompt+KV).

## 2. Slot selection by LCP similarity

`get_available_slot()` (server-context.cpp:1485):
- threshold `slot_prompt_similarity` default 0.1 (common.h:677; CLI
  `--slot-prompt-similarity`), logs `(> 0.100 thold)`.
- For each idle slot: `lcp_len = slot.prompt.tokens.get_common_prefix(
  task.tokens)`; `f_sim_cur = lcp_len/task.tokens.size()`;
  select best slot with `f_sim_cur > threshold`.
- `f_keep = (f_sim_best*task.tokens.size())/ret->prompt.tokens.size()`;
  if `f_keep < 0.5` -> `update_cache` -> `prompt_save`/`prompt_load`
  via the in-RAM `server_prompt_cache` (cache_ram_mib) to avoid losing
  long prompts to slot eviction; `prompt_clear()` on failure.
- Single slot here => selection always lands on slot 0; the LCP logic
  governs how much of the retained prompt is reused.

## 3. Per-request prefix reuse decision (the n_past computation)

SLOT_STATE_STARTED path (server-context.cpp:3057-3302):
- `cache_prompt` defaults TRUE for tasks (server-task.h:53).
- `n_past = slot.prompt.tokens.get_common_prefix(input_tokens)` —
  longest common prefix with the retained prompt (line 3112).
- Chunk-level KV-shift reuse (`n_cache_reuse`, default 0 = disabled;
  server-task.h:68) only when `llama_memory_can_shift` and no mtmd
  (lines 3120-3180). For hybrid memory can_shift comes from the
  attention KV part (`llama_memory_hybrid::get_can_shift` ->
  `mem_attn->get_can_shift()`); llama_kv_cache::get_can_shift() is
  false only for STEP35 / n_pos_per_embd>1 -> true here, but the
  feature is off by default.
- Checkpoints (lines 3220-3290): if the requested reuse point
  diverges mid-context (`pos_min >= pos_min_thold`), the server
  searches `slot.prompt.checkpoints` (list of common_prompt_checkpoint
  storing partial tgt/dft state); restores the nearest valid
  checkpoint (load partial-only) and sets n_past accordingly; erases
  checkpoints with `pos_max > pos_next`. If none covers the
  divergence: `do_reset` -> `n_past = 0`, full re-processing
  ("forcing full prompt re-processing due to lack of cache data
  (likely due to SWA or hybrid/recurrent memory, see ...pr/13194)").
- Checkpoint cadence: `checkpoint_min_step = 8192` default (common.h:
  614), max `n_ctx_checkpoints = 32`/slot (common.h:613);
  `create_checkpoint()` at server-context.cpp:2240/3521.
- `slot.stats.n_prompt_cached = n_past` (line 3302); reported in
  /slots as `n_prompt_tokens_cache` (line 658) and in OpenAI-compat
  usage as `prompt_tokens_details.cached_tokens`.

## 4. KDA / hybrid memory (kimi-linear)

- `LLM_ARCH_KIMI_LINEAR` is classified HYBRID (`llm_arch_is_hybrid`,
  llama-arch.cpp:977), NOT recurrent (Mamba/RWKV only), NOT diffusion.
- `llama_model::create_memory` default branch -> hybrid path ->
  `llama_memory_hybrid` (llama-model.cpp:2444): attention layers use a
  normal KV cache (`mem_attn`), recurrent (KDA) layers use
  `llama_memory_recurrent` (mem_recr; F32 state, n_rs_seq rollback
  snapshots). Layer split via default filters `!is_recr(il)` /
  `is_recr(il)` (llama-memory-hybrid.cpp:48-64).
- seq_rm/seq_cp/seq_keep apply to BOTH attn and recurrent parts
  (llama-memory-hybrid.cpp) so prefix edits invalidate both coherently.
- Because recurrent state cannot be shifted, mid-context edits rely on
  checkpoints (see 3) — consistent with 14A: pure prefix extensions
  (consecutive same-session turns appending user/tool content) reused
  99.2-99.9% with NO checkpoint restore needed (state already covers
  the prefix); no KDA-specific boundary appeared on plain extensions.

## 5. Accounting chain -> OpenClaw usage fields

- llama.cpp OpenAI-compat usage:
  `{prompt_tokens, completion_tokens, total_tokens,
    prompt_tokens_details:{cached_tokens = n_prompt_tokens_cache}}`
  (server-task.cpp:366-373 usage_json_oaicompat).
- OpenClaw dist (usage-BpC2Ujh-.mjs:56): cacheRead =
  `raw.prompt_tokens_details?.cached_tokens` (or other aliases);
  parseOpenAICompletionsUsage -> input = prompt_tokens - cached -
  cacheWrite; cacheRead = cached. Hence record usage.input = newly
  evaluated prompt tokens, usage.cacheRead = reused prefix tokens —
  matches 14A (legC: input 48, cacheRead 48,054, total 48,107;
  promptTokens 48,102 = 48,054 + 48).
- /slots fields (server-context.cpp:656-658): n_prompt_tokens =
  retained prompt token count; n_prompt_tokens_cache =
  stats.n_prompt_cached (last task's n_past; 0 after release/reset).

## 6. Persistence / slot-save-path

- `--slot-save-path runtime/state/slot-cache` enables the /slots
  save/load actions (server-context.cpp:4501+, 5116-5176) to persist
  slot KV/state to disk. Slot KV additionally survives across requests
  in memory (the retained prompt + state is what produces reuse).
- No compaction/context-shift observed in 14A windows; single slot, no
  slot eviction between consecutive same-server requests.

## Live evidence (2026-09-08 11:50 MST)

- /props n_ctx = 262144; model =
  models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf.
- /slots: id 0, n_ctx 262144, n_prompt_tokens 42303 (retained prompt
  of the last 14A legE-t2 task), n_prompt_tokens_cache 0 (post-release
  stats), id_task 7620. Consistent with the lifecycle in section 1/5.
