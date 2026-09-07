# Step 9B — Agent-Trajectory Quality Localization (scope and experiment design)

## Status

**COMPLETE — 2026-09-03.** Design approved and executed in full (111 rows, n=3 per
condition). Execution report: `service-progress/step-09b-localization-report.md`
(raw evidence under `benchmarks/results/service-step-09b-verify/`). Stopped for
review per directive; Step 10 not begun.
Frozen Step 9 result stands: `FAIL — RESPONSE QUALITY (agent trajectory)`
(9A patch set frozen; repaired path clean of leakage/stall/fallback). This
document defines the smallest controlled experiment that attributes the
remaining strict-format failures to agent-environment dimensions before the
result is accepted as an inherent Kimi limitation. Step 10 not begun.

### Owner amendments (approved 2026-09-03, applied)

1. n = 3 repetitions per condition approved.
2. Phase 0 observational capture approved **only if necessary**: at most one
   fresh, non-scoring `OPENCLAW_DEBUG=1` capture if a required payload field
   cannot be recovered from retained evidence. Outcome: **not needed** — every
   required field (system text, tools array, message histories, outbound
   sampler behavior) was recovered read-only from the retained trajectory
   store (`~/.openclaw/agents/kimi/agent/openclaw-agent.sqlite`,
   `trajectory_runtime_events` → `context.compiled`/`prompt.submitted`/
   `model.completed`) and the frozen dist/config. See
   `benchmarks/results/service-step-09b-verify/phase0/`.
3. **No runner-side in-place variant.** If C4 does not reproduce the retained
   agent-path behavior and implicates runner orchestration, stop and report
   for review.
4. **C1a tightened to true one-variable ablations.** Reply-directive
   instructions and tool-policy instructions are now removed in separate
   conditions (C1a-D, C1a-T), never together. C2a likewise splits (C2a-D,
   C2a-T).

## 1. Objective

Answer, with measured evidence:

> Why do instructions that Kimi can satisfy directly (bare llama-server
> control → exact `PLATANOS!`, exact `{"ok": true}`) degrade when executed
> through the full OpenClaw agent environment (prose-wrapped, clarifying,
> empty, or `!`-dropped replies under the frozen suite)?

Attribute the degradation to one or more agent-environment dimensions while
keeping the model, sampler, frozen user prompts, and frozen expected outputs
unchanged. Use the retained frozen probes as diagnostic cases — P1/P1b/P3
(primary), P4/P5/P7 (secondary), P2 as a passing control.

## 2. Question decomposition and hypotheses

The difference between the two anchors is entirely in the model *input
context* plus the runner-side *loop policy* that surrounds it:

| Anchor | Messages | Tools | Loop |
|---|---|---|---|
| Direct control (retained) | `[user]` only | none | none (single completion) |
| Full agent path (r2) | system + user + tool rounds + commentary (≈12.8k ctx) | full catalog | auto-execute tool_use, continue until stop, reject empty visible replies |

Candidate causes (owner list), each mapped to a testable dimension:

- **H1 — OpenClaw system-prompt instructions**: the system message content
  (persona, directives, tool guidance, injected context) alone degrades
  strict-format compliance.
- **H2 — Tool availability / tool descriptions**: declaring the tool catalog
  (schemas/descriptions) changes answer behavior even when no tool runs.
- **H3 — Agent policy / mandatory-encouraged tool behavior**: the
  reply-directive lines and tool-policy sentences inside the system text
  (the lines 9A restored/retained) drive the behavior change.
- **H4 — Multi-call / tool-result trajectory effects**: one or more executed
  tool rounds (and their results, e.g. the failed `update_goal` round)
  derail the final generation.
- **H5 — Accumulated agent context**: the full retained context mass
  (system + tools + history + commentary) beyond any single isolated layer.
- **H6 — Inherent Kimi limitation under the necessary OpenClaw environment**:
  residual — no single dimension suffices; even the minimal environment tips
  the model out of exact compliance.

