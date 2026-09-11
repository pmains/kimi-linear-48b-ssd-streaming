# Step 15A — Minimum Warm-Resume State for a Restored Kimi Linear Slot

Status: **COMPLETE (read-only source characterization).**
Determination: **`CHECKPOINT_RECONSTRUCTION_SUFFICIENT`**.
No production behavior was modified. No llama.cpp patch applied.

Source analysed (live binary/checkout, commit `a895f6826`):

- `llama.cpp/tools/server/server-context.cpp`
- `llama.cpp/tools/server/server-task.h`
- `llama.cpp/common/common.h`, `llama.cpp/common/common.cpp`
- `llama.cpp/src/llama-memory-hybrid.cpp`, `llama.cpp/src/llama-memory-recurrent.cpp`
- `llama.cpp/src/llama-context.cpp`
- `llama.cpp/include/llama.h`

Exact file:line references: `benchmarks/results/service-step-15/15a/source-notes.md`.

## The failure, stated precisely

From Step 14D(i), with the current server:

```text
save warm slot
→ erase / restart
→ restore snapshot successfully   (n_restored == saved depth)
→ restored prompt/model state present
→ first continuation request
→ no usable restored prompt checkpoint
→ do_reset
→ n_past = 0
→ full re-prefill  (cache_n = 0)
```

The restored state is present and correct; only the *reuse bookkeeping*
is missing. 15A localized it to one object: `slot.prompt.checkpoints`.

## Live model facts (established here)

Read from the GGUF metadata of the production model and the live server:

- `general.architecture = kimi-linear`, 27 blocks.
- `kimi-linear.attention.head_count_kv` is a per-layer array of 27 i32
  with values `0` for most layers and `1` for a minority — i.e. a
  **hybrid** model: KDA / linear-attention (recurrent) layers plus a few
  full-attention (MLA) layers (`key_length 576`, `value_length 72`,
  `kv_lora_rank 512`, `kda.head_dim 128`, `ssm.conv_kernel 4`).
- **No `sliding_window` / `swa` metadata key** → `llama_model_n_swa(model)
  == 0` → `server-context.cpp:1191` sets `n_swa = 0` (`swa_full` unset).
- `context_length` 1,048,576 (trained); server `n_ctx` 262144; 1 slot.
- Checkpoints are enabled by default: `n_ctx_checkpoints = 32`,
  `checkpoint_min_step = 8192` (`common.h:613-614`).

## Answers to the 15A questions

**Q1 — Where are `slot.prompt.checkpoints` created?**
`create_checkpoint()` at `server-context.cpp:2240`, called from the prompt
processing loop at `server-context.cpp:3521`. Gated by `do_checkpoint`
(`:3353-3366`), which requires:
`n_ctx_checkpoints > 0` AND task is a completion AND the model cannot do
full partial sequence removal or uses SWA:
`ctx_tgt_seq_rm_type ∈ {FULL, RS} || n_swa > 0`.
This hybrid model satisfies it (recurrent memory ⇒ full-sequence-only
removal), which is exactly why live same-slot reuse works today.

**Q2 — What does each checkpoint contain?**
`struct common_prompt_checkpoint` (`common.h:1115`):

| field | meaning |
|---|---|
| `n_tokens` | prompt token count at checkpoint time |
| `id_task` | creating task id |
| `pos_min`, `pos_max` | position range |
| `data_tgt` | **`llama_state_seq_get_data_ext(ctx, seq_id, PARTIAL_ONLY)`** |
| `data_dft` | same, for the draft context |
| `data_spec` | speculative-decoding state |

Captured by `update_tgt` / `update_dft` (`common.cpp:2196-2230`); loaded by
`load_tgt` / `load_dft` (`common.cpp:2232-2266`) via
`llama_state_seq_set_data_ext`.

**Q3 — At what intervals are they retained?**
At user-message starts (`spans.is_user_start`, `:3437`) and near the end
of a prompt — the last `4 + n_ubatch` and `4` tokens are processed
separately to force an end checkpoint (`:3451-3458`). Minimum spacing
`checkpoint_min_step = 8192` tokens.

**Q4 — How are they bounded / evicted?**
Two rules in `create_checkpoint` (`:2246-2266`): evict checkpoints whose
`n_tokens ≤ last + checkpoint_min_step` unless created by the current
task; and while `size >= n_ctx_checkpoints`, erase the front (FIFO). Cap
32 per slot.

**Q5 — How do they reference KV / KDA recurrent / rollback state?**
`data_tgt` is a serialized memory-state blob from the *same*
`llama_state_seq_*` machinery used by the slot save file, but captured
with `LLAMA_STATE_SEQ_FLAGS_PARTIAL_ONLY` (`llama.h:903`). For a hybrid
memory this is significant: `llama_memory_hybrid::state_write/state_read`
(`llama-memory-hybrid.cpp:191-199`) **skip the attention KV when
PARTIAL_ONLY is set** and write/read only the recurrent part. So a
checkpoint carries the **KDA recurrent state** (and, via `data_dft` /
`data_spec`, draft/speculative state) at `pos_min/pos_max`; the KV itself
stays in the live memory and is not part of the checkpoint.

**Q6 — Which function consumes a checkpoint during prefix reuse?**
The prompt-reuse block in `update_slots` (`server-context.cpp:3240-3277`):
`std::find_if` over `slot.prompt.checkpoints.rbegin()...rend()`, then
`it->load_tgt(...PARTIAL_ONLY)`, `it->load_dft(...)`,
`common_speculative_set_state(spec, slot.id, it->data_spec)`, followed by
`pos_next = min(pos_next, max(it->pos_min + 1, it->pos_max))` and
`n_past = min(slot.prompt.tokens.size_up_to_pos(pos_next), it->n_tokens)`.

