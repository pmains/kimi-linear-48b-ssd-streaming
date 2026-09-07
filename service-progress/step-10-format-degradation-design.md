# Step 10 Design — Why the Real Agent Path Degrades Exact-Format Compliance

## Status

PLAN (pre-registered 2026-09-04 before any Step 10 execution). Supersedes the
roadmap's former Step 10 (TTFT), which is deferred to Step 10A by owner order.
No production patch is authorized by this step: the deliverable is a
determination with retained evidence. Step 10 must STOP for review before any
new production patch.

## Owner Order (Pete, 2026-09-04 16:46 MST)

- Treat Step 9D as PASS; retain the 9D patch. Do not revisit the directive fix.
- Focus Step 10 on the remaining real-path response-quality failures:
  P1 punctuation loss (`PLATANOS` vs `PLATANOS!`) and P1/P2 prose/fenced-output
  behavior (prose-wrapped replies, ```json```-fenced objects).
- Determine why the full OpenClaw agent path degrades exact-format compliance
  relative to the reduced baseline (the frozen 9B/9C C1a-D offline cell:
  P1 3 terse bare `PLATANOS`, P2 3 exact `{"ok": true}`).
- Keep sampler effects and agent-environment effects experimentally separate.
- Preserve the 9D long wall times (P1-r2 532.1 s, P2-r2 371.3 s) as a separate
  performance issue: Step 10 does NOT cover latency (that is deferred Step 10A
  TTFT territory). Wall times are recorded as covariates only.
- Update SERVICE-ROADMAP.md and write the Step 10 plan/report artifacts.
- Stop for review before making any new production patch.

## Evidence Entering Step 10 (frozen baselines, all at llama-default sampler)

Frozen real-path (9D verify, post-patch headless fresh sessions, n=3/probe):

| probe | PASS | families (n=3) |
|---|---|---|
| P1 | 0/3 | 2 terse-wrong bare `PLATANOS`, 1 prose-wrapped (commentary sentence) |
| P2 | 1/3 | 1 exact `{"ok": true}`, 1 prose-wrapped w/ embedded fenced json, 1 terse-wrong fenced ```json``` |

Frozen reduced baseline (9B/9C offline C1a-D, n=3/probe, 64K same server):

| probe | PASS | families (n=3) |
|---|---|---|
| P1 | 0/3 | 3 terse-wrong bare `PLATANOS` |
| P2 | 3/3 | 3 exact |

Attribution already established by 9B (n=3 cells): the `!`-drop appears at C0
(bare prompt, llama defaults temp 0.8) → sampler-contributing; prose/fenced
families appear as environment layers are added (system text, tool catalog).

## Why the delta question is still open

The real-path distribution (P2 1/3 exact; P1 +1 prose-wrapped) is not yet
explained by the frozen offline cells because three experimental gaps remain:

1. **Payload mismatch between "reduced baseline" and the current real headless
   prompt.** C1a-D removed the ENTIRE `## Assistant Output Directives` section.
   The 9D production gate removes only TWO lines from that section
   (`- Native reply starts with ...`, `- Directives stripped before render ...`)
   when `hasDeliverySurface == false`. The current real headless prompt
   therefore still contains the section header and three other directive-ish
   bullets (`MEDIA:` line, "outside fences/Markdown" line, `[[audio_as_voice]]`
   line). No offline cell yet models the true current headless prompt.
2. **n=3 everywhere.** Binomial noise alone can produce the observed P2 1/3 vs
   3/3 split under a moderate true exactness rate. Step 10 must enlarge n on
   both sides of the comparison.
3. **Sampler vs environment are confounded in the historical comparison.** Both
   sides ran llama defaults (temp 0.8), so the sampler is nominally equal — but
   the sampler's *variance contribution* was never quantified at the
   environment-equivalent payload, and no greedy (temp 0) floor exists at that
   payload.

## Hypotheses (pre-registered)

- H-S1 (sampler-primary): Under the current headless-equivalent prompt (E2),
  the greedy floor (temp 0) returns exact outputs for P1 and P2 at high rate;
  exact-format rate falls monotonically as temperature rises. The real-path
  shortfall is then mostly sampler-mode variance at llama defaults, amplified
  by the agent path's inability to set a lower temperature (it sends no sampler
  fields; llama defaults apply — verified in openclaw.json model params and
  dist request construction).
- H-S2 (sampler-not-sufficient): At default temp 0.8 the offline E2/E2T cells
  still show materially higher exact-format compliance than the real path →
  the residual delta is agent-environment, not sampling noise.
- H-E1 (surviving section lines): The three non-gated bullets + header of
  `## Assistant Output Directives` (still present in the real headless prompt)
  bias P1/P2 toward commentary/fenced output. Tested by C1 vs E2 (two-line
  delta = the 9D patch semantics) and E2 vs C1a-D (whole-section delta).
- H-E2 (tool catalog): Declaring the 29-tool catalog (E2T) shifts the P2
  family distribution (9B saw tools counteract fences at C2) and adds
  prose-style behavior; measured at the headless-equivalent system.
- H-E3 (trajectory/multi-call): Prose-wrapped replies in the real path come
  from later model calls after commentary/tool rounds, not from the first call.
  Tested by C3-style two-call replay of any prose-producing real-path case and
  by comparing single-call offline families with real-path per-call walls
  (short-wall real replies are single-call; long-wall replies are multi-call —
  documented, not investigated for latency).