Runner-side loop policy (auto-execute tool calls, continuation turns,
empty-reply rejection, post-generation maintenance) is client-side and not
directly reproducible by endpoint calls; the design partitions it by
difference (see decision rule 8).

## 3. Evidence base (retained, drives the design)

All under `benchmarks/results/service-step-09/` (r2 rerun), `…-baseline-2026-09-02/`,
`…-09a-verify/`, plus reports `service-progress/step-09{,-a}.md`.

- Direct controls (attribution): P1 → `PLATANOS!` exact; P2 → `{"ok": true}`
  exact (temp 0, no system, no tools). Model alone satisfies the frozen
  prompts that fail through the agent path.
- r2 frozen-suite shapes (scorer verbatim): P1 INVALID `empty_result` (~16 s,
  no visible assistant reply; srvlog shows a 6-token generation);
  P1b clarifying question; P3 prose reasoning ending in `40`; P4 prose +
  fenced excerpt + `**18080**`; P5 prose list + `**Answer: 5**`; P7 sentence
  ending inline `COMPLETE` (not its own final line); P2 exact PASS; P6 PASS.
- P1 has produced **three different shapes** across retained runs under
  different system-prompt states: clarifying question (gated, directive
  lines omitted), `PLATANOS` without `!` (opt1-1, directive lines restored),
  empty visible reply (r2, same frozen config as opt1-1) → run-to-run
  variance exists even at fixed config; repetitions are required.
- P1-opt1-1 localization: two model calls; first = commentary + unnecessary
  `update_goal` tool call → tool result `{"status":"error","tool":
  "update_goal","error":"goal not found"}` → second call = terse `PLATANOS`
  (5 tokens = 4 content + EOS; no `!` token possible). Token accounting and
  transcript prove the `!` never existed at the generation boundary.
- P3 non-monotonicity: PASS (bare `40`) on 2026-09-02 baseline; FAIL (prose +
  `40`) in r2 under the restored directive lines → the system-text state is
  already observed to flip outcomes for the *same* frozen probe.
- Session transcripts (SQLite) are proven readable (P1-opt1-1 localization);
  they contain the verbatim message sequence (system + user + tool rounds)
  per provider call.

## 4. Frozen invariants and non-goals

Unchanged across every condition and the whole step:

- Model and endpoint: the same live llama-server (`127.0.0.1:18080/v1`,
  same GGUF model id as the retained direct controls and agent path).
- Sampler: a single pinned profile (recovered in Phase 0 from the agent
  path's actual outbound requests; applied identically to every condition —
  see §6). Sampling is never varied as an experimental dimension.
- Frozen user prompts: byte-identical `prompts/*.md` (SHA-256 pinned).
- Expected outputs and scoring semantics: `expected/*.json` + the documented
  match semantics (exact / single_word_not / json_eq / number_eq /
  count_eq / ends_complete_with_date). The frozen suite scorer file is not
  modified; a separate offline scorer mirrors the same semantics.
- Live agent, dist, tool policy, directive instructions, config: untouched.
- No cloud fallback path exists in the direct-endpoint harness.

Not investigated (explicit exclusions): TTFT, decode performance, wall
times (Step 10 territory — the 355.6 s and ~16 s observations are retained,
not analyzed), and any tuning of prompts/expected outputs/scorers in
response to failures. No iterative tuning loop against individual probes.

## 5. Method

Offline **payload ablation at the llama-server boundary**: construct
OpenAI-format chat-completion requests that differ from the bare direct
control by exactly one agent-environment dimension at a time, all built
from verbatim payloads recovered from retained transcripts. Each condition
is a small, deterministic, single-completion (or scripted two-completion)
endpoint call — no agent session, no dist/config change, no prompt patch.
The frozen user-prompt text is identical in every condition; only
surrounding context layers are added/removed.

