# Step 11 Design — Qualify Usable Agent Context at Production Config

Date: 2026-09-05 · Author: Alkaline (agent) · Authorization: Pete *** MST
Repo: `github.com/pmains/kimi-linear-48b-ssd-streaming` <!-- project: github.com/pmains/kimi-linear-48b-ssd-streaming -->

## Objective

Determine how much of the aligned 64K window the real OpenClaw agent (kimi,
llama-server/kimi-linear-48b MXFP4, `--ctx-size 65536`, launchd 64K server,
`tools.loopDetection.enabled: true`) can use productively under **realistic
sustained workloads**, and whether the service remains stable as sessions
grow.

Roadmap §11 scope note: Stage 8 already validated 128K *native-runtime*
capacity; Step 11 qualifies the *agent* at the **current production
configuration** — the aligned 64K provider window — and does NOT attempt
128K/256K agent context (256K gate unchanged, not triggered).

**Constraints (from authorization): no change to context size, sampler,
prompting, or liveness configuration.** No production config/code change at
all. All changes are throwaway workload prompts + retained drivers under
`benchmarks/results/service-step-11/` and `tools/`.

## Mechanism validation (done before this design)

Same-key multi-turn continuation is the load-bearing mechanism. Verified live
via `service_step10a_turn.py` (retained instrumentation: llama window,
gateway window, /slots, final CLI doc, client.json):
- t1 (session `agent:kimi:step11smoke-64k-s1`): rc=0, wall 58.8 s,
  promptTokens 14,759, cacheRead 25,014
- t2 (same key): rc=0, wall 64.3 s, promptTokens 17,457, cacheRead 29,658

→ context grows across same-key turns and per-turn token/cache metrics are
captured. Evidence: `benchmarks/results/service-step-11/smoke/`.

## Workload design (realistic, not synthetic format probes)

Three sustained session scenarios, each a chain of same-key turns, run
sequentially on the single-slot 64K server. Fresh `step11` session keys per
scenario; absolute paths in prompts (headless cwd is `~/.openclaw`).

### Session R — research conversation (~10 turns, key `agent:kimi:step11-64k-research`)
Realistic repo-research dialogue with tool use (read/exec/sessions_history),
follow-ups that require retrieving earlier turns, one obsolete-instruction
test, and a final synthesis that must reference turn-1 material. Exercises:
multi-turn conversation, retention, instruction-vs-history discrimination,
tool results, growing context.

### Session C — coding work (~8 turns, key `agent:kimi:step11-64k-code`)
Build + test a small Python module in `benchmarks/results/service-step-11/work/code/`
with write/exec/read cycles: write module → write tests → run (fail) → fix →
run (pass) → add feature → verify → summarize. Exercises: substantial tool
use, iteration, error recovery, multi-turn state.

### Session G — context growth → auto-compaction (~10–12 turns, key `agent:kimi:step11-64k-growth`)
Sequential big-file digest turns (SERVICE-ROADMAP.md ~85 KB, service-progress
reports, benchmarks summaries) so promptTokens climbs past the auto-compaction
threshold (`agents.defaults.compaction.keepRecentTokens: 50000`, mode
default) mid-session; then continue ~3 turns post-compaction to test
stability/retention after a real compaction event. Exercises: context growth,
cache reuse under growth, auto-compaction, post-compaction stability.

## Measurements (per turn, from retained instrumentation)

| Metric | Source |
|---|---|
| rc / wall / timed_out | client.json |
| promptTokens (assembled context) | .out agentMeta |
| usage input/output/cacheRead/total | .out agentMeta |
| contextTokens (resolved) | .out agentMeta |
| TTFT / prefill (first prompt-eval ms + tokens) | llama window |
| decode tok/s | llama window eval lines |
| llama task count / turn | llama window |
| model-fetch latency | gateway window |
| slot cache before/after | slots.json |
| compaction events | gateway window (auto-compaction lines) + promptTokens drop |
| quality | rc + reply artifact + targeted recall checks |
| failures | rc!=0, timeout, liveness/replay flags, loop events |

Session-level: promptTokens progression vs turn; wall vs promptTokens
(latency curve); cacheRead vs promptTokens (reuse curve); compaction point;
usable-context ceiling classification (technical/useful/practical per §11).

## Acceptance (maps to roadmap §11)

1. How much working context does the agent actually need/use? — from
   scenario turns and recall results.
2. Response quality as context grows? — rc + recall/artifact checks per
   promptTokens bucket.
3. Retrieval/prioritization of relevant info? — explicit cross-turn recall
   questions.
4. Latency cost of larger context? — wall + TTFT vs promptTokens curve.
5. Current useful/practical ceiling within 64K? — classify.
6. 256K evidence question — out of scope (production window is 64K);
   recorded as not triggered.

## Procedure / STOP

1. Smoke test (done). 2. Design doc (this). 3. Prompts + leg driver
(`tools/service_step11_leg.sh`, resumable) + analyzer
(`tools/service_step11_analyze.py`). 4. Run leg detached (single-slot,
sequential; long turns up to 1200 s). 5. Analyze. 6. Update
SERVICE-ROADMAP.md §11 + report `service-progress/step-11-usable-context.md`
+ machine-readable `benchmarks/results/service-step-11/`. **STOP for review.
No production change.**
