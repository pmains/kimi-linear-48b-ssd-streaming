# Service Progress Notes

This directory records the service-specific experiments around `caveman`
and the local Kimi Linear 48B path.

The notes here are not just summaries. They are meant to let the next
person reproduce the same request, the same server state, and the same
failure boundary without reconstructing the experiment from chat history.

---

## Required Detail

Each note should include enough context to answer these questions:

- What exactly was requested?
- Was this a cold request, a warm follow-up, or a restart test?
- Which agent and model path handled it?
- Which live server instance was involved?
- What changed between attempts?
- What did the logs and session transcript show?
- What concrete command reproduces the run?

At minimum, try to capture:

- request text or prompt shape;
- agent name;
- model reference;
- live server host, port, and process identity when relevant;
- prompt token counts;
- prompt-processing and first-token latency;
- whether the request reused a previous bootstrap or slot;
- the relevant log path or session transcript path;
- any fallback or abort behavior.

---

## Suggested Structure

Use this structure for new notes when practical.

# Stage X Report - [Short Name]

## Status

`PASS`, `PARTIAL`, `FAIL`, or `INVALID`

## Objective

What this step tried to establish.

## Environment

Record only the context needed to reproduce or interpret the run.

Examples:

- hardware and OS;
- repository commit;
- model revision or path;
- service host, port, and context/cache settings;
- live `llama-server` PID or restart state;
- relevant OpenClaw agent name and config path.

## Changes

What changed, if anything, and why.

## Tests

What was run to verify the step.

For each test, record:

- command or action;
- expected result;
- observed result;
- pass/fail status.

## Measurements

Record the quantitative evidence:

- prompt tokens;
- prompt-eval duration;
- first-token latency;
- total wall time;
- reuse or slot-selection indicators;
- memory or cache effects when relevant.

## Results

Summarize the facts established by the step.

## Problems

Record blockers, failures, or surprising behavior.

## Decisions

Record any important decisions or scope boundaries.

## Next Steps

Say what the next note or phase needs to verify.

## Reproduction

Provide exact commands or actions needed to repeat the run.

## Artifacts

List the important files, logs, transcripts, or benchmark outputs.

---

## Historical Notes

Existing notes in this directory may be shorter than the current template.
When a note is revisited or extended, prefer filling in the missing
sections rather than inventing a new format.
