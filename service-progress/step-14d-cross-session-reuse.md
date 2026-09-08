# Step 14D Report — Cross-Session / Persistent Reuse (2026-09-08)

## Status

**14D PASS.** Measurement + read-only characterization per
SERVICE-ROADMAP.md §14D: no production changes, no llama.cpp/OpenClaw
patches, no config/tool/prompt edits, no gateway or llama restart
(single-llama discipline; llama pid 7021 constant). Live 262144
contract, 11B(i) fix live (sha b54b13f1d7), FK 0 both agent DBs, idle
gate PASS before the measured turns, config drift vs 13D none (same
contextWindow exception as 14A). Driver: `tools/service_step14d.sh`;
evidence: `benchmarks/results/service-step-14/14d/`.

## Objective

Determine whether useful state can be reused safely across SESSION
boundaries or SERVER-LIFECYCLE boundaries (§14D), after same-session
reuse was qualified in 14A/14B. Questions:

1. When a DIFFERENT session (fresh key) runs against a warm single slot
   holding another session's large prompt, what is reused?
2. When a session returns AFTER another session evicted it from the
   single slot, is its accumulated context restored (RAM prompt cache)
   or re-prefilled?
3. Is cross-session reuse safe (no content leakage between sessions)?
4. What survives server-lifecycle boundaries (gateway restart vs llama
   restart; RAM cache vs --slot-save-path persistence)?

## Changes

- `tools/service_step14d.sh` — retained 14D driver: preflight, workload
  idle gate, six measured turns (S1 build/continuation, S2 fresh
  session/continuation, S1 return, S1 warm control), embedded analysis +
  final verify, per-turn records through the retained runner
  (service_step10a_turn.py).
- `benchmarks/results/service-step-14/14d/` — evidence: preflight /
  final-verify / analysis / evidence-notes.md / per-leg dirs
  (client/record/reply/slots + llama + gateway windows) / prompts.
- `service-progress/step-14d-cross-session-reuse.md` — this report.

## Results

### Environment gates (preflight == final)

llama :18080 200 · n_ctx 262144 · gw :18789 200 (pid 82948, unchanged
across the whole window) · llama pid 7021 · 11B(i) sha b54b13f1d7 ·
kimi FK 0 · poliscopic FK 0 · slot single (--parallel 1) · idle PASS.

### Q1 — Fresh different session vs warm slot: SHARED PREFIX IS REUSED (97%)

S2-t1 was a brand-new session key with a tiny first turn. Its assembled
prompt (16,348 tokens) is ~15.9K of identical OpenClaw serialization
(system prompt + tool schema + bootstrap) that every kimi session
shares, plus ~490 tokens of new user content. The slot still held S1's
27.7K prompt, so llama's LCP reuse matched the shared prefix:

