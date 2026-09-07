# Step 11B Design - Trace and Fix the Pre-Model U+2026 Message Corruption

Date: 2026-09-05 | Author: Alkaline (agent) | Authorization: Pete mandate 2026-09-05 19:05:44 MST; boundary-trace authorization 21:50:08 MST)
Repo: `github.com/pmains/kimi-linear-48b-ssd-streaming`
Companion: 11A report `service-progress/step-11a-u2026-path-truncation-report.md`;
forensics `benchmarks/results/service-step-11a/11a-forensics-summary.md`.

## Objective

Instrument the real `openclaw agent --message-file` path (message ingestion →
steering/normalization → transcript persistence) and identify the FIRST
function where a byte-clean absolute path becomes the U+2026 middle-truncated
form (stored path head-6 "/Users" + U+2026 + tail; e.g. rendered "/Users<U+2026>VICE-ROADMAP.md"). Use the retained 11A clean-file probe
(`benchmarks/results/service-step-11a/probe-clean.md`) as the discriminator
and capture byte-accurate before/after values at each boundary. Then
implement the SMALLEST fix that preserves full user-message content and
paths, and validate with:
  (1) the original clean-file reproduction,
  (2) retained Step 11 G-style path cases,
  (3) normal long user messages (regression).
Update SERVICE-ROADMAP.md, write the Step 11B report, stop for review.

## Constraints

- Do NOT change model, sampler, context, or tool configuration.
- llama-server (launchd, 64K single slot :18080) untouched; used only as the
  model endpoint for headless probe legs, sequentially.
- Code fix target = the code that ACTUALLY runs: installed openclaw
  `2026.9.1-beta.1` at `/opt/homebrew/lib/node_modules/openclaw/dist`
  (gateway PID 59990, :18789). Repo `openclaw-src` (2026.8.1-era fork) is
  NOT the running code; readable architecture map only.
- Production gateway is patched + restarted ONLY after the fix is proven on
  an isolated instrumented copy (detached-supervisor pattern, 10B/10D
  precedent). Exact diff retained in repo.

## 11A-established facts (byte-level)

- Input file clean (od-verified); raw model emits clean path 8/8 on clean
  input. Corruption is OpenClaw-side and PRE-MODEL: stored transcript user
  message seq=1 (written at session start, before first model output)
  already contains U+2026 bytes in the live gateway DB.
- `finalPromptText` clean while stored content corrupted → gateway keeps a
  clean copy in the doc/prompt-text layer; the corrupted stored content is
  what feeds prompt assembly (model copies it byte-identically).
- Signature: sentence byte-identical, only the long path token shortened to
  head-6 `<U+2026>` tail-7..15. NOT a pure length rule (88-char path stored clean;
  51-char corrupted, same gateway PID). Exact dist function NOT isolated.
- 11A static scan candidates (coerceDisplayValue, compactRawCommand,
  compactProgressLineDetail, maskLifecycleIdentifier, redactSessionKey,
  shortId, truncateTitle, etc.) — none match the token-level signature.

## Method

1. Design (this file).
2. Locate in installed dist: CLI `--message-file` read site; the
   CLI→gateway transport payload shape; the gateway agent-turn receive
   handler; session-create/message-append; steer/normalize; transcript
   store (event_json) write sites.
3. Build an ISOLATED code-identical replica: byte-copy of the installed
   package (dist + node_modules) to a scratch dir; cloned config/home/state
   with rewritten paths and a fresh gateway port (same llama baseUrl);
   confirm the corruption reproduces there (probe-clean.md → replica DB
   seq=1 corrupted). If isolation does NOT reproduce, instrument the
   production path via the detached-supervisor pattern and record why.
4. Instrumented trace on the replica at each boundary (file read, CLI
   payload build, wire, gateway receive, message-append/normalize,
   transcript store write, prompt assembly) with byte-accurate hex+len
   dumps; run the discriminator probe; identify the FIRST boundary where
   content diverges and the exact function.
5. Implement the smallest fix in the replica; re-run probe → seq=1
   byte-clean; run the G-style path case and the long-message regression.
6. Apply the identical patch to the installed dist (backup + retained
   diff), restart the gateway via the detached supervisor, live-validate
   (1)(2)(3) on the real path.
7. SERVICE-ROADMAP.md §11B + report + evidence. STOP for review.

## Evidence/driver retention

- `benchmarks/results/service-step-11b/` — env, isolated configs, probe
  runs, boundary dumps, DB copies, diffs.
- `tools/service_step11b_*.py|sh` — retained drivers.
- Report `service-progress/step-11b-u2026-trace-fix-report.md`.

---

## Gate 1 (2026-09-05 ~21:37 MST): isolated-replica reproduction — CONFIRMED (YES)

Gate question (Pete): does the byte-clean absolute path reproduce as a literal
U+2026-corrupted path in the isolated replica?

Answer: **YES.** Byte evidence in
`benchmarks/results/service-step-11b/gate1-isolated-reproduction.md`.