Failure family classification (per condition, per probe): the final text is
classified as `exact` (passes frozen semantics), `prose-wrapped`,
`clarifying`, `empty/directive-only`, or `tool_use-only`. Classification is
pre-registered; n = 3 repetitions per condition to bound the observed
run-to-run variance.

Probe classes:

- **Class I (self-contained, no tools needed to answer)**: P1, P1b, P3 —
  the full ladder applies; the direct floor exists and (for P1/P2) is
  retained. These probes are the primary discriminators.
- **Class II (content requires tools)**: P4, P5, P7 — no tool-less floor
  exists (model cannot know the port / file list / UTC date without a
  tool); the ladder applies only at the replay level. P2 (+P6 optionally)
  serve as passing controls: they must stay PASS through the ladder,
  bounding interpretation.

## 6. Phase 0 — read-only recovery (execution gate, after approval)

1. From retained session transcripts (SQLite), recover the **verbatim
   outbound request payloads** (full messages incl. system, tools array,
   sampling parameters, stop reasons) for the failing runs: P1-opt1-1
   (session `71780bcf-b176-4a0a-ba1a-38b62466cf62`), P1-r2, P1b-r2,
   P3-r2, P4-r2, P5-r2, P7-r2 (+P2-r2, P6-r2 as controls). Save each as a
   hashed JSON artifact.
2. If a field (e.g. the `tools` array or sampling params) is not present in
   the retained transcript, recover it read-only from the frozen dist /
   runner config that built the request, and record provenance. If still
   unrecoverable, run **one** observational capture probe (fresh session,
   same frozen config, non-scoring, `OPENCLAW_DEBUG=1` for request logging)
   per missing profile; the capture run itself is retained as evidence.
3. Publish the **pinned sampler profile** (temperature/top-p/etc. actually
   sent by the agent path). Note: retained direct controls used temp 0;
   if the agent path differs, C0 is re-run at the pinned profile so the
   ladder floor and the agent path are sampler-comparable.
4. Gate note: if a probe's full payload is unrecoverable, that probe runs
   the C0–C3 ladder only; C4/R1/R2 are marked `N/A` for it.

## 7. Ablation matrix

Every condition below specifies: **(1) what is changed**, **(2) what remains
frozen**, **(3) which retained probe(s) it uses**, **(4) what result would
distinguish competing hypotheses**, **(5) what evidence will be retained**.

### C0 — bare floor

1. Changed: nothing added — `messages = [user]` with the frozen prompt
   (extend the retained P1/P2 direct controls to P1b/P3; run at the pinned
   sampler profile).
2. Frozen: model, sampler, prompt bytes, expected semantics.
3. Probes: P1, P1b, P3 (P2 already retained).
4. Distinguishes: establishes the model-alone floor at the pinned sampler.
   If P1b or P3 fails here (e.g. clarifying even bare), that probe's agent
   failure is partly model-inherent (H6 evidence); expected: all pass,
   matching retained controls.
5. Evidence: raw completion JSON, extracted text, score, per-condition
   subdir under `benchmarks/results/service-step-09b-verify/`.

### C1 — + system prompt (H1)

1. Changed: adds the verbatim OpenClaw system message recovered in Phase 0
   (the full system text the agent path sent), nothing else.
2. Frozen: user prompt bytes, model, sampler, expected semantics; no tools.
3. Probes: P1, P1b, P3.
4. Distinguishes: if C1 fails where C0 passed (prose/clarifying/`!`-drop/
   empty), **OpenClaw system-prompt instructions are sufficient to cause
   the degradation (H1)**. If C1 still passes exactly, the system text
   alone is exonerated.
5. Evidence: request payload (system text SHA-256), raw completion, score,
   failure-family label.

### C1a-D — system minus reply-directive instructions only (H3-text, class D)

1. Changed: C1's system text with the **reply-directive instruction lines
   removed only** — the `## Assistant Output Directives` section (bullets:
   MEDIA attachment, directive-starts-line, `[[audio_as_voice]]`,
   `[[reply_to_current]]` native-reply line, directives-stripped-before-
   render). These are the lines 9A option1-restore made unconditional. The
   removal diff is retained. No tool-policy or other lines change.
