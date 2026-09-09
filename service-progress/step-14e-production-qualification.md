# Step 14E Report — Production Qualification vs Step 13G (2026-09-08/09)

## Status

**14E PARTIAL — decisive suffix-only property confirmed at 121.7K
assembled context; the roadmap's ~150K decisive leg is BLOCKED by
OpenClaw auto-compaction at ~122K** (framework boundary, not a
llama.cpp/SSD reuse failure). Measurement-only per SERVICE-ROADMAP.md
§14E: no production changes, no llama.cpp/OpenClaw patches, no
config/tool/prompt edits, no gateway/llama restart (llama pid 7021
constant; gw pid 82948 constant). Live 262144 contract, 11B(i) sha
b54b13f1d7, FK 0 both agent DBs, idle gate PASS, zero config drift.
Driver: `tools/service_step14e.sh`; evidence:
`benchmarks/results/service-step-14/14e/`.

## Objective

14E per roadmap: repeat a representative real agent workload and
compare against Step 13G; measure reduction in newly evaluated prompt
tokens, TTFT, and total wall time. Decisive benchmark: large accumulated
context + small suffix, ideally ~150K existing + ~1K new tokens — to
determine whether the system evaluates roughly the suffix rather than
recomputing the entire accumulated context.

## Changes

- `tools/service_step14e.sh` — retained 14E driver: preflight, workload
  idle gate, 14 context-build legs (B1-B14, each reading one large
  poliscopic workspace doc in full into the retained 13G session
  `agent:poliscopic:step13g-kg-maintenance-r1`) + 1 decisive suffix leg
  (S1), per-turn records through the retained runner, embedded analysis
  + final verify.
- `benchmarks/results/service-step-14/14e/` — evidence: preflight /
  final-verify / analysis / auto-compaction-evidence.md / per-leg dirs
  (client/record/reply/slots + llama + gateway windows) / prompts.
- `service-progress/step-14e-production-qualification.md` — this report.

## Results

### Environment gates (preflight == final)

llama :18080 200 · n_ctx 262144 · gw :18789 200 (pid 82948, unchanged
across the whole window) · llama pid 7021 · 11B(i) sha b54b13f1d7 ·
kimi FK 0 · poliscopic FK 0 · loadavg 1.95-2.90 (final) · idle PASS.

### Context build (real agent path, retained 13G session)

The 13G session was resumed after the 14D kimi sessions had evicted its
slot state, so B1 paid a full cold re-prefill (~49.8K assembled, client
cap rc=1 at 1500s — the read still completed server-side). B2-B12 then
grew the session monotonically through real file-read tool results:

| leg | promptTokens (assembled) | usage.input (new) | wall s | notes |
|---|---|---|---|---|
| B1 | 49,819 | 49,819 | 1,501.8* | cold re-prefill; *client cap, read completed |
| B2 | 67,444 | 14,561 | 765.9 | file read (KG-ROADMAP content entered) |
| B3 | 75,716 | 9,304 | 530.7 | |
| B4 | 83,059 | 8,375 | 503.7 | |
| B5 | 90,148 | 8,121 | 525.3 | |
| B6 | 95,474 | 6,358 | 464.1 | |
| B7 | 100,396 | 5,954 | 442.1 | |
| B8 | 105,798 | 6,434 | 489.2 | |
| B9 | 110,129 | 5,363 | 434.0 | |
| B10 | 114,652 | 5,555 | 467.4 | |
| B11 | 121,656 | 8,036 | 649.7 | |
| **B12** | **121,733** | **~589** | **52.1** | **decisive: 121.7K existing + tiny suffix; 99.5% cacheRead** |
| B13 | (compaction) | — | 1,530.9* | *client cap; OpenClaw auto-compaction at 22:56:20 |
| B14 | 48,993 | 6,501 | 323.7 | post-compaction context (~49K) |
| S1 | 49,230 | 753 | 38.3 | suffix leg at 49K (98.5% cacheRead) |

Per-leg llama tasks confirm each build leg evaluated only its newly
read file content against a warm cached prefix (e.g. B11 task 12502:
new 7,440 / assembled 121,660 / reuse 0.94; B12 task 12515: new 589 /
assembled 121,737 / **reuse 0.9952**).

### Decisive suffix-only measurement at 121.7K (B12)

B12 ran against a 121,737-token assembled context and evaluated only
589 new tokens (0.48%): cacheRead 121,148 (99.5%), wall 52.1 s, single
llama task, no compaction. This directly answers the roadmap question
at the largest context the real path sustained: the system evaluated
the tiny suffix, NOT the 121.7K accumulated context.

