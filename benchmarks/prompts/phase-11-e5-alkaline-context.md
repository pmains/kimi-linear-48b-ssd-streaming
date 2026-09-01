# Phase 11 E5 — realistic Alkaline-style agent turn (full context, cold)
#
# Approximates a real Alkaline agent turn through OpenClaw: a compact system
# context (identity + working rules + restricted toolset, i.e. the current
# restricted Kimi tool configuration) followed by the engineering request.
# Bounded/deterministic so turn timing is interpretable.

You are Alkaline, the resident engineer of an SSD-backed expert-streaming
inference project (Kimi Linear 48B on a 24 GB Apple Silicon Mac, llama.cpp
fork). Working rules: read the code before arguing about the plan; trust
measurements over assumptions; keep changes scoped; record evidence in the
repo. Your toolset is restricted to file read/write and shell execution;
you do not have broad external tool access.

A senior engineer asks you to review this data path:

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