2. Frozen: everything else (user prompt, tools absent, model, sampler).
3. Probes: P1, P1b, P3 (directly tests the 9A-observed regression and the
   Phase-0 mechanism candidate: r2-p1's scored output was a lone
   `[[reply_to_current]]` directive tag).
4. Distinguishes: if C1 fails and C1a-D passes, the **reply-directive
   instructions specifically are the operative component (H3-text-D)**. If
   both fail with the same family, the cause is broader system content
   (H1-general) or another instruction class.
5. Evidence: diff file, both payloads, completions, scores, family labels.

### C1a-T — system minus tool-policy instructions only (H3-text, class T)

1. Changed: C1's system text with the **tool-policy instruction sentences
   removed only** — the `Tools policy-filtered. Names case-sensitive; call
   exact.` line, the `## Tool Call Style` section, and the `## OpenClaw
   Control` section. The removal diff is retained. No reply-directive or
   other lines change.
2. Frozen: everything else (user prompt, tools absent, model, sampler).
3. Probes: P1, P1b, P3.
4. Distinguishes: isolates the tool-policy instruction class from the
   reply-directive class (H3-text-T) as a separate one-variable step.
5. Evidence: diff file, both payloads, completions, scores, family labels.

### C2 — + tools declared, no execution (H2)

1. Changed: adds the verbatim `tools` array recovered in Phase 0
   (`tool_choice` auto) to the C1 request. No tool runs; a single
   completion. If the model emits `tool_use`, it is recorded as
   `tool_use-only` (a noncompliance shape) and the condition ends there.
2. Frozen: user prompt, system text, model, sampler, expected semantics.
3. Probes: P1, P1b, P3.
4. Distinguishes: if C2 fails where C1 passed (tool detour, prose,
   clarifying), **tool availability/descriptions degrade compliance (H2)**.
   If C2 still passes, tool presence alone is exonerated.
5. Evidence: request payload incl. tools array (SHA-256), completion
   (incl. any `tool_calls`), score, family label.

### C2a-D — C1a-D system + tools (class-D text × H2 interaction)

1. Changed: verbatim tools array added to the C1a-D (reply-directives-
   stripped) system condition.
2. Frozen: user prompt, stripped system text, model, sampler.
3. Probes: P1, P1b, P3.
4. Distinguishes: C2 vs C2a-D separates "tool presence" from
   "reply-directive sentences" — if C2a-D passes while C2 fails, the
   reply-directive text (not tool presence) is causal even when tools
   exist (H3-D confirmed, H2 refuted); if both fail, tool presence is
   causal with or without the directive lines (H2 confirmed).
5. Evidence: payload, completion, score, family label.

### C2a-T — C1a-T system + tools (class-T text × H2 interaction)

1. Changed: verbatim tools array added to the C1a-T (tool-policy-
   stripped) system condition.
2. Frozen: user prompt, stripped system text, model, sampler.
3. Probes: P1, P1b, P3.
4. Distinguishes: same separation for the tool-policy instruction class
   (H3-T confirmed vs H2 confirmed).
5. Evidence: payload, completion, score, family label.

### C3 — + one tool round (H4, trajectory)

1. Changed: adds one executed tool round to the C2 request: the model's
   first completion is answered with a canned tool result — verbatim
   retained round for P1 (`update_goal` → `goal not found`, mirroring
   opt1-1) and a minimal canned round for P1b/P3 — then a second
   completion produces the final answer.
2. Frozen: user prompt, system text, tools array, model, sampler.
3. Probes: P1, P1b, P3 (per-probe note: if the retained run had no tool
   round, C3 collapses to C2 and is marked so).
4. Distinguishes: if C3 fails where C2 passed, a **single multi-call/
   tool-result round is sufficient to derail the final answer (H4)** —
   directly tests the opt1-1 mechanism (failed tool round → terse
   `PLATANOS` without `!`).