**Q7 — Exact conditions for direct extension / checkpoint restore /
`do_reset` / full reprocessing?**
Order (`:3105-3290`):
1. `n_past = get_common_prefix(input_tokens)` under `cache_prompt`
   (only when `slot.can_split()`; `:397` — true for normal completion
   tasks). Optional chunk reuse with shifting only if the memory is
   shiftable (`can_cache_reuse`), which a recurrent memory is not.
2. If `n_past > 0 && n_past <= prompt.n_tokens()`:
   `pos_min = llama_memory_seq_pos_min(memory, slot.id)` (hybrid:
   `max(attn_pos_min, recr_pos_min)`, `llama-memory-hybrid.cpp:172-175`).
   - If `pos_min == -1` → `GGML_ABORT` (not our case: memory is restored).
   - If `pos_min >= pos_min_thold` → **search a checkpoint**:
     - usable ⇔ `cur.pos_max <= pos_next` AND
       (`cur.pos_min < pos_min_thold` OR `cur.pos_min == 0`).
     - found → **checkpoint restore** (n_past from the checkpoint).
     - none → **`do_reset`** → `pos_next = 0; n_past = 0` → full
       re-prefill (trace: *"forcing full prompt re-processing due to lack
       of cache data (likely due to SWA or hybrid/recurrent memory)"*).
3. `[TAG_PROMPT_LOGITS]`: if `n_past == n_tokens`, `n_past--`.

**Q8 — What invariant is missing after `SLOT_RESTORE`?**
The `SLOT_RESTORE` handler (`server-context.cpp:2492-2560`) restores the
memory via `llama_state_seq_load_file` and does:

```cpp
slot->prompt.clear();
slot->prompt.tokens = std::move(restored);
```

It sets **`prompt.tokens` and the memory state, but leaves
`slot->prompt.checkpoints` empty.** For a hybrid/recurrent model the
reuse path depends on a checkpoint to re-establish the recurrent state at
a position; with an empty list the search fails and `do_reset` zeroes
`n_past`. That is the missing invariant: **no checkpoint accompanies the
restored state.**

**Q9 — Is a checkpoint at the restored endpoint sufficient for
append-only continuation?**
Yes, from source: a checkpoint with `pos_min == 0` satisfies the
`cur.pos_min == 0` clause unconditionally, and `pos_max ≤ pos_next`
holds for an endpoint checkpoint; `load_tgt` re-establishes the recurrent
state (idempotent, since the memory is already restored), and
`n_past = min(size_up_to_pos(pos_next), it->n_tokens) = restored depth`,
so only the appended suffix is evaluated.

**Q10 — If not, what minimum checkpoint ladder?**
Not required for append-only continuation. A ladder would only matter for
branching / rollback to an earlier position, which is out of Stage 15's
scope ("one restored slot and one continuation").

**Q11 — Can the needed checkpoint be reconstructed from already-restored
state without replaying the prompt?**
Yes. At `SLOT_RESTORE` time the live context already holds the full
restored state, so the PARTIAL_ONLY capture that `create_checkpoint`
performs (`update_tgt`/`update_dft`) is directly available — no prompt
replay, no file-format change.

## Determination (required classification)

```text
CHECKPOINT_RECONSTRUCTION_SUFFICIENT
```

- **Minimum requirement:** a *single endpoint checkpoint* for the restored
  slot, reconstructible **in memory** at restore time from the
  already-restored context (PARTIAL_ONLY capture + `data_spec`), pushed
  onto `slot.prompt.checkpoints`.
- **Not required:** persisting a checkpoint ladder; changing the slot
  file format; replaying the prompt; OpenClaw-side involvement.
- **Candidate 15B change (NOT implemented here):** in the `SLOT_RESTORE`
  handler, after `slot->prompt.tokens = restored`, synthesize an endpoint
  checkpoint — `update_pos(n_tokens = restored.size(), pos_min = 0,
  pos_max = restored.size() - 1)`, `update_tgt(ctx_tgt, slot->id,
  PARTIAL_ONLY)`, `update_dft(ctx_dft, slot->id, PARTIAL_ONLY)`,
  `common_speculative_get_state(...)` — and push it. Then verify the reuse
  path takes the checkpoint-restore branch (`n_past ≈ restored depth`)
  instead of `do_reset`.

## Problems / open items (for 15B/15C)

- The exact arithmetic that admits the checkpoint search for this hybrid
  memory (`pos_min >= pos_min_thold`, where hybrid `seq_pos_min =
  max(attn, recr)` and `n_swa = 0`) is **inferred from source**, not
  instrumented. The empty-checkpoint invariant is certain; the branch
  arithmetic must be confirmed empirically by 15B/15C (startup trace
  confirms `context checkpoints enabled, max = 32, min spacing = 8192`).
- Reconstructed checkpoint lifetime: it must survive the erase/restore
  boundary only in memory; persistence is unnecessary for 15C's protocol
  (save → erase/restart → restore → append), because the reconstruction
  happens *at restore time*. Confirm in 15B.
- `data_dft` / `data_spec` are empty in the production configuration (no
  draft/speculative model); reconstruction should tolerate that.

## Next step

Stop here per the 15A gate. Do not patch llama.cpp until explicitly
authorized (15B).

## Reproduction

Source references and the decision tree: `benchmarks/results/service-step-15/15a/source-notes.md`.
Model facts: GGUF metadata read of `models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf`
(architecture `kimi-linear`, 27 blocks, `attention.head_count_kv` 0/1,
no sliding-window key).
Baseline measurements: `benchmarks/results/service-step-14/14di/`.