Reference comparison vs Step 13G (retained gates.json):
13G kg-maintenance cold run: assembled 47,626 · newly evaluated 46,780
(98%) · wall 1,410.7 s. 14E B12 warm at 121.7K: newly evaluated 589
(0.48%) · wall 52.1 s. Same accumulated-session continuation class that
14A legC measured at 48K (44 new / 48,054 cached, 11.4 s) now confirmed
at 121.7K.

### Blocking finding: OpenClaw auto-compaction caps real-path context at ~122K

During B13, at ~121.8K assembled context, OpenClaw's embedded-agent
layer auto-compacted the session (gateway log line 132599,
2026-09-08T22:56:20.988-07:00: `auto-compaction succeeded for
llama-server/kimi-linear-48b; retrying prompt`). After the retry the
session context was ~45-49K (B14 promptTokens 48,993; S1 49,230),
consistent with the compaction policy `keepRecentTokens: 50000`
(openclaw.json agents/defaults/compaction; midTurnPrecheck enabled).
The remaining B14/S1 legs therefore ran against the compacted ~49K
session, and S1 (intended as the ~150K suffix leg) instead re-measured
suffix-only behavior at 49K: 749 new / 48,486 cached / 98.5% reuse /
wall 38.3 s — consistent with 14A legC but not at 150K scale.

Evidence: `benchmarks/results/service-step-14/14e/auto-compaction-evidence.md`.

Implication: through the real OpenClaw agent path on this stack, a
single session's accumulated context is capped by OpenClaw's
auto-compaction policy (~122K observed here, well below llama's 262144
window). The roadmap's ~150K decisive context is not reachable via a
real agent turn without an OpenClaw-side compaction-policy change
(outside 14E's no-change mandate).

## Problems

- The ~150K decisive leg could not be executed: OpenClaw auto-compaction
  fired at ~122K during B13 and reset the session to ~49K. This is a
  framework behavior, not a llama/KV/SSD reuse failure — recorded as a
  production finding and the binding constraint on the roadmap's target.
- B1 and B13 hit the runner's 1500 s client cap (rc=1) on cold/large
  turns (same artifact class as 14A legA/E and 14D S1-t3/t4; server-side
  work continued and the transcript grew). B1's read completed
  server-side (task released 53,399 assembled); B13 was interrupted by
  the auto-compaction retry and the cap.
- The analysis driver's initial 13G-reference lookup read the wrong
  nesting level (gates.json top-level keys were None); corrected in
  place to the nested /context + /kg-maintenance values.
- Shared-server interleave: llama log windows span other agents' tasks
  when a leg's wall time is long (task-id gaps visible in B13's window);
  per-leg attribution in the table uses client/record promptTokens +
  usage (authoritative per turn), with llama tasks as corroboration.

## Decisions

- No production change, no patch, no restart (single-llama discipline).
- Continued the retained 13G poliscopic session (real KG-maintenance
  workload context) and grew it with real workspace-doc reads — the
  closest real-path analogue to the roadmap's "large accumulated
  context" requirement.
- Where the ~150K target was blocked by framework auto-compaction, the
  suffix-only property was reported at the largest context actually
  sustained (121.7K, B12) plus the post-compaction 49K control (S1),
  and the auto-compaction boundary is documented as the production
  envelope.

## Next Phase

- Step 14 closes here (14A-14E). The reuse story is complete and
  consistent: same-session warm suffix-only evaluation confirmed from
  13.5K (14A) through 48K (14A legC) to 121.7K (14E B12); cross-session
  shared-bootstrap reuse 97% and the f_keep<0.5 RAM-cache boundary
  characterized in 14D; gateway restart preserves slot state while
  llama restart forces cold re-prefill (14D/14B).
- If ~150K-context production workloads are ever desired through the
  real agent path, the lever is OpenClaw's compaction policy
  (agents/defaults/compaction: keepRecentTokens / midTurnPrecheck /
  maxActiveTranscriptBytes) — an OpenClaw config change that was
  explicitly out of scope for measurement-only 14E and needs separate
  authorization.
- 14E did not expand into 512K/1M context, decode optimization, model/
  sampler changes, multi-agent concurrency, or Mistral/K3 work.

## Reproduction

    bash tools/service_step14e.sh r1

Requires the live 262144 contract (llama pid 7021, gw :18789), an idle
slot, and the retained 13G poliscopic session. Driver self-gates.
Evidence under benchmarks/results/service-step-14/14e/ (preflight.json,
analysis.json, final-verify.json, auto-compaction-evidence.md, per-leg
windows). NOTE: the build legs intentionally push past ~120K, where
OpenClaw auto-compaction will fire and reset the session — that is the
phenomenon being characterized.