Run: code-identical isolated gateway (byte-copy of installed 2026.9.1-beta.1
at /tmp/oc11b-pkg, cloned config on :18791, fresh state), probe = retained
126-byte byte-clean `probe-clean.md` (0 U+2026). Correctly routed via
OPENCLAW_GATEWAY_URL=http://127.0.0.1:18791 + runtime token from the cloned
config (readtok.py helper; token never echoed/typed/written). Session
`agent:kimi:step11b-iso-4` (sid 4b0db6e4-12d3-4f17-acbd-6805733ca718) exists
ONLY in the isolated store; production store at 21:37 contains only the two
earlier mis-routed runs (iso-1, iso-3).

Stored seq=1 (isolated DB): type=message, event len=379, u2026chars=1,
e280a6bytes=1. Content:
'Please read the file at exactly this path: /Users<U+2026>VICE-ROADMAP.md...'
Hex around corruption: 3a202f5573657273 e280a6 56494543452d524f41444d41502e6d64
(clean 126-byte file -> head-6 "/Users" + U+2026 + tail "VICE-ROADMAP.md":
identical 11A signature). Model-side churn then copied the corrupted path into
read/exec args (seq 4+), File-not-found, exactly the production family.

Conclusion: corruption lives in the installed OpenClaw code path
(ingestion -> transcript store, pre-model). Reproduces with identical code +
cloned config + fresh state => NOT production-gateway state/config artifacts.

Routing side note (disclosed): earlier probes iso-1/iso-3 were served by the
production gateway (env-only overrides ignored; sessions created in the
production kimi agent store). Both are stray test sessions of the 11A probe
family; no production config/code change. The valid isolated run is iso-4.

Per the gate: STOPPED here. No instrumentation, no tracing, no fix yet.
Next authorized phase (Step 11B mandate): boundary trace to first corrupting
function, then smallest fix, then validation (clean-file repro, G-style path
cases, long-message regression), roadmap + report, stop for review.

---

## Gate 2 (2026-09-05 ~23:15 MST): boundary trace - FIRST CORRUPTING FUNCTION PROVEN

Result: the first function whose output contains U+2026 (E2 80 A6) given byte-clean
input is **maskToken(token) in /tmp/oc11b-pkg/dist/redact-CquADQ9-.js (~line 1108)**,
reached from the transcript-store persistence redaction of the USER message content
field (serializeForStorage), not from CLI steering. Full byte evidence:
`benchmarks/results/service-step-11b/localization-boundary-trace.md` (ASCII-clean,
hex-authoritative) and trace raw logs trace{1,3,7,8}-raw.log.

Boundary chain proven clean on entry (content 126 B, 0 U+2026) through: CLI read
(B1) -> dispatch (B2a) -> content-phase (B3/B4/B4b) -> user-turn-prep (B4.9) ->
persistUserTurnTranscript (B5.1) -> appendTranscriptMessageInTransaction (B5.7) ->
before-write hook (B5.2) -> redactTranscriptMessage entry (B5.6) ->
redactTranscriptStructuredFieldValue (B6) -> redactSensitiveFieldValueWithConfig
(B7) -> redactSensitiveFieldValueWithOptions post-registered (B8).

First flip (traces 7/8):
  maskToken ENTRY: clean 40 B (hex 2f55736572732f706d61696e732f436f64652f6f70656e636c61772f6b696d692f53455256494345; text decodes to "/Users/pmains/Code/openclaw/kimi/SERVICE", u2026=0)
  maskToken EXIT : 13 B (hex 2f5573657273e280a656494345; "/Users" + U+2026 + "VICE", u2026=1)
  -> remaining "-ROADMAP.md" untouched -> stored "/Users<U+2026>VICE-ROADMAP.md"

Call path: CLI -> gateway turn -> recorder persist -> persistSessionTranscriptTurn
-> appendTranscriptMessage -> appendTranscriptMessageInTransaction
(session-accessor-D8yLi-lE.js ~1966) serializeForStorage ->
redactTranscriptMessageForStorage -> redactTranscriptMessage
(session-accessor.sqlite-transcript-store-C2gLatTZ.js ~711) ->
redactTranscriptStructuredValue (~614) ->
redactTranscriptStructuredFieldValue("content") (~431) ->
redactSensitiveFieldValueWithConfig (redact-CquADQ9-.js ~1550) ->
redactSensitiveFieldValueWithOptions (~1524) -> redactText (~1383) ->
redactMatch (~1366) -> maskSecretValue (~1158) -> **maskToken (~1108)** ->
appendTranscriptEventInTransaction persists corrupted event_json.

Trigger: default redaction pattern AWS_SECRET_ACCESS_KEY_VALUE_PATTERN heuristic
(redact-CquADQ9-.js ~line 832): 40+ contiguous [A-Za-z0-9/+=] chars with mixed
case, a digit-or-slash, and a non-hex char. The plain absolute-path prefix
"/Users/pmains/Code/openclaw/kimi/SERVICE" (40 chars, all letters and slashes; "/" is in the class) satisfies it.
Explains the 11A "not a pure length rule" puzzle: paths whose first 40 chars
contain "-", ".", "_" (breaking the class run) do not match; the probe path's
first 40 chars do not, so it matches and is masked at persistence.

Per the 21:50 gate instruction: STOPPED here, no fix implemented. Trace artifacts
retained: benchmarks/results/service-step-11b/trace{1,3,7,8}-raw.log. Next
authorized phase: smallest fix preserving full message content/paths, then
validation (clean-file repro, G-style path cases, long-message regression),
roadmap + report, stop for review.