5. Evidence: both completions (tool round + final), canned result bytes,
   score, family label.

### C4 — full retained replay (H5 + runner-policy partition)

1. Changed: replaces the constructed request with the **verbatim
   final-call input** of the retained failing run (system + tools +
   full history exactly as sent to the model), replayed to llama-server
   at the pinned sampler.
2. Frozen: user prompt text (inside the replayed history), model, sampler,
   expected semantics.
3. Probes: P1 (two instances: opt1-1 payload — the only Class-I instance
   with a real tool round — and r2 payload), P1b, P3. Phase-0 note:
   P1-r2/P1b/P3 scored replies were produced by their **first** model call
   (msgCount 0, no tool round), so for those the C4 payload is
   wire-identical to C2; C2 doubles as the replay check and C4's
   discriminating instance is P1-opt1-1 (commentary + failed `update_goal`
   round + final call). Class-II probes P4/P5/P7 run their own R1/R2 below.
4. Distinguishes: (a) if C4 reproduces the retained failure family while
   C2/C3 pass, **accumulated context beyond isolated layers (H5)** is
   implicated; (b) if C4 does **not** reproduce at the pinned sampler, the
   runner-side loop policy (auto-continue, empty-reply rejection) is
   implicated instead — the model input was not sufficient to explain the
   agent-path outcome (see decision rule 8).
5. Evidence: replayed payload (SHA-256 vs recovered original), completion,
   score, family label, reproduction verdict.

### Class-II replay conditions

### R1 — full replay (P4/P5/P7)

1. Changed: verbatim final-call input replay of the retained run, same as
   C4 but for tool-dependent probes.
2. Frozen: model, sampler, expected semantics.
3. Probes: P4, P5, P7 (+P6 control: expect PASS reproduction).
4. Distinguishes: reproduction of the prose-wrapped-but-content-correct
   family confirms the degradation lives in the model input, not in tool
   execution quality (all three had correct content — port 18080, count 5,
   correct UTC date — with wrong format).
5. Evidence: payload, completion, family label, content-correctness check.

### R2 — replay minus system message

1. Changed: R1's payload with the system message removed (tools + tool
   results + user retained); single textual deletion, diff retained.
2. Frozen: everything else (model, sampler, tools, results, history).
3. Probes: P4, P5, P7.
4. Distinguishes: if prose wrapping disappears without the system text,
   **system-prompt instructions drive the final-answer verbosity (H1)**
   even when tool results are present; if prose persists, the tool-result
   context or the format instruction itself is the cause.
5. Evidence: payload, completion, family label.

### Phase 0 outcomes (recovered, drive execution)

1. Verbatim payloads per run: `phase0/payloads/<run>.compiled.json`
   (prompt, systemPrompt, messages, tools) + SHA-256 manifest
   (`phase0/payload-manifest.json`). System text for r2 runs is 29,795
   chars (sha `9c3172a4…`); opt1-1 first-call variant 29,801 (sha
   `8378af8c…`).
2. Scored FAIL replies were first-call outputs for P1-r2 (directive-tag
   only `[[reply_to_current]]`), P1b (clarifying question), P3 (prose
   ending in 40), P2-control (exact PASS), P7/P5 (content correct, format
   wrong); P4/P6 used tool rounds before their final replies.
3. Pinned sampler profile: the agent path sends **no sampler overrides**
   (openclaw.json llama-server model entry has none; dist only forwards
   temperature/top_p when defined). Effective sampling = llama-server
   defaults (verified via `/props`): temperature 0.8, top_p 0.95,
   min_p 0.05, top_k 40. Ladder requests replicate the agent path (no
   sampler fields) so every condition is sampler-comparable with the
   agent path; the retained temp-0 direct controls are a separate,
   documented reference (deterministic floor), not the ladder floor.