- S2-t1: assembled 16,348 · new 486 · **cached 15,862 · reuse 97.0%** ·
  wall 15.5 s (vs ~438 s for S1's cold context build of the same class).
- Same agent ⇒ same ~15.9K bootstrap ⇒ cross-session first turns are
  nearly free. This is automatic (no session identity in llama; pure
  token prefix).

### Q2 — Return to a session after another session evicted it: UNIQUE CONTENT IS RE-PREFILLED below ~2×bootstrap

S1-t3 returned to S1 after S2's turns (S2-t1/t2, tasks 7898-9181) had
taken over the single slot. First task back:

- S1-t3 task 9395: assembled 27,760 · **new 11,875 · cached 15,885 ·
  reuse 57.2%** · prompt-eval 302.8 s (~5 min @ 39.2 tok/s).

Only the shared ~15.9K bootstrap was reused; S1's session-UNIQUE
accumulated content (~11.9K: the file-read conversation from S1-t1) was
re-prefilled. Subsequent tasks in the same turn (9437-10760) recovered
to 92-98% reuse once S1 re-established in the slot; S1-t4 warm control
ran at 97-98% per task.

Mechanism (matches 14B §2 exactly): the in-RAM prompt cache
(server_prompt_cache, cache_ram_mib default 8192) saves an evicted slot
prompt ONLY when `f_keep = LCP/slot_prompt_len < 0.5`. Here S2 shared
the ~16K bootstrap with S1's 27.7K ⇒ f_keep = 0.573 >= 0.5 ⇒ NO save ⇒
S1's unique tail was discarded at eviction and re-prefilled on return.

Boundary implied: same-agent sessions carry a ~16K common prefix, so
accumulated contexts ≲ ~2×bootstrap (~32K) sit above the 0.5 threshold
and their unique content is NOT cached across an interleave; contexts
well above that (e.g. the 13G poliscopic 48K session) drop below 0.5
when a small session evicts them, ARE RAM-cached, and restore at ~99.9%
(consistent with 14A legC: 48,054 cached / 48,107 assembled after kimi
legs had interleaved). Cross-agent interleave (tiny shared prefix) also
lands below 0.5 and is cached.

### Q3 — Safety: PASS

Reuse is token-prefix only (`n_past = get_common_prefix`,
server-context.cpp:3112): divergent session content is never mixed.
Contamination check on the kimi DB: S1's read request + file content
(`step-13-context-capacity.md`, `D14-S1-`) present in the S1 transcript
and ABSENT from S2; S2's `D14-S2-` markers absent from S1. FK 0. No
compaction/context-shift in any window.

### Q4 — Server lifecycle: gateway restart preserves; llama restart does not

- **Gateway restart** (OpenClaw, separate launchd job): slot KV/state
  and the RAM prompt cache survive — llama pid 7021 stayed constant
  across the two 11B(i) gateway kickstarts (12:47/13:06) and throughout
  14D; the slot retained the 14A task-7620 prompt across them.
- **llama restart**: nothing auto-saves on shutdown. `--slot-save-path
  runtime/state/slot-cache` enables only MANUAL /slots save/load
  actions (server-context.cpp:4501+, 5116-5176); the launchd wrapper
  never calls them. A llama restart therefore forces a cold re-prefill
  of every active session (~48K context ≈ 27-30 min at 13G scale). The
  RAM cache is bounded by cache_ram_mib (default 8192 MiB) and volatile.
- No restart was performed (single-llama discipline); this is read-only
  characterization, consistent with 14B §6.

## Problems

- S1-t3 and S1-t4 hit the 900 s client cap (rc=1, timed_out): the kimi
  agent at 256K went multi-round verbose (12 and 9 llama tasks per
  turn) — same artifact class as 14A legA/legE. NOT a reuse failure:
  per-call llama reuse stayed 0.92-0.98 within those turns; the cap is
  client-side.
- First driver launch (15:02) was reaped mid-turn (exec lifecycle;
  task 7668 cancelled at 4,096 tokens, no client.json). Relaunched
  detached (setsid) at 15:09 on the SAME session key, so the S1
  transcript contains the t1 read request twice (seq 4 aborted, seq 6
  relaunch). S1-t1's first llama task reused the aborted run's 4,142-
  token bootstrap remnant — a mild cross-attempt warm start. Noted for
  transparency; does not affect the S2/S1-t3 measurements (different
  sessions/tasks).
- Measured cross-session miss is BOUNDED and structural (single slot +
  0.5 f_keep threshold), not a defect of the reuse machinery; no
  remediation change was made or is proposed under the no-change
  discipline of §14D.

## Decisions

- No production change, no patch, no restart (single-llama discipline).
- Single-slot ping-pong design (S1→S2→S1) chosen because production
  runs `--parallel 1`; it directly measures the multi-session
  interleave that kimi/poliscopic/aristotle workloads impose on one
  server.
- f_keep-threshold interpretation adopted only after it explained both
  directions of evidence: the 14D S1 loss (0.573 → no save) AND the 14A
  legC restore (48K ≪ 0.5 → save → 99.9%).

## Next Phase

- 14E (production qualification vs Step 13G) is the remaining §14
  substep and requires separate authorization. Its decisive benchmark
  per the roadmap is ~150K existing context + ~1K new tokens; note the
  14D finding that single-slot interleave re-prefills session-unique
  content below ~2×bootstrap — a multi-session production workload may
  see material cross-session re-prefill cost that same-session numbers
  (99%+) do not show.
- If a future step ever needs cross-session restore for mid-size
  sessions, the lever is llama-side (cache_ram_mib / f_keep / slot
  count / --slot-save-path), NOT OpenClaw-side — outside the current
  no-change mandate.

## Reproduction

    bash tools/service_step14d.sh r1

Requires the live 262144 contract (llama pid 7021, gw :18789) and an
idle slot; driver self-gates. Evidence lands under
benchmarks/results/service-step-14/14d/ (preflight.json, analysis.json,
final-verify.json, evidence-notes.md, per-leg windows).
