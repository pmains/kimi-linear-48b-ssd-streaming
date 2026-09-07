# Step 9B Report — Agent-Trajectory Quality Localization (execution results)

## Status

**COMPLETE — 2026-09-03.** The approved Step 9B ablation matrix was executed in
full against the live llama-server endpoint (`127.0.0.1:18080`, MXFP4
Kimi-Linear-48B-A3B) with the frozen model, sampler, user prompts, expected
outputs, and scoring semantics. 111 rows retained (n = 3 per condition):
90 Class-I ladder rows (C0, C1, C1a-D, C1a-T, C2, C2a-D, C2a-T, C3, C4 ×
P1/P1b/P3/P2) + 21 Class-II replay rows (R1/R2 × P4/P5/P6/P7).
Stopped for review. Step 10 not begun. Nothing patched.

## Objective

Attribute the retained Step 9 `FAIL — RESPONSE QUALITY (agent trajectory)`
families to agent-environment dimensions (H1–H6) by replaying one added
environment layer at a time at the llama-server boundary, with the agent
path's actual outbound request shape (no sampler fields → llama defaults
temp 0.8/top_p 0.95/min_p 0.05/top_k 40; model id `kimi-linear-48b`; tools
in the OpenAI wrapped form).

## Changes

- `service-progress/step-09b-agent-trajectory-localization.md` — amended to
  the approved design (owner amendments: n=3; ≤1 observational capture only
  if needed — not needed; no runner-side in-place variant; C1a/C2a split into
  single-instruction-class ablations C1a-D/C1a-T, C2a-D/C2a-T).
- `tools/service_step09b_ablate.py` — retained execution driver (payload
  construction, llama-server calls, per-rep durable JSON, frozen-match
  offline mirror). Frozen suite files untouched.
- `tools/service_step09b_classify.py` — retained re-classifier applying the
  frozen scorer's instruction/format/result split to every raw rep.
- `benchmarks/results/service-step-09b-verify/phase0/` — recovered verbatim
  system prompts, trajectory payloads (system/tools/messages per call),
  per-probe scored-reply → model-call mapping, `tools-full-29.json`
  (see Problems).
- `benchmarks/results/service-step-09b-verify/ladder/…`, `replay/…`,
  `classified.csv`, `ladder-summary.csv`, `summary-tables.txt`,
  `ladder-run.log`, `replay-run.log`.

## Results

### Class-I ladder (n = 3 per cell; PASS = frozen `instruction_followed`)

Condition legend: C0 bare user only · C1 +verbatim system (no tools) ·
C1a-D system minus reply-directive instructions · C1a-T system minus
tool-policy instructions · C2 +tools declared · C2a-D/C2a-T tools + each
stripped system · C3 (P1) one executed tool round · C4 (P1-opt1-1) verbatim
final-call replay.

| probe | C0 | C1 | C1a-D | C1a-T | C2 | C2a-D | C2a-T | C3 | C4 |
|---|---|---|---|---|---|---|---|---|---|
| P1 (`PLATANOS!`) | 1 exact, 2 terse | **1 directive-only, 2 directive-prefixed** | 3 terse (`PLATANOS`) | 2 directive-prefixed, 1 prose | 1 prose, 1 directive-prefixed, 1 terse | 1 prose, 2 terse | 3 terse | 1 exact, 1 terse, 1 clarifying | **3 terse `PLATANOS`** |
| P1b (1 word ≠ model) | 3 exact | 3 exact | 3 exact | 2 exact, 1 prose | 2 exact, 1 terse | 1 terse, 2 prose | **2 prose, 1 clarifying** | — | — |
| P2 (`{"ok": true}`) | 3 exact | **2 fenced, 1 exact** | 3 exact | 1 exact, 2 fenced | 2 exact, 1 prose | 2 exact, 1 terse | 1 terse, 1 directive-only, 1 exact | — | — |
| P3 (bare `40`) | 3 exact | 1 exact, 2 prose | 2 exact, 1 prose | 2 exact, 1 prose | **3 prose** | 2 terse, 1 prose | 3 prose | — | — |

Families (pre-registered): exact · terse-wrong · prose-wrapped ·
prose-content-ok (content right, format wrong) · clarifying ·
empty/directive-only (lone `[[reply_to_X]]` tag) · directive-prefixed.

### Class-II replays (n = 3)

