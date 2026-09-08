# 14D evidence notes — Cross-session / persistent reuse (2026-09-08)

Driver: `tools/service_step14d.sh` (retained). Measurement-only: no
production changes, no patches, no gateway/llama restart. Live 262144
contract, llama pid 7021 constant, 11B(i) sha b54b13f1d7 live, FK 0 both
DBs, idle gate PASS. Gateway pid 82948 unchanged across the whole window
(no gateway restart during 14D).

## Design

Two kimi sessions on the SINGLE llama slot (--parallel 1):

- S1 (`agent:kimi:step14d-S1-r1`): t1 builds a large context by reading
  service-progress/step-13-context-capacity.md; t2 warm suffix.
- S2 (`agent:kimi:step14d-S2-r1`): DIFFERENT session key, fresh; t1 tiny
  first turn; t2 warm suffix. S2's turns evict S1 from the slot.
- S1-t3: RETURN to S1 after S2 evicted it — does llama restore S1's
  accumulated context (RAM prompt cache) or re-prefill it?
- S1-t4: warm control after S1 re-establishes.

## Per-task llama accounting (from llama log windows; assembled = release
n_tokens, cached = assembled - prompt-processing tokens at progress 1.00)

| label | task | assembled | new | cached | reuse | note |
|---|---|---|---|---|---|---|
| S1-t1 | 7676 | 16,406 | 12,264 | 4,142 | 0.252 | first task; 4,142 cached = bootstrap remnant left by the ABORTED first launch (15:02, cancelled at 4096 tok) |
| S1-t1 | 7727 | 23,963 | 8,106 | 15,857 | 0.662 | within-turn 2nd model call reuses 1st |
| S1-t2 | 7743-7886 | 24,006→27,695 | 549-3,083 | 23,457→24,612 | 0.889-0.977 | multi-round warm; per-call high reuse |
| S2-t1 | 7898 | 16,348 | 486 | 15,862 | **0.970** | FRESH session first turn: 97% of assembled prompt reused from slot (shared ~15.9K OpenClaw bootstrap/system/tool prefix) |
| S2-t2 | 7913-9181 | 16,436→30,764 | 92-12,560 | 15,913→30,672 | 0.559-0.997 | 7978 (0.559) = mid-turn agent expansion (+12.5K new content, genuine new work, not a reuse miss) |
| S1-t3 | 9395 | 27,760 | 11,875 | 15,885 | **0.572** | RETURN to S1: only bootstrap prefix reused; S1-unique ~11.9K RE-PREFILLED (pt=302.8s ≈ 5 min @39.2 tok/s) |
| S1-t3 | 9437-10760 | 29,610→33,330 | 605-2,384 | 27,226→31,887 | 0.919-0.980 | after re-establish, warm again |
| S1-t4 | 10774-11928 | 33,863→44,689 | 598-9,251 | 33,265→43,914 | 0.972-0.983 | warm control; 11720 (0.792) = mid-turn agent expansion (+9.3K new) |

## Mechanism finding (cross-session eviction threshold)

llama's in-RAM prompt cache (`server_prompt_cache`, cache_ram_mib
default 8192; server-context.cpp:1300-1370) saves an evicted slot prompt
ONLY when `f_keep < 0.5`, where `f_keep = LCP / slot_prompt_len` (14B
§2). Consequences measured here:

- S2-t1 evicted S1's 27,695-token prompt. LCP(S1,S2) = shared bootstrap
  ~15,862 of S2's 16,348 → f_keep = 15,862/27,695 = 0.573 >= 0.5 → NO
  RAM save → S1's unique ~11.8K tail discarded from the slot.
- S1-t3 return: slot holds S2's prompt; LCP with S1's 27,760 = bootstrap
  ~15,885 → n_past 15,885, re-eval 11,875 (S1-unique content) → 57.2%
  first-task reuse, ~5 min re-prefill.
- Boundary: same-agent sessions share the ~16K bootstrap, so accumulated
  contexts ≲ 2×bootstrap (~32K) sit at f_keep >= 0.5 and their UNIQUE
  content is NOT cached across interleave. Contexts well above ~32K
  (e.g., 13G poliscopic 48K) drop below 0.5 when a small session evicts
  → RAM cache saves them → full restore on return (explains 14A legC:
  48,054/48,107 = 99.9% after kimi legs had run in between).
- Cross-agent interleave (kimi vs poliscopic, small shared prefix) also
  falls below 0.5 → cached → restored (14A legC evidence).

## Safety

- Reuse is token-prefix only (n_past = LCP, server-context.cpp:3112):
  divergent session content is never mixed.
- Contamination check on the kimi DB: S1's file-read request and content
  (`step-13-context-capacity.md`, `D14-S1-`) present in S1 transcript,
  ABSENT from S2 transcript; `D14-S2-` markers absent from S1. FK 0.
- No compaction/context-shift in any window (all windows grep 0).

## Server-lifecycle (read-only characterization; NO restart performed)

- Slot KV/state and the RAM prompt cache are in-memory only. They
  survive OpenClaw gateway restarts (separate launchd job; llama pid
  7021 constant across the two 11B(i) gateway kickstarts 12:47/13:06 and
  throughout 14D; slot retained the 14A task-7620 prompt across them).
- They do NOT survive a llama-server restart: nothing auto-saves on
  shutdown. `--slot-save-path runtime/state/slot-cache` enables only
  MANUAL /slots save/load actions (server-context.cpp:4501+, 5116-5176);
  the launchd wrapper does not call them. A llama restart therefore
  forces a cold re-prefill of every active session (~48K context ≈
  27-30 min at 13G scale).
- RAM cache bound: cache_ram_mib default 8192 MiB; slot KV additionally
  limited by n_ctx 262144.

## Artifacts (documented, not reuse failures)

- S1-t3/S1-t4 rc=1 timed_out at the 900 s client cap: the kimi agent at
  256K went multi-round verbose (12 and 9 llama tasks per turn), same
  artifact class as 14A legA/legE. Per-call llama reuse stayed high
  (0.92-0.98); the cap is client-side, not a server reuse failure.
- First driver launch (15:02) was reaped mid-turn (task 7668 cancelled
  at 4,096 tokens; no client.json). Relaunched detached at 15:09 on the
  SAME session key, so S1 contains the t1 read request twice (seq 4 from
  the aborted run, seq 6 from the relaunch). S1-t1's first llama task
  reused the 4,142-token bootstrap remnant of the aborted partial — a
  mild cross-attempt warm start, noted for transparency; it does not
  affect the S2/S1-t3 reuse measurements.

## Environment (preflight == final)

llama 200 · n_ctx 262144 · gw :18789 200 · llama pid 7021 · gw pid 82948
· 11B(i) sha b54b13f1d7 · kimi FK 0 · poliscopic FK 0 · config drift vs
13D: none (contextWindow exception only, same as 14A).
