# Step 12 Report: Qualify Decode Throughput

## Status

PASS (2026-09-06). Decode throughput was measured through the real
OpenClaw agent path on representative Step 9-11 workloads and meets the
promoted baseline for the workloads where decode is the binding cost.
Runtime remains frozen; no decode-optimization problem is opened. STOPPED
at the Post-Step-12 gate.

## Objective

Per SERVICE-ROADMAP.md §12, determine whether the current Kimi generation
speed is sufficient for productive OpenClaw use and whether further decode
optimization is justified by measured user impact. The primary question:
is decode throughput still a meaningful usability bottleneck after response
quality, TTFT, and usable context have been qualified?

## Environment (unchanged through the step)

- Production gateway :18789 (healthz 200 throughout), llama-server :18080
  (health 200, n_ctx 65536), live W4 / 8 GiB expert-cache config.
- Step 11B redaction fix live (dist sha 8baf4746...), `PRAGMA
  foreign_key_check` = 0 rows at start and end.
- No config, plist, llama-server, model, plugin, or 11B patch changes.

## Method

Workloads are the staged Step 9-11 prompts (`P3.md`, `R1.md`,
`C1-s12.md`, `G1.md`), each run through the real agent
(`openclaw agent --agent kimi --model llama-server/kimi-linear-48b`), one
fresh session per leg, with llama-server + gateway log windows captured by
the retained instrumented runner (`tools/service_step10a_turn.py`), driven
by `tools/service_step12_leg.sh` (retained; resumable). Repeat reps were
added for the decode-relevant classes: short-p3 x2, res-r1 x2,
eng-c1s12 x3, long-g1 x1. Decode/prefill/TTFT values are parsed from the
llama-server `print_timing` lines in each leg's window log by the retained
parser `benchmarks/results/service-step-12/parse_decode.py` (output
`decode-summary-final.json`); TTFT proxy = first task's prompt-eval time
(slot-delta portion, per LCP slot reuse) + one token.

## Results

Measured decode rate (llama eval time):

| class | reps | decode tok/s | median |
|---|---|---|---|
| short-p3 (single-number answer) | 2 | 10.69, 10.39 | 10.54 |
| res-r1 (read 2 files -> 3 bullets) | 2 | 8.40, 8.14 | 8.27 |
| eng-c1s12 (write module + compile) | 3 | 8.61, 9.29, 9.33 | 9.29 |
| long-g1 (94 KB read -> bullets) | 1 | 7.43 (partial) | 7.43 |

Wall-time split by workload class (decode vs prefill share of llama-model
time in the turn):

- short-p3: prefill 48-59% (TTFT-bound), decode 29-42%; wall 19.5-25.3 s;
  TTFT proxy ~11.6-12.3 s; replies 202-397 chars.
- res-r1: prefill 75-78%, decode 21-23%; wall 124.6-220.1 s; TTFT proxy
  ~11.7-13.0 s; replies 976-1428 chars.
- eng-c1s12: decode 68-70%, prefill 26-28%; wall 92.5-162.0 s (5-8 llama
  tasks per turn = multi-call tool turns); TTFT proxy ~14.9-15.8 s;
  replies 451-613 chars.
- long-g1: prefill 76%, decode 20%; runner-capped at 900 s (rc=1, still
  processing at cutoff; 24 llama tasks, 31.8k prefill tokens); TTFT proxy
  14.1 s; partial reply 151 chars.

Model time is 90-98% of wall for completed legs; gateway/llama-fetch
overhead is small (36-100 ms typical), so tools + plumbing are not the
wait.

## Attribution (the §12 five questions)

1. Decode fraction of representative turn time: engineering 68-70%
   (decode-bound), short answers 29-42%, research 21-23%, long tool-heavy
   20%. Decode is the dominant user-visible wait only for engineering
   turns.
2. Short answer after first token: *** tokens at ~10.5 tps = ~6-11 s
   of decode after ~12 s TTFT; total ~20-25 s. Interactive and fine.
3. Normal engineering answer: decode 63-114 s at 8.6-9.3 tps within
   92-162 s multi-call turns. This is the model's core use case and it is
   decode-bound at the low edge of the promoted band.
4. Long tool-heavy answer (94 KB read): exceeds the 900 s runner cap
   (agent still processing); decode is only ~20% of elapsed, prefill 76%.
   Not decode-bound; it is prefill/context-growth bound across the tool
   loop (each successive read re-processes a growing context).
5. Would a plausible throughput improvement materially change UX? Only for
   the engineering class (~70% decode at ~9.3 tps; ~1.3-1.5x would cut
   ~20-40 s per 92-162 s turn). Short answers are TTFT-bound, research and
   long tool-heavy turns are prefill-bound, so decode work would not fix
   their wall time.

## Classification

PASS - decode throughput is adequate in practice:

- The decode-bound class (engineering, the intended productive workload)
  decodes at median 9.29 tps, inside the promoted "approximately 9-11
  tok/s" band at its low edge (reps 8.61-9.33).
- The short-answer class decodes at 10.54 tps, inside the band.
- Classes that measure below the band (research 8.27, long-context 7.43)
  are not decode-bound: prefill/TTFT is 75-78% of their wall time, so
  decode improvement would not change their latency. A decode-optimization
  problem would misattribute those waits.

Per the §12 decision rule ("if approximately 9-11 tok/s is adequate in
practice, Step 12 should PASS and the runtime should remain frozen; do not
continue runtime optimization merely because higher tok/s is technically
possible"), no decode-optimization problem is routed to ROADMAP.md and the
runtime remains frozen.

## Problems

- long-g1 did not complete within the runner's 900 s per-turn cap (rc=1,
  timed_out). Its decode figure (7.43 tps) is a partial measurement from
  the processing window. This is recorded as a prefill/context-bound
  observation, not a decode failure; a bounded rerun with a longer window
  would be needed for a completed long-tool-heavy data point (not run -
  out of scope for the decode question).
- Class decode medians come from 1-3 reps; eng-c1s12 has 3 reps and is the
  primary evidence class. No system state changed during the battery; the
  gateway/llama were quiescent between legs (slots idle).

## Decisions

- Classify §12 PASS; runtime frozen; no decode optimization opened.
- Retain the workload battery, parser, prompts, and per-leg windows as
  reproducible evidence.
- No changes to production files of any kind during this step (verified by
  final gate: sha 8baf4746..., FK 0, gw 200, llama 200).

## Next

Report PASS at the Post-Step-12 gate with evidence. The roadmap's
Post-Step-12 Decision section (production-state summary) is not started
without authorization.

## Reproduction

```
# battery (all four classes; resumable)
bash tools/service_step12_leg.sh

# individual leg example
python3 tools/service_step10a_turn.py benchmarks/results/service-step-12/eng-c1s12 \
  eng-c1s12-r4 benchmarks/results/service-step-12/prompts/C1-s12.md \
  agent:kimi:step12-eng-c1s12-r4 --agent kimi --timeout 900 \
  --llama-log /tmp/kimi-llama-server.launchd.log \
  --gateway-log "$HOME/Library/Logs/openclaw/gateway.log"

# parse llama windows -> decode-summary-final.json
python3 benchmarks/results/service-step-12/parse_decode.py
```

Evidence: `benchmarks/results/service-step-12/` - `decode-summary-final.json`,
`step12-verdict.json`, `parse_decode.py`, `prompts/`, per-leg
`*.client.json`, `*.llama.window.log`, `*.gateway.window.log`,
`*.record.json`, `*.slots.json`, `*.reply.txt` under
`short-p3/`, `res-r1/`, `eng-c1s12/`, `long-g1/`.