| cond | P4 (18080) | P5 (count 5) | P6 (file roundtrip) | P7 (COMPLETE + date) |
|---|---|---|---|---|
| R1 full replay | 3 prose-wrapped | 3 prose-content-ok | 2 prose-content-ok, 1 empty | 2 prose-wrapped, 1 terse |
| R2 minus system | 2 prose-wrapped, **1 exact `18080`** | 3 prose-content-ok | — | 2 terse, 1 prose |

### Earliest environment addition associated with each retained failure family

| retained r2 family | probe | reproduced offline at | earliest sufficient layer |
|---|---|---|---|
| empty_result (`[[reply_to_current]]`-only) | P1 | **C1 rep3: lone `[[reply_to_current]]`** | system prompt alone; eliminated by C1a-D (0/3 directive-affected) → **reply-directive instructions** |
| `!`-drop (`PLATANOS` w/o `!`) | P1 | **C0 2/3 terse `PLATANOS`** | bare prompt at llama-default sampler → **sampler (temp 0.8)**; reply-directive lines add tag prefixes on top |
| clarifying question | P1b | C2a-T 1/3 clarifying; not at C0–C2 | tools declared + tool-policy stripped; rare tail at agent-path shape (C2 2/3 exact) — r2 catch is within that tail |
| fenced JSON | P2 | **C1 2/3 fenced** | system prompt alone; PASS restored by C1a-D (3/3) and mostly by tools (C2 2/3) → **reply-directive lines bias fences; tools counteract** |
| prose reasoning | P3 | C1 2/3 prose; **C2/C2a-T 3/3 prose** | system text alone, dominant with tools → **system + tool-catalog context** |
| prose content-ok (format FAIL) | P4/P5/P7 | R1: P4 3/3 prose, P5 3/3 content-ok, P7 0 PASS | verbatim final-call input reproduces the family offline |

### Classification against H1–H6

- **H1 / H3-D (reply-directive instruction lines) — PRIMARY for the
  directive-tag family and a contributor to fenced JSON.** C1 alone
  reproduced r2-P1's empty mechanism (lone `[[reply_to_current]]`) and the
  `!`-prefixing; C1a-D (removing only the `## Assistant Output Directives`
  section — the lines 9A option1-restore made unconditional) eliminated
  every directive-tag emission (0/3 across P1/P2) and restored P2 exact
  3/3. This is the same class of lines whose restoration flipped P1 from
  clarifying (pre-9A) to directive-imperfect (post-9A) in the retained 9A
  record.
- **Sampler (agent path runs at llama defaults temp 0.8, not the temp-0
  direct control) — CONTRIBUTING to P1 `!`-loss.** C0 bare at the pinned
  profile produced `PLATANOS!` only 1/3; the temp-0 direct control produced
  it 1/1. Sampler does not explain the prose/clarifying/directive families
  (C0 P1b/P2/P3 pass 3/3 at the same profile).
- **H2 (tool declarations) — CONTRIBUTING, probe-dependent.** P3 prose
  becomes dominant only when tools are declared (C2/C2a-T 3/3 prose vs
  C1 1/3 exact); P2 fenced tendency is *reduced* by tools (C2 2/3 exact),
  matching r2 P2 PASS under the real 29-tool catalog.
- **H5 (accumulated context) — NOT indicated for Class-I families.** The
  r2 Class-I scored replies were first-call outputs; C1/C2 single-call
  payloads already reproduce or straddle the families. R1/R2 replays of the
  tool-using probes reproduce their prose families from the retained
  final-call input alone.
- **Runner orchestration (auto-continue / empty-reply rejection) — NOT
  implicated.** C4 (verbatim opt1-1 final-call replay) reproduced the
  retained terse `PLATANOS` 3/3 offline with no runner in the loop; C1
  reproduced the empty `[[reply_to_current]]` mechanism directly.
- **H6 (inherent under the necessary environment) — residual only.** With
  the full necessary context (C2: system+tools at llama-default sampler),
  strict-format compliance is already imperfect for P1/P3 in n=3; the
  families are boundary-model behavior under that context, not a defect of
  one environment layer.

### Primary vs contributing (summary)

1. **Reply-directive instruction lines** (9A-restored, in the system
   prompt) → directive-tag emission family (incl. r2-P1 empty_result) and
   fenced-JSON tendency. Primary, text-level, isolated by C1a-D.
2. **Sampler profile** (llama defaults temp 0.8, which the agent path
   actually used) → `!`-drop and general terse variance on P1.
3. **Tool catalog declaration** → prose-reasoning dominance on P3 and
   partial counteraction of fences on P2.
4. Prose content-ok format failures on tool-using probes (P4/P5/P7) are
   reproduced offline from the model input alone — they are model behavior
   under the required tool-use context, not runner artifacts.

