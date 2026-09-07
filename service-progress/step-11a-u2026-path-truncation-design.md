# Step 11A Design — Localize the U+2026 Path-Truncation Failure

Date: 2026-09-05 · Author: Alkaline (agent) · Authorization: Pete *** MST
Repo: `github.com/pmains/kimi-linear-48b-ssd-streaming` <!-- project: github.com/pmains/kimi-linear-48b-ssd-streaming -->

## Objective

Trace exactly where a valid absolute path (e.g.
`/Users…VICE-ROADMAP.md`) becomes the
U+2026-truncated forms seen in evidence (e.g. `/Users…VICE-ROADMAP.md`,
`/Users…DMAP.md`) — from raw model output through OpenClaw tool-call
parsing/serialization to the final tool invocation. Reproduce with focused
controls using the same files and tool schemas, including successful
full-path calls for comparison.

**Constraints: no change to context size, sampler, prompts, or production
configuration.** No patch. Deliverable: classification (post-model OpenClaw
corruption vs model-emitted) + smallest fix / safest mitigation proposal +
evidence + SERVICE-ROADMAP.md update + report; STOP for review.

## Leading hypothesis to test first (authoring-layer artifact)

During Step 11 authoring the same U+2026 artifact hit *this agent's own*
tool-call paths repeatedly (e.g. three failed `write` calls to
`/Users…vice-progress/...` in this session, ENOENT), and a raw dump of
`G1.md` visibly contains `/Users…VICE-ROADMAP.md`. Meanwhile a grep-based
"prompts contain no U+2026" check was defective (`$'\u2026'` is invalid in
zsh; the `||` fallback printed NONE on the grep's syntax error). So a
credible hypothesis is that the G-session prompts were corrupted **at
authoring time** and the kimi model *faithfully copied* the corrupted path
into read args (transcript shows `{"path": "/Users…VICE-ROADMAP.md"}` — an
exact match to the prompt text). The P1-r5 anchor (clean prompt "Respond
only PLATANOS!", no such path in context) is the cleaner case of possible
model-emitted truncation and must be traced separately.

## Evidence to collect

1. Byte-level U+2026 inventory (python `"\u2026" in s`, plus hexdump spot
   checks) across: all step-11 prompts (R/C/G), step-10/10D/11 reports and
   design docs, step-11 smoke prompts, LOOP/LOOP-STRICT/NORM prompts, and
   the G-session transcript read args.
2. Exact-match check: do G-session read tool args equal the corresponding
   prompt substring byte-for-byte (faithful copy) or differ (new truncation
   at call time)?
3. P1-r5 anchor: was there ANY U+2026 string in the model's context
   (system prompt, project context, tool schema, prior tool results) that
   the model could copy? The prompt itself was clean.
4. Raw-model controls: direct llama-server `/v1/chat/completions` calls
   with (a) the full path requested in the user message, (b) same path in a
   tool-call context, several temperatures/repeats — check raw JSON for
   U+2026 vs full path. Establishes whether the *model* emits it.
5. OpenClaw layer: inspect read-tool error formatting and transcript
   serialization in dist for any U+2026/slicing; check whether the read tool
   error text returned to the model contains U+2026 (which the model could
   then copy back — a plausible self-sustaining-loop mechanism).
6. Successful full-path controls: same files/schema with clean prompts →
   confirm full-path reads succeed (already exist: R/C sessions, smoke t1/t2,
   NORM-r3 — reuse).

## Classification targets

- (A) Authoring-layer artifact (prompts corrupted before the run) → G
  failures are probe artifacts; P1-r5 must still be explained.
- (B) Model emits U+2026 directly → characterize when/why (path length?
  specific path segments? tokenizer?) + safest mitigation.
- (C) OpenClaw truncates after model output (tool schema display, read
  error formatting, transcript serialization) → smallest OpenClaw fix.
- (D) Copy-back loop: model reads a U+2026 path from its own context
  (tool error text) and re-emits it → explains byte-identical repeats.

## Procedure / STOP

1. This design. 2. Byte inventory + transcript exact-match (steps 1–2).
3. P1-r5 context scan (step 3). 4. Raw llama-server controls (step 4).
5. OpenClaw dist inspection (step 5). 6. Classify A/B/C/D, write
   SERVICE-ROADMAP.md §11A + report `service-progress/step-11a-u2026-path-truncation.md`
   + evidence under `benchmarks/results/service-step-11a/`. **STOP for
   review. No patch, no config/prompt/sampler change.**
