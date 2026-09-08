# Step 14A Report — Measure Existing Reuse Behavior (2026-09-07)

## Status

**14A PASS.** Measurement-only step per SERVICE-ROADMAP.md §14A: no
production changes, no llama.cpp/OpenClaw patches, no config/tool/prompt
edits. Live 262144 contract (llama pid 7021 constant across the window,
gateway pid 56719, 11B sha 8baf474684, FK 0 both agent DBs, zero config
drift vs 13D, workload idle gate PASS before the measured legs). Driver:
`tools/service_step14a.sh`; evidence:
`benchmarks/results/service-step-14/14a/`.

## Objective

Characterize the prefix/KV/recurrent-state reuse that already exists
through the real OpenClaw agent path, using controlled consecutive turns
in the same agent/session plus a fresh-session control, against the same
live llama-server PID. Answer the §14A acceptance questions and classify
PASS/PARTIAL/FAIL.

## Changes

- `tools/service_step14a.sh` — retained 14A driver: preflight, workload
  idle gate, five leg types (legA small ctx + suffix pair, legB medium
  accumulated ctx + suffix pair, legC/legC2 continuation of the retained
  13G poliscopic session at ~47.6K established ctx, legD fresh-session
  control, legE repeated stable-tool-schema engineering pair), per-turn
  records through the retained runner, embedded analysis + final verify.
- `benchmarks/results/service-step-14/14a/` — full evidence: preflight /
  final-verify / analysis / summary json, evidence-notes.md, per-leg
  dirs (record/client/reply/slots + llama + gateway windows), prompts/,
  `probe-continuation/` (same-key continuation probe with note).
- `service-progress/step-14a-existing-prefix-reuse.md` — this report.

## Results

### Legs (all through the real agent path, ctx 262144 resolved, winner
llama-server, no fallback, liveness working except noted, 0 compaction)

| leg | turn | rc | wall | assembled (promptTokens) | last-call new / cacheRead / total | reuse |
|---|---|---|---|---|---|---|
| probe | cont-t1 (small establish) | 0 | 76.5s | 13,561 | 105 / 13,456 / 13,561 | 99.2% |
| probe | cont-t2 (small suffix) | 0 | 36.6s | 14,196 | 101 / 14,095 / 14,211 | 99.3% |
| legA | t1 (small, driver dup) | 1* | 902.7s | 35,240 | 1,179 / 34,061 / 35,328 | 96.4% |
| legA | t2 (small suffix, driver dup) | 1* | 906.2s | 55,217 | 45 / 55,172 / 55,269 | 99.8% |
| legB | t1 (medium ctx build) | 0 | 220.1s | 18,823 | 6,032 / 12,791 / 18,828 | 67.9% |
| legB | t2 (medium suffix) | 0 | 7.8s | 18,856 | 29 / 18,827 / 18,872 | 99.8% |
| legC | large suffix (13G session cont.) | 0 | 11.4s | 48,102 | 48 / 48,054 / 48,107 | 99.9% |
| legC2 | large suffix #2 | 0 | 6.8s | 48,140 | 34 / 48,106 / 48,147 | 99.9% |
| legD | fresh-session control | 0 | 27.6s | 12,725 | 515 / 12,210 / 12,794 | 95.4% |
| legE | t1 repeated tool turn | 0 | 957.9s | 39,568 | 2,471 / 37,097 / 39,691 | 93.5% |
| legE | t2 identical repeat | 0 | 137.1s | 42,267 | 2,471 / 39,796 / 42,304 | 94.1% |

\* legA hit the 900s client cap both turns (rc=1, timed_out) because the
kimi agent at 256K went multi-round verbose (8 and 13 model calls,
context growing to 35K/55K assembled) and exceeded the client budget
mid-turn. NOT a reuse failure — llama reuse fractions across completed
calls were 0.94–0.97 (t1) and 0.996–0.999 (t2); doc_status timeout,
same artifact class as 13F pass-1/pass-2 client caps. The probe pair is
the clean small-context record.

### Server-side reuse evidence (llama window per task)

Per-task release `n_tokens` = full assembled prompt while the final
`prompt processing, n_tokens = X, progress = 1.00` line = only the newly
evaluated delta. Examples:

- legC (task 6653): prompt processing **44 tokens**, release 48,106 →
  48,062 tokens served from cache.
- legC2 (task 6662): prompt processing **30 tokens**, release 48,146.
- Selection lines: `selected slot by LCP similarity, f_sim_best =
  0.9xx (> 0.100 thold), f_keep = 1.000` — llama.cpp slot KV prefix
  reuse is the operating mechanism; OpenClaw re-sends the full assembled
  prompt each model call and llama reuses the cached prefix.

