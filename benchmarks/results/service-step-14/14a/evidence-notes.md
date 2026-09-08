# 14A evidence notes (2026-09-07 21:00-21:53 MST)

Measurement of EXISTING reuse behavior only. No production changes, no
llama.cpp/OpenClaw patches, no config/tool/prompt edits. Live 262144
contract, llama pid 7021 constant across the window, gateway pid 56719,
11B sha 8baf474684, FK 0 both agent DBs, zero config drift vs 13D.
Workload idle gate PASS before the measured legs (slot idle, no other
agent/turn processes).

## Mechanism observed (per-turn, from record.json usage + llama window)

OpenClaw/gateway sends the FULL assembled prompt on every model call.
llama.cpp server caches the prompt prefix in the slot KV and reuses it:
llama log "selected slot by LCP similarity, f_sim_best = 0.9xx
(> 0.100 thold), f_keep = 1.000"; per-task release n_tokens = full
assembled prompt while "prompt processing n_tokens" (progress=1.00) =
only the newly evaluated delta. usage.cacheRead = cached prefix tokens,
usage.input = newly evaluated. No compaction events anywhere; no
truncation (truncated=0 on every release).

## Per-leg results (lastCallUsage: new / cacheRead / total => reuse)

- probe pair cont-t1/cont-t2 (SAME session, small): 13,561 -> 14,196
  assembled; t2 final call 101 new / 14,095 cached / 14,211 (99.3%);
  llama processed 97 new of 14,210. wall 76.5s / 36.6s, rc 0. This is
  the clean small-context + small-suffix pair (see probe-continuation/).
- legA-t1/t2 (driver duplicate of small pair): rc=1 timed_out at the
  900s client cap BOTH turns - the kimi agent at 256K went multi-round
  verbose (8 and 13 model calls, context grew to 35K then 55K assembled)
  and exceeded the client budget mid-turn (doc_status timeout). NOT a
  reuse failure: llama reuse fractions across the calls that ran were
  0.94-0.97 (t1) and 0.996-0.999 (t2). Client-cap artifact like 13F
  pass-1/pass-2; the probe pair is the clean small-context record.
- legB-t1/t2 (medium ctx + suffix, same session): t1 established 18,823
  assembled via a read-tool turn (220.1s). t2 suffix: 29 new / 18,827
  cached / 18,872 => 99.8% reuse; wall 7.8s; llama 1 task.
- legC (LARGE 13G poliscopic session continuation + suffix): assembled
  48,102; 48 new / 48,054 cached / 48,107 => 99.9% reuse; wall 11.4s.
  llama task 6653: prompt processing 44 tokens, release 48,106.
- legC2 (second consecutive suffix on same session): assembled 48,140;
  34 new / 48,106 cached / 48,147 => 99.9%; wall 6.8s.
- legD (fresh-session control, same suffix text as probe t2): assembled
  12,725; 515 new / 12,210 cached / 12,794 => 95.4%. Fresh session still
  reuses the invariant fixed material (system+tools+skills prefix left
  in the slot by earlier kimi sessions) but must evaluate ~400-500 tokens
  of fresh-session serialization + user content - vs ~100 tokens new on
  the same text as a continuing-session suffix.
- legE-t1/t2 (repeated stable-tool-schema engineering turns, same
  session, identical prompts): t1 first run 957.9s (context grew to
  39,568 during tool rounds); t2 identical repeat 137.1s (7x faster):
  final call 2,471 new / 39,796 cached / 42,304 (94.1% on last call;
  overall turn 2 llama tasks vs t1's 10, cumulative cacheRead 79,486).

## Latency attribution

Suffix turns at 18K-48K context cost seconds (legB-t2 7.8s, legC 11.4s,
legC2 6.8s) because only the delta is prefilled: legC prefill of 44 new
tokens took 5.24s (8.4 tok/s at 48K depth - per-token cost scales with
context depth) then short decode. Cold re-prefill of the same 48K at the
measured ~27-30 tok/s depth rate would be ~27-30 minutes; reuse removes
that. Remaining latency is (a) the small delta prefill and (b) decode at
depth - both throughput topics, not reuse misses.

## Reuse-miss conditions observed

1. Fresh session content: anything not previously in the slot prefix is
   evaluated (legD 515 new tokens incl. fresh-session serialization).
2. New user/tool material appended to an established session is the
   delta evaluated (by design; prefix before it is reused at 99+%).
3. Client-side turn caps at 256K (900s) can cut a verbose multi-round
   agent turn before completion (legA) - an agent-behavior/budget
   artifact, not a server reuse miss.
4. No misses correlated with tool ordering, session metadata changes,
   compaction, slot replacement, or server PID change in this window
   (single slot, nothing else ran; pid 7021 constant).
5. KDA recurrent state: no special reuse boundary observed beyond
   ordinary prompt/KV prefix reuse - cacheRead tracks the full assembled
   prefix at 18K and 48K depth alike (99.8-99.9%).

## Files

analysis.json (per-leg parsed llama tasks + usage), per-leg dirs with
record/client/reply/llama+gateway windows/slots, prompts/, preflight.json,
final-verify.json, probe-continuation/ (small-context clean pair + note).
