# Step 15B — Implement the Minimum Persistent-Resume Mechanism

## Status

**STOPPED at the pre-implementation verification gate.**

No llama.cpp change was applied. No build was produced for promotion. No
production binary was touched.

Reason: the owner's precondition for 15B was to first verify the 15A inferred
branch arithmetic (`seq_pos_min`, `pos_min_thold`, hybrid `max(attn, recr)`,
`n_swa = 0`) and to STOP and report the exact boundary if that arithmetic
proves wrong rather than broadening the patch. Re-verification did **not**
reconfirm it — source-derived arithmetic predicts the reuse path would *not*
fall through to `do_reset`, contradicting the measured behaviour.

Full analysis: `benchmarks/results/service-step-15/15b/verification-boundary.md`.

## Objective

Reconstruct, after a successful `SLOT_RESTORE`, the same minimal endpoint
checkpoint that normal live execution would have produced for the restored
prefix, so the restored slot resumes at approximately the restored depth
instead of reprocessing the prompt from token 0.

Planned scope (unchanged, not executed): `n_tokens = restored depth`,
`pos_min = 0`, `pos_max = restored depth - 1`, PARTIAL_ONLY capture via the
existing `update_tgt` / `update_dft` machinery, exactly one endpoint checkpoint
pushed onto `slot.prompt.checkpoints`. No slot-file format change, no ladder,
no prompt replay, no OpenClaw change.

## Changes

None to llama.cpp.

Added evidence:

- `benchmarks/results/service-step-15/15b/verification-boundary.md`
- `service-progress/step-15b-checkpoint-reconstruction.md` (this report)

## Results of the verification step

### Confirmed

- **`n_swa = 0`.** `llama_model_n_swa` returns `hparams.n_swa` (only
  `LLAMA_ARCH_DEEPSEEK4` is zeroed); `n_swa` defaults to 0; the GGUF key
  `%s.attention.sliding_window` is **absent** from all 48 keys of the
  production model; nothing in the loader assigns it. 15A's inference holds.
- **`pos_next(n) = n`** for non-mtmd prompts, and `get_common_prefix` is a
  token-id LCP.
- **The restore path still leaves `slot.prompt.checkpoints` empty.**
- **The reuse block is exactly as 15A described**, including
  `if (pos_min >= pos_min_thold) { checkpoint search; else do_reset }`.

### Contradiction found

With `n_swa = 0`, `has_new_tokens = true` (suffix appended) and `pos_next =
n_past = D`:

```
pos_min_thold = max(0, D - 0 - 0) = D
```

The checkpoint branch — and therefore `do_reset` — is entered **only if
`pos_min >= D`**. For a restored state we expect `attn_min = 0` and
`recr_min = D-1`, so `pos_min = D-1 < D` ⇒ the branch is **skipped** ⇒
`n_past` stays `D` ⇒ suffix-only reuse.

But Step 14D(i) measured full re-prefill (`cache_n = 0`, `prompt_n = D`), and
the server's own slot-selection log shows the restored prompt matched at
`f_sim_best = 0.999`. So `do_reset` did run, contrary to the arithmetic.

**Conclusion:** the empty-checkpoint invariant is certain, but *that it is the
trigger for `do_reset`* is not established. The 15A determination is therefore
not yet sufficient to justify the patch as specified.

### Remaining unknown (requires instrumentation)

The runtime value of `llama_memory_seq_pos_min(memory, slot.id)` immediately
after `SLOT_RESTORE`. For this hybrid + MLA model it is not safely derivable
from source: `llama_kv_cache::seq_pos_min` delegates to a wrapped specialized
cache when `other` is set, and specialized caches exist for this family
(`llama-kv-cache-dsv4.cpp`, `llama-kv-cache-iswa.cpp`, `llama-kv-cache-msa.cpp`,
`llama-memory-hybrid-iswa.cpp`). If the attention side reports `pos_min >= D`,
the branch fires and `do_reset` runs — which would reconcile everything — but
that must be observed, not assumed.

## Problems

- Instrumentation requires building and **running** a modified binary. 15B
  forbids production promotion; a second concurrent instance would need its own
  8 GiB expert cache (two on a 24 GB host ⇒ memory thrash that would destabilise
  the live server).

## Decisions

- Did not apply the patch.
- Did not broaden the change.
- Did not promote any binary.
- Escalated the boundary with options A–D
  (`verification-boundary.md` §6):
  - **A** temporary second instance for the instrumented run (preferred);
  - **B** one short controlled restart of the production server with an
    instrumented build (identical env), then restore the production binary;
  - **C** authorize the patch without the arithmetic proof (the empty-checkpoint
    invariant is certain; a reconstructed endpoint checkpoint is at worst a
    no-op);
  - **D** other.

## Next Phase

Await owner direction on A/B/C/D. 15C live qualification is not begun.

## Reproduction

Source citations and the exact decision tree are in
`benchmarks/results/service-step-15/15b/verification-boundary.md`
(§2 verbatim code, §5 minimal instrumentation).