4. Mechanism candidate from r2-p1: assistantTexts `["[[reply_to_current]]"]`
   — the whole "empty" reply was one reply-directive tag, stripped
   pre-render → `empty_result`. Directly tests C1a-D.
5. No observational capture required; none run.

## 8. Decision rules (outcome pattern → hypothesis)

1. C0 fails for a Class-I probe → model-alone deficiency (H6 for that
   probe); not expected from retained controls.
2. C1 fails, C0 passes → **H1** (system-prompt instructions sufficient).
3. C1a passes, C1 fails → **H3-text** (policy/directive sentences are the
   operative component inside the system text).
4. C2 fails, C1 passes → **H2** (tool availability/descriptions).
5. C2 fails, C2a passes → policy text is causal in the presence of tools
   (H3 confirmed); converse → H2 confirmed regardless of policy lines.
6. C3 fails, C2 passes → **H4** (one multi-call/tool-result round derails
   the final answer).
7. C4 fails while C2/C3 pass, or C4's family differs from C3's → **H5**
   (accumulated agent context).
8. C4 does not reproduce the retained agent-path family at the pinned
   sampler → the derailment is partly **runner-side loop policy**
   (auto-execute/continue, empty-reply rejection); confirming that layer
   would need an in-place agent-path variant, which requires separate
   authorization (not part of this matrix).
9. Multiple layers each flip outcomes → the first ladder step that flips
   the family is the primary cause; later flips are secondary contributors.
   Report contribution order, not just the first cause.
10. P2 (+P6) must remain PASS across the ladder; if a condition breaks the
    controls, the layer change is interpreted as a global formatting
    override, and that condition's Class-I failures are read cautiously.

## 9. Evidence retention

- New tree `benchmarks/results/service-step-09b-verify/`:
  `manifest.json` (probe prompt SHA-256s, pinned sampler profile, condition
  definitions, payload SHA-256s), `phase0/` (recovered payloads, provenance,
  capture-run logs), per-condition subdirs `C0…C4/C1a/C2a/R1/R2/<probe>/`
  (request payload, raw completion JSON, extracted text, score, family
  label), plus `ladder-summary.csv` and this design doc as the scope record.
- System-text diffs for C1a/R2 retained next to the payloads.
- This design document is the Step 9B scope record; an execution report
  (`step-09b-report.md`) is written only after the matrix runs.

## 10. Execution proposal (post-approval; nothing run yet)

1. Phase 0 recovery (§6) — read-only plus at most one observational
   capture probe per missing profile.
2. A small retained driver (`tools/service_step09b_ablate.py`, written at
   execution time) issues the condition payloads serially to the live
   llama-server endpoint, n = 3 per condition, saves raw evidence, applies
   the frozen-match semantics offline, and writes `ladder-summary.csv`.
3. Class-I ladder first (C0–C4 + C1a/C2a on P1/P1b/P3, P2 control), then
   Class-II replays (R1/R2 on P4/P5/P7, P6 control) — stop early only if
   the Class-I ladder already isolates a single dimension and Pete approves
   skipping the Class-II tail.
4. Report: per-probe ladder table, family labels, decision-rule verdict,
   hypothesis attribution, retained evidence pointers. Stop for review.
5. No Step 10 work; no suite rerun; no prompt/tool/config changes.

## 11. Open questions for owner review

1. Accept n = 3 repetitions per condition (variance-bound), or prefer a
   different bound?
2. Approve the possible single observational capture probe in Phase 0
   (fresh session, `OPENCLAW_DEBUG=1`, non-scoring) if a payload field is
   not recoverable from retained transcripts?
3. If decision rule 8 fires (runner-side loop policy implicated), is an
   in-place agent-path variant (e.g. tool-execution disabled or
   empty-reply rejection toggled) in scope for a later round, or is that
   beyond Step 9B?

## 12. Stop point

Design proposed; **no ablation run, no patch, no suite rerun, no Step 10
work**. Stopped for review per directive.