## Design

Everything runs against the SAME live llama-server endpoint the real agent
uses (`127.0.0.1:18080`, 64K, MXFP4, --parallel 1, llama-default sampler when
no sampler fields are sent). Frozen prompts/expected/scorer untouched.

### Payload conditions (offline, OpenAI wire shape)

| cond | system prompt | tools | meaning |
|---|---|---|---|
| C0 | none (user only) | none | bare floor |
| C1 | full retained agent system text (9A-era verbatim = current full text; 9D changed no text, only a gate) | none | full system |
| C1a-D | full minus ENTIRE Assistant Output Directives section | none | 9B/9C reduced baseline (byte-identical validation vs retained payload_sha) |
| E2 | full minus the TWO gated reply-directive lines only (9D headless semantics) | none | true current headless probe prompt model |
| E2T | E2 | 29-tool wrapped catalog (tools-full-29.json) | true current headless prompt + tool declaration |

E2 is the single new condition 9B/9C never ran; it is the experimental bridge
between the reduced baseline (C1a-D) and the real headless path.

### Leg 1 — sampler axis (offline)

Cell set: payloads {C0, E2} x probes {P1, P2} x temperature {0.0, 0.8, 1.6},
n=10 per cell. temp 0.0 cells send `"seed": 42` (deterministic greedy floor;
assert determinism: 10/10 identical outputs). temp 0.8/1.6 cells leave seed at
server default (random per call — emulates real-path variance). No other
sampler fields are sent (llama defaults otherwise). 120 calls.

### Leg 2 — environment axis (offline, at the real path's default temp 0.8)

Cell set: payloads {C0, C1, C1a-D, E2, E2T} x probes {P1, P2}, n=10 per cell,
no sampler fields (identical to the agent path's outbound request). 100 calls
(C0/E2 shared with Leg 1's temp-0.8 cells — run once, counted once).

### Leg 3 — real path (reference distribution)

P1/P2 x 3 fresh headless sessions (no --deliver, model pinned
`llama-server/kimi-linear-48b`, 64K) via the retained turn runner, NEW session
namespace (step10), same dist state as 9D (gateway PID 10951 started after the
9D patch; no dist change since). Combined with the frozen 9D verify rows
(n=3/probe) → n=6/probe real-path distribution. Classification: frozen scorer
semantics (service_step09d_classify.py).

### Analysis plan

- Determinism assertion for temp-0 cells (identical text 10/10).
- Family tables per cell; exact-format rate with Wilson 95% CI.
- Attribution rules:
  - If temp-0 exactness high AND temp-0.8 offline exactness high (>~CI
    overlap with real path) → H-S2 holds: residual delta is agent-environment;
    localize to remaining layer differences (E3 trajectory; unmodeled request
    scaffolding) and report the candidates, no patch.
  - If temp-0 exactness high AND temp-0.8 offline E2/E2T exactness low and
    statistically indistinguishable from the real path → H-S1 holds: the
    degradation vs the reduced baseline is the sampler mode the agent path is
    forced to run (llama defaults), and the reduced baseline's 3/3 exact was
    small-n luck at a favorable draw; recommend (for review, not this step)
    per-model sampler configuration as the only lever, since env layers are
    already minimal at E2/E2T.
  - C1 vs E2 vs C1a-D contrasts isolate the directive-section text effects
    (H-E1); E2 vs E2T isolates the tool-catalog effect (H-E2).
- Wall times recorded per call (covariates; latency analysis deferred).

## Constraints

- No dist/config/tool/prose/sampler-default changes. No production patch.
  Offline sampler cells vary `temperature`/`seed` ONLY in the request body to a
  llama-server research endpoint (same endpoint; request-scoped fields — no
  server or config change; the production agent path never sends sampler
  fields, so production behavior is untouched).
- 9D directive fix untouched. Worker.mjs copies untouched.
- Real-path leg identical in shape to 9D verify (fresh sessions, no --deliver,
  no stale-gateway issue: gateway 10951 predates no dist change).
- Single-slot server: offline ladder and real leg run SEQUENTIALLY (no
  overlap), with load covariates recorded before/after.

## Artifacts

- Design: `service-progress/step-10-format-degradation-design.md` (this file).
- Drivers (retained before execution):
  - `tools/service_step10_localize.py` (offline legs 1+2),
  - `tools/service_step10_realpath.sh` (real leg 3),
  - `tools/service_step10_analyze.py` (analysis).
- Results: `benchmarks/results/service-step-10/{offline,realpath,analysis}/`
  (per-rep JSON durable records, env.txt, manifests, tables).
- Report: `service-progress/step-10-format-degradation-report.md` (written
  after execution; Status PASS/PARTIAL/FAIL on the determination question).

## Reproduction (pre-registered)

```sh
cd /Users/pmains/Code/openclaw/kimi
# offline legs (temp sweep + env ladder at llama defaults):
python3 tools/service_step10_localize.py
# real-path leg (3 fresh sessions per probe):
bash tools/service_step10_realpath.sh
# analysis:
python3 tools/service_step10_analyze.py
```

## Stop Rule

This step ends with the written report and a STOP for review. No new
production patch may be made under Step 10 authorization. If the evidence
identifies a lever (e.g., per-model sampler configuration), it is recorded as
a recommendation for the owner's decision, not applied.