### Is a principled OpenClaw/config fix indicated?

A targeted, principled fix **is indicated for the directive-tag family
specifically**: the reply-directive instruction lines (made unconditional by
9A option1-restore) demonstrably cause lone-`[[reply_to_current]]` and
prefix-tag outputs on exact-reply probes when the delivery path cannot
render them (offline equivalent of the empty_result). Making those lines
conditional again on an actual messaging delivery surface (or instructing
"never emit the tag when no delivery channel will consume it") is the one
change with direct, measured support (C1a-D removed the whole family in 3/3
across P1/P2). No single fix is supported for the remaining families: P3
prose and P4/P5/P7 prose-content-ok track tool-catalog presence and the
model's assistant-style behavior under it, and P1 `!`-loss tracks the
sampler. Treating those as response-quality limitations of the frozen
agent path is consistent with the evidence.

## Problems

- **Trajectory-store truncation (Phase 0).** The OpenClaw trajectory store
  redacts oversized JSON at write time: every retained 29-tool wire array
  has entries 9–28 stored as literal `"[Truncated]"` strings (the `intent`
  anchor was even nested-truncated inside its parameters), and two
  `conversationRef` patterns were stored as
  `"[Malformed diagnostic JSON redacted]"`, which llama-server rejects.
  Resolution (approved Phase-0 fallback, provenance recorded): 11/29 tool
  definitions are byte-exact from retained payloads (apply_patch…intent,
  read, write); the remaining 18 were reconstructed from the frozen dist
  tool factories that built the agent requests; malformed patterns replaced
  with the dist's real anchored regex. Ladder requests therefore carry the
  same catalog the agent path declared, with documented provenance.
- **Offline replay cannot execute tools.** Class-II R1/R2 replay the
  retained final-call input (tool results already in context) — the
  correct layer for the format-family question; no live tool execution is
  claimed.
- **P1b clarifying family is a rare tail.** It reproduced only at C2a-T
  (1/3) and did not appear at the agent-path-equivalent C2 in n=3; the r2
  single catch is consistent with a low-probability outcome under the C2
  distribution, so attribution for that one family is weaker than for the
  directive-tag families.
- One observational capture was attempted (smoke probe, `OPENCLAW_DEBUG=1`)
  before it was determined unnecessary; it timed out at the gateway and was
  not relied on for any payload.

## Decisions

- Pinned sampler = agent-path identical (no sampler fields → llama
  defaults), per approved design; retained temp-0 direct controls kept as a
  separate reference, and C0 confirms the sampler contributes to P1
  `!`-loss.
- C1a/C2a executed as two one-variable instruction-class ablations
  (reply-directives vs tool-policy), never combined.
- Tools catalog assembled as 11 byte-exact + 18 dist-reconstructed entries;
  requests use the OpenAI wrapped form llama-server requires.
- R1/R2 built from the retained messagesSnapshot of the model call that
  produced each scored reply (drop trailing assistant output, drop internal
  compaction markers), system text from that run's first-call payload.

## Next Phase

Step 9 determination stays `FAIL — RESPONSE QUALITY (agent trajectory)` per
frozen-rubric evidence. Before any Step 10 work (355.6 s wall-time
investigation remains parked), the owner should decide whether the one
measured-fix candidate (conditional reply-directive lines) is worth a
controlled re-test, and whether the P3/P4/P5/P7 prose families are accepted
as model behavior under the tool-using agent context. No Step 10 work was
started.

## Reproduction

- Environment: live llama-server `127.0.0.1:18080` (MXFP4
  `moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf`), OpenClaw dist
  with the frozen 9A patch set.
- Class-I ladder (n=3): `python3 tools/service_step09b_ablate.py --conditions C0,C1,C1a-D,C1a-T,C2,C2a-D,C2a-T,C3,C4 --probes P1,P1b,P3,P2 --reps 3`
- Class-II replays (n=3): `python3 tools/service_step09b_ablate.py --replays`
- Reclassification: `python3 tools/service_step09b_classify.py`
- Raw evidence: `benchmarks/results/service-step-09b-verify/ladder/*/*/rep*.json`,
  `replay/*/*/rep*.json`, `classified.csv`, `ladder-summary.csv`,
  `summary-tables.txt`, `ladder-run.log`, `replay-run.log`.
- Phase-0 artifacts: `benchmarks/results/service-step-09b-verify/phase0/`
  (verbatim system prompts, per-run compiled payloads, `tools-full-29.json`,
  `payload-manifest.json`, `phase0-summary.json`).
