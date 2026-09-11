# 15A source notes — checkpoint machinery (llama.cpp @ a895f6826)

All paths relative to `llama.cpp/`. Read-only analysis; no patch applied.

## Definitions

| Symbol | Location |
|---|---|
| `struct common_prompt_checkpoint` | `common/common.h:1115` |
| `n_ctx_checkpoints = 32` (max per slot) | `common/common.h:613` |
| `checkpoint_min_step = 8192` (min spacing) | `common/common.h:614` |
| `LLAMA_STATE_SEQ_FLAGS_PARTIAL_ONLY` | `include/llama.h:903` |
| `std::list<common_prompt_checkpoint> checkpoints` | `tools/server/server-task.h:567` |
| `--ctx-checkpoints` / `--checkpoint-min-step` CLI | `common/arg.cpp:1690-1702` |

## Checkpoint lifecycle

| Step | Location |
|---|---|
| gate `do_checkpoint` (n_ctx_checkpoints>0, completion task, seq_rm_type FULL/RS or n_swa>0) | `tools/server/server-context.cpp:3353-3366` |
| force end-of-prompt checkpoint (offsets `4+n_ubatch`, `4`) | `server-context.cpp:3447-3458` |
| user-message-start checkpoints | `server-context.cpp:3437-3443` |
| `create_checkpoint(slot, n_tokens_cur, pos_min, pos_max)` | `server-context.cpp:2240` |
| call site | `server-context.cpp:3521` |
| eviction (min-step) + FIFO cap | `server-context.cpp:2246-2266` |
| capture state (`update_tgt`/`update_dft`, PARTIAL_ONLY) | `server-context.cpp:2271-2279`; `common/common.cpp:2196-2230` |
| consume during reuse (`find_if` → `load_tgt`/`load_dft`/`data_spec`) | `server-context.cpp:3240-3277` |
| load state (`load_tgt`/`load_dft`) | `common/common.cpp:2232-2266` |

## Memory-state semantics

| Behaviour | Location |
|---|---|
| hybrid state_write/read: PARTIAL_ONLY skips attention KV, writes/reads recurrent only | `src/llama-memory-hybrid.cpp:191-199` |
| hybrid `seq_pos_min` = `max(attn.seq_pos_min, recr.seq_pos_min)` | `src/llama-memory-hybrid.cpp:172-175` |
| recurrent `seq_pos_min` = min `cell.pos` among cells holding seq; -1 if none | `src/llama-memory-recurrent.cpp:364-378` |
| kv-cache `seq_pos_min` | `src/llama-kv-cache.cpp:655-665` |
| `llama_context::state_seq_save_file` (magic/version/tokens + `state_seq_write_data(io, seq, 0)`) | `src/llama-context.cpp:3232-3244` |
| `llama_context::state_seq_load_file` | `src/llama-context.cpp:3178-3230` |

## Reuse decision tree (server-context.cpp ~3105-3290)

```
can_split()                       (:397)   completion task -> true
  └─ cache_prompt -> n_past = get_common_prefix(input_tokens)   (:3112)
       └─ optional chunk reuse only if memory shiftable (recurrent memory is not)
  └─ if n_past > 0 && n_past <= prompt.n_tokens():
        pos_min = llama_memory_seq_pos_min(memory, slot.id)     (:3192)
          = max(attn, recr) for hybrid
        if pos_min == -1 -> GGML_ABORT                          (:3194)
        if pos_min >= pos_min_thold:                            (:3190, :3224)
              pos_min_thold = max(0, pos_next - n_swa - (has_new_tokens?0:1))
              checkpoint = find_if(rbegin..rend):
                  usable <=> cur.pos_max <= pos_next
                             AND (cur.pos_min < pos_min_thold OR cur.pos_min == 0)
              found -> load_tgt/load_dft(PARTIAL_ONLY) + data_spec;
                       pos_next = min(pos_next, max(pos_min+1, pos_max));
                       n_past   = min(size_up_to_pos(pos_next), it->n_tokens)
              none -> do_reset: pos_next = 0; n_past = 0   (full re-prefill)
  └─ [TAG_PROMPT_LOGITS] if n_past == n_tokens -> n_past--      (:3298-3302)
```

## SLOT_RESTORE handler (server-context.cpp:2492-2560)

```
if slot busy -> defer
nread = llama_state_seq_load_file(ctx_tgt, filepath, slot->id, nullptr, 0, &n_packed)
if nread != 0:
    packed.resize(n_packed)
    llama_state_seq_load_file(..., packed.data(), ..., &n_packed)
if nread == 0 -> throw "No available space in KV cache or invalid slot save file"
restored = server_tokens::deserialize(packed, mctx != nullptr)
if restored.size() > slot->n_ctx -> throw
if !restored.validate(ctx_tgt) -> throw
slot->prompt.clear()
slot->prompt.tokens = std::move(restored)      <-- NO checkpoints populated
res->n_tokens = slot->prompt.tokens.size()
```

Missing invariant: **`slot->prompt.checkpoints` remains empty**, so the
reuse path cannot re-establish a checkpoint and takes `do_reset`.

## Live model facts (GGUF metadata read)

```
general.architecture                 = kimi-linear
kimi-linear.block_count              = 27
kimi-linear.context_length           = 1048576
kimi-linear.embedding_length         = 2304
kimi-linear.attention.head_count     = 32
kimi-linear.attention.head_count_kv  = [0,0,0,1, 0,0,0,1, ...]   (27 entries)
kimi-linear.attention.key_length     = 576
kimi-linear.attention.value_length   = 72
kimi-linear.attention.kv_lora_rank   = 512
kimi-linear.kda.head_dim             = 128
kimi-linear.ssm.conv_kernel          = 4
kimi-linear.expert_count             = 256 ; expert_used_count = 8
(no sliding_window / swa key)
```

⇒ hybrid KDA (recurrent) + MLA attention layers; `llama_model_n_swa == 0`.
Server: `--ctx-size 262144`, `--parallel 1`, `--slot-save-path
runtime/state/slot-cache`.

## Error-path note (carried from 14D(i))

`llama_context::state_seq_load_file` aborts (`ggml_abort`) on a truncated
snapshot — a malformed-state rejection path, not a silent accept.
