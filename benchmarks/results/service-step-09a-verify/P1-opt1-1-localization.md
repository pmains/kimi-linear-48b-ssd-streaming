# P1-opt1-1 — Localization of the missing `!` (2026-09-03)

## Question (Pete, 16:48 MST)

Where does the trailing `!` disappear? Establish whether:
1. Kimi generation inside the OpenClaw embedded-agent path contains `PLATANOS!` and OpenClaw transforms it to `PLATANOS`; or
2. Kimi generation itself is already `PLATANOS` under the full OpenClaw agent prompt/context.

## Verdict

**Hypothesis 2.** The `!` never existed in the agent path. The earliest capture
of the model output — the session-transcript assistant message, written from
the provider response before any directive stripping — is already `"PLATANOS"`.
Every downstream OpenClaw record is byte-identical `PLATANOS`. No OpenClaw
code path removes a trailing `!`, and no `!` was present to remove.

## Evidence chain (all from run 1441f2fa, session 71780bcf)

### 1. Session transcript (SQLite `transcript_events`, rawest capture)
- seq1 user: `Respond only PLATANOS!` (23:39:21.998Z)
- seq4 assistant (23:43:11.606Z): content = commentary text
  `I'll respond only with the word PLATANOS as requested.` + toolCall
  `update_goal {status:complete, note:"Respond only with PLATANOS"}`;
  stopReason `toolUse`; usage.output=42; responseId chatcmpl-3bgUA3...
- seq5 toolResult: `{"status":"error","tool":"update_goal","error":"goal not found"}` (isError=true)
- seq6 assistant (23:43:15.144Z): content = `[{"type":"text","text":"PLATANOS"}]`
  — **no `!`, no tag**; stopReason `stop`; usage.input=43, usage.output=5,
  cacheRead=12801; responseId chatcmpl-R1R60v...

### 2. Trajectory `model.completed` event
- `assistantTexts: ["PLATANOS"]`, `stopReason: "stop"`; messagesSnapshot shows
  the full sequence: user → assistant(commentary+toolCall) → toolResult(error) → assistant("PLATANOS").

### 3. Envelope / delivered
- `finalAssistantRawText`, `finalAssistantVisibleText`, `payloads[0].text`,
  `.reply.txt` are all exactly `PLATANOS` (no `!` at any stage).

### 4. Token accounting (llama-server /tokenize + /detokenize, srvlog)
- `PLATANOS`  → 4 tokens [3842,1192,1584,4110]
- `PLATANOS!` → 5 tokens [3842,1192,1584,4110, 0]; token 0 = `!` (detokenize confirms)
- Direct control (bare user turn): text `PLATANOS!`, completion_tokens=6;
  srvlog task 27240 eval = 6 tokens ⇒ n_decoded includes EOS (5 content + EOS).
- Agent final call: transcript output=5, srvlog task 26894 eval = 5 tokens
  ⇒ 4 content tokens + EOS = exactly `PLATANOS`. No room for a `!` token.

### 5. Dist code check
- `stripInlineDirectiveTagsForDisplay` / `collapseLeadingReplySeparator` /
  `parseInlineDirectives` remove tags and leading `: ` separators only;
  nothing strips trailing punctuation. Also, the raw transcript content shows
  no directive tag was emitted in this run at all.

### 6. Direct llama-server control (re-run for retention, 17:0x)
- `benchmarks/results/service-step-09a-verify/P1-opt1-1-direct-control.json`
  text `PLATANOS!`, finish `stop`, completion_tokens 6, wall 3.1 s.
  ⇒ The model can and does emit `PLATANOS!` with the bare frozen P1 prompt.

## First divergence point

The final provider generation in run 1441f2fa (responseId chatcmpl-R1R60v…,
23:43:15Z) already returned `PLATANOS` — the divergence from the direct control
is at generation, upstream of every OpenClaw text transform.

Contextual difference vs direct control: under the full agent context the model
first produced commentary + an `update_goal` tool call (which failed with
"goal not found" in the fresh probe session), and the follow-up generation in
that tool-error context produced the terse `PLATANOS` without the `!`.
The direct control is a single bare user turn with no agent system prompt,
no tools, no tool-error round.

## Out of scope (per Pete 16:48 MST)
- 355.6 s wall time NOT investigated (Step 10). For the record only:
  main run durationMs=233342; a subsequent continuation run (a097c9b2,
  transcript seq7-8) aborted at 23:45:16Z with "request timed out"
  (~120 s, the B2-capped maintenance-run family).
- No patches, no frozen-suite rerun, no sampler/Kimi/prompt changes.
