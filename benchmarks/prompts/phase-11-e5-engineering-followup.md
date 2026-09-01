# Phase 11 E5 — warm follow-up prompt (run B)
#
# Same prefix as run A (full prompt file + follow-up appended) so the
# llama-server slot can reuse the cached prefix (cache_prompt / n_cache_reuse
# provider params), exercising prefix/prompt-cache reuse on a subsequent turn.

Act as a senior C++ engineer reviewing a llama.cpp expert-streaming runtime.

The following function loads one routed MoE expert slice into a zero-copy
cache slot:

    void load_expert(int layer, int expert_id, int slot) {
        const size_t off = tensor_abs_offset(layer) + expert_id * PER_EXPERT;
        pread(fd, staging, PER_EXPERT, off);
        memcpy(slot_ptr(slot), staging, PER_EXPERT);
        slot_expert[slot] = expert_id;
    }

and the caller then builds `slot_ids` from the router output and feeds the
graph. Assume slot reuse/eviction is handled elsewhere.

List, concretely and tersely:
1. the two most likely correctness risks in this data path,
2. the one most likely performance risk,
3. the minimal fix for each.

No more than 200 words total. Use bullet points.

Follow-up: which of the correctness risks you listed would produce garbage
logits *without* any wrong kernel arithmetic, and why?
