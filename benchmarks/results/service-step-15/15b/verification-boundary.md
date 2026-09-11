# 15B pre-implementation verification — exact boundary (STOP report)

Owner instruction for 15B: *verify the inferred branch arithmetic before applying
the change; if the arithmetic proves wrong, STOP and report the exact boundary
rather than broadening the patch.*

**Outcome: the arithmetic did NOT reconfirm. No llama.cpp change was applied.**

Source: live checkout, commit `a895f6826`.

---

## 1. What 15A asserted

- `SLOT_RESTORE` restores the memory + `slot->prompt.tokens` but leaves
  `slot->prompt.checkpoints` empty.
- The reuse path consults a checkpoint; finding none it takes `do_reset`
  (`n_past = 0`) → full re-prefill.
- Fix classification: `CHECKPOINT_RECONSTRUCTION_SUFFICIENT` — synthesize one
  endpoint checkpoint at restore time.

## 2. What was re-verified now (verbatim, with citations)

**2.1 `n_swa = 0` for this model.**
- `llama_model_n_swa()` returns `model->hparams.n_swa`
  (`src/llama-model.cpp:2664-2671`); only `LLM_ARCH_DEEPSEEK4` is special-cased
  to 0 — Kimi Linear is not.
- `hparams.n_swa` defaults to `0` (`src/llama-hparams.h:149`), `swa_type`
  defaults to `LLAMA_SWA_TYPE_NONE` (`:147`).
- The GGUF key needed to set it is `%s.attention.sliding_window`
  (`src/llama-arch.cpp:255`). A full dump of the model's **48 GGUF keys**
  contains **no** `sliding_window` / SWA key.
- Nothing in `llama-arch.cpp` or the loader assigns `hparams.n_swa` for this
  arch (grep of `src/` shows only reads/logging, no assignment).
⇒ `n_swa == 0` at runtime. (15A's inference was correct.)

**2.2 The branch code (verbatim, `tools/server/server-context.cpp:3187-3224`).**

```
llama_pos pos_next = slot.prompt.tokens.pos_next(n_past);
const bool has_new_tokens = (n_past < slot.task->n_tokens());
const auto pos_min_thold = std::max(0, pos_next - n_swa - (has_new_tokens ? 0 : 1));

if (n_past > 0 && n_past <= slot.prompt.n_tokens()) {
    const auto pos_min = llama_memory_seq_pos_min(llama_get_memory(ctx_tgt), slot.id);
    if (pos_min == -1) GGML_ABORT(...);
    ...
    if (pos_min >= pos_min_thold) {
        // checkpoint search; none -> do_reset -> pos_next = 0; n_past = 0
    }
}
```

- `pos_next(n)` for non-mtmd returns `n` (`tools/server/server-common.cpp:372-379`).
- `get_common_prefix` is a token-id LCP (`:663-686`).
- hybrid `seq_pos_min = max(attn.seq_pos_min, recr.seq_pos_min)`
  (`src/llama-memory-hybrid.cpp:172-175`).
- recurrent cells store `cell.pos = last processed position`
  (`src/llama-memory-recurrent.cpp:634-644`) and `state_read_meta` restores
  `cell.pos` straight from the file (`:950-1010`).

**2.3 Restore still leaves checkpoints empty** (`server-context.cpp:2536`
area: `prompt.clear(); prompt.tokens = restored;`) — unchanged.

## 3. The contradiction

Substituting the verified values for an appended suffix after restore:

| quantity | source-derived value |
|---|---|
| `n_past` (LCP of restored prompt vs request) | `D` (restored depth) |
| `pos_next` | `D` (`pos_next(n) = n`) |
| `has_new_tokens` | `true` (suffix appended) |
| `n_swa` | `0` |
| `pos_min_thold` | `max(0, D − 0 − 0) = D` |
| `pos_min` | `max(attn_min, recr_min)` |

For the restored state we expect `attn_min = 0` (KV positions `0..D−1`) and
`recr_min = D−1`, giving `pos_min = D−1`. Then **`pos_min (D−1) ≥ thold (D)`
is false ⇒ the checkpoint block is skipped ⇒ `n_past` stays `D` ⇒ suffix-only
reuse.**

But Step 14D(i) measured the opposite: `cache_n = 0`, `prompt_n = D`
(full re-prefill), i.e. `do_reset` ran. The slot-selection log even shows the
restored prompt matched (`f_sim_best = 0.999`), so `n_past` was ≈ `D` before
the block.

**Therefore the 15A arithmetic does not reproduce the observed failure.** The
empty-checkpoint invariant is *certain*, but the claim that it is what triggers
`do_reset` (and hence that reconstructing a checkpoint is sufficient) is
**not verified** by this reading, and cannot be used as the stated
justification for the patch as-is.

## 4. Remaining unknown (requires runtime instrumentation)

The one quantity that decides everything is the **runtime value of
`llama_memory_seq_pos_min(memory, slot.id)` immediately after `SLOT_RESTORE`**
and at the first post-restore request. For this model it is not derivable from
source with confidence because:

- `llama_kv_cache::seq_pos_min` **delegates** to a wrapped specialized cache
  when `other` is set (`src/llama-kv-cache.cpp:655-665`), and specialized
  caches exist for this family (`llama-kv-cache-dsv4.cpp`,
  `llama-kv-cache-iswa.cpp`, `llama-kv-cache-msa.cpp`,
  `llama-memory-hybrid-iswa.cpp`). Kimi Linear is hybrid + MLA
  (`kv_lora_rank 512`), so the attention side's `seq_pos_min` semantics are
  not the plain KV case.
- Consequently `pos_min` after restore may legitimately be `≥ D`, which would
  make the branch fire and `do_reset` run — but that must be **observed**, not
  assumed.

## 5. Minimal instrumentation that would resolve it

Existing trace points (currently below the server's log level):

- `server-context.cpp:3249` — `SLT_TRC "checking checkpoint with [%d, %d] against %d..."`.
- `server-context.cpp:3274-3277` — `SLT_TRC "forcing full prompt re-processing due to lack of cache data (likely due to SWA or hybrid/recurrent memory)"`.
- `slots_debug` via env `LLAMA_SERVER_SLOTS_DEBUG` (`:1285`, prints token context around the mismatch at `:3201`).

Plus one added line at `:3224` logging
`n_past, pos_next, n_swa, pos_min, pos_min_thold, has_new_tokens, checkpoints.size()`.

## 6. Constraint conflict

Instrumentation requires building **and running** a modified binary.

- 15B explicitly forbids production binary promotion.
- A second concurrent llama-server instance would need its own 8 GiB expert
  cache; two instances on this 24 GB host risk memory thrash that would
  destabilise the live production server.

So the verification step needs owner direction. Options:

- **A** — approve a temporary second instance (bounded, minutes) for the
  instrumented run; production untouched.
- **B** — approve one short controlled restart of the production server using
  an instrumented build with identical env/slot config, then restore the
  production binary.
- **C** — authorize the 15B patch without the arithmetic proof (the
  empty-checkpoint invariant is certain; a reconstructed endpoint checkpoint is
  at worst a no-op if the branch is not the trigger).
- **D** — other.

Recommendation: **A** (then B if A is infeasible). Option C is defensible but
violates the owner's stated precondition, so it is not taken unilaterally.

## 7. Status

**STOPPED at the pre-implementation verification gate.** No code changed, no
build produced for promotion, no production binary touched.