## Acceptance answers

1. **What reuse mechanism is operating today?** llama.cpp server-side
   slot KV cache with LCP-based prefix reuse. OpenClaw/gateway sends the
   full assembled prompt on every model call; llama-server matches the
   cached prefix (f_sim_best threshold 0.100, f_keep 1.000) and
   evaluates only the delta. usage.cacheRead = cached prefix tokens,
   usage.input = newly evaluated. No compaction events; truncated=0 on
   every release.
2. **What fraction of a stable accumulated prompt is normally reused?**
   Same-session consecutive turns: 99.2–99.3% at ~13.5K, 99.8% at
   ~18.8K, 99.9% at ~48K assembled — the suffix turns evaluate only tens
   of new tokens (29–48) of the accumulated prompt. A fresh session
   still reuses the invariant fixed material (~95.4% incl. system +
   tools + skills left in the slot by earlier same-agent sessions).
3. **What causes reuse misses?** (a) genuinely new content — fresh
   session serialization + user text (legD: 515 new tokens vs ~100 for
   the same text appended to a continuing session); (b) the delta of new
   user/tool material in an established session is the evaluated part by
   design (that is correct behavior, not a miss); (c) client-side turn
   caps at 256K can truncate a verbose multi-round agent turn before
   completion (legA) — an agent-behavior/budget artifact, not a server
   reuse miss. In this window no misses correlated with tool ordering,
   session metadata changes, compaction, slot replacement, or server PID
   change (single slot, nothing else ran; pid 7021 constant).
4. **How much latency is attributable to newly evaluated suffix vs
   unnecessary re-prefill?** Suffix turns at 18K–48K cost seconds
   (legB-t2 7.8s, legC 11.4s, legC2 6.8s) because only the delta is
   prefilled. Cold re-prefill of the same 48K context at the measured
   depth rate (~27–30 tok/s) would be ~27–30 minutes; reuse removes that.
   Remaining latency is the small delta prefill (44 tokens @ 8.4 tok/s
   at 48K depth — per-token cost scales with context) plus decode —
   throughput behavior, not avoidable re-prefill.
5. **Is there a material optimization problem worth opening?** For the
   primary real-world pattern (established session + small new work), no:
   reuse is already ~99.9% effective and only the true delta is
   evaluated. No avoidable same-session re-prefill was observed. KDA
   recurrent state imposed no observable extra reuse boundary beyond
   ordinary prompt/KV prefix reuse (cacheRead tracks the full assembled
   prefix at 18K and 48K depth alike).

## Problems

- legA (driver duplicate of the small-context pair) hit the 900s client
  cap on both turns — verbose multi-round kimi-agent behavior at 256K
  exceeding the client budget mid-turn (8–13 model calls per turn).
  Evidence retained with per-call reuse fractions; the clean
  small-context record is the probe pair (cont-t1/t2, rc 0 both, 99.3%
  reuse). Noted in evidence-notes.md; not a runtime or reuse failure.
- llama /slots `n_prompt_tokens_cache` reports 0 in post-task snapshots
  even when the next request reuses ~48K tokens; the authoritative reuse
  signals are per-task llama print_timing/release lines and
  usage.cacheRead. Documented; mechanism detail belongs to 14B.

## Decisions

- Measurement only: no production change, no patch, no config/tool/prompt
  edit anywhere in the window (preflight/final-verify confirm zero drift
  vs the 13D snapshot, FK 0, pid constant).
- Same-session consecutive turns implemented by reusing one session key
  across successive runner invocations; the probe validated this
  continuation semantics first (cont-t1 → cont-t2: context grew
  13,561 → 14,196 on the same key).
- legC reuses the retained 13G poliscopic session (agent
  poliscopic:step13g-kg-maintenance-r1) as the real large accumulated
  context — the roadmap's designated real-workload reference (~47.6K
  established) — and continues it with tiny suffixes.

## Next Phase

14A evidence supports moving to §14B (characterize the server-side reuse
mechanism: llama slot/KV pool semantics, the LCP selection threshold and
f_keep path, cacheRead accounting, KDA recurrent-state interaction) —
the mechanism details 14A could only observe indirectly. 14A found no
avoidable same-session reuse miss, so any 14C stabilization work should
first confirm a real avoidable-miss condition per the roadmap. Per
instruction: STOP for review after 14A. Do not begin 14B or make
production changes without explicit authorization.

## Reproduction

```bash
bash tools/service_step14a.sh   # probe + 5 leg types + analysis, ~1-2h window
# evidence: benchmarks/results/service-step-14/14a/
# small-context clean pair: benchmarks/results/service-step-14/14a/probe-continuation/
```
