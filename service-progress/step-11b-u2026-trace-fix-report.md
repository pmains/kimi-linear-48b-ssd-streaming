# Step 11B Report: Fix the Pre-Model U+2026 Path-Content Corruption

## Status

PASS — FIX LIVE IN PRODUCTION after requalification 2 (2026-09-06 21:22-
21:23): all six production E2E legs PASSED with the FK-safe legs harness,
0 FK violations, patch retained (active sha 8baf4746...). Run history: run 1
(12:02) = test-oracle FAIL with correct runtime behavior; run 2 (15:01) =
formal requalification, inconclusive at harness level (legs-driver FK
cleanup defect); run 3 (21:22) = clean PASS. Corrected history: the earlier
"production applied" claim was FALSE; production stayed pristine until run 1.

## Objective

Fix the false-positive AWS-secret redaction in the installed OpenClaw
2026.9.1-beta.1 dist (`redact-CquADQ9-.js`,
`AWS_SECRET_ACCESS_KEY_VALUE_PATTERN`) that rewrote benign absolute path runs
of 40+ contiguous `[A-Za-z0-9/+=]` characters into a head-6 + U+2026 +
tail-4 corrupted form during transcript persistence (pre-model), corrupting
stored user-message content and later tool arguments. Preserve legitimate
AWS-secret redaction; do not broadly disable transcript redaction. Change
only the isolated `/tmp/oc11b-pkg` replica first.

## Changes

Single constant change at dist line 831 in the isolated replica
`/tmp/oc11b-pkg/dist/redact-CquADQ9-.js` (the
`AWS_SECRET_ACCESS_KEY_VALUE_PATTERN` String.raw template). The exactly-40
maximal-run window, uppercase/lowercase lookaheads, and the non-hex
lookahead are unchanged. Three guards tightened:

1. Symbol lookahead `{0,39}[0-9/+=]` changed to `{0,39}[0-9]`: a real digit
   is now required; slash, plus, and equals no longer satisfy the symbol
   requirement (they are path/base64 separator characters).
2. Runs beginning with `/` are rejected (absolute-path signature):
   added `(?!\/)`.
3. Runs containing 3+ slashes inside the 40-window are rejected: added a
   negative lookahead for three slash occurrences.

Pattern length OLD 184 B -> NEW 257 B. sha256 before
`5505b775545c7d39ee88916fb80d559a7cd4a916154680baf1fdfc53ca32e2f6`; after
`8baf47468478e930ad5b54cd5f8deda49164141d4069bb7c0418cb849ab179ce`.
Production dist sha unchanged (`5505b775...`), verified by cmp and sha256.
Pristine archive: `/tmp/oc11b-env/redact-CquADQ9-.js.before-831`.

No other pattern, no prefilter change, no labeled-AWS-pattern change, no
maskToken change, no model/sampler/context/tool configuration change.

## Results

1. Regex-level OLD-vs-NEW unit check, 111-entry corpus
   (`/tmp/oc11b-env/fix-regexcheck.mjs`, run from repo root): the probe run
   and synthetic path false positives are matched by OLD and rejected by
   NEW; every true AWS-secret shape is still matched by both; labeled-form
   behavior unchanged (old-match == new-match). Result:
   ALL-AS-EXPECTED, exit 0. Harness self-check: 0 U+2026 bytes, 0 asterisk
   runs.

2. E2E validation on the patched isolated gateway :18791 through the real
   CLI (`openclaw agent --message-file`), fresh session keys
   `agent:kimi:step11b-fix-N`, byte-proofs read from the isolated transcript
   DB (seq=1 message.content). Driver: `/tmp/oc11b-env/fix-legs.py`.
   Summary (fix-results.json, all_ok: True):

   - original clean-file discriminator (126 B): byte-identical, 0 U+2026
   - retained G-style real path cases (808 B): byte-identical, 0 U+2026
   - long benign paths matching the old heuristic (710 B): byte-identical,
     0 U+2026
   - long benign paths not matching the old heuristic (528 B):
     byte-identical, 0 U+2026
   - representative true AWS-secret-shaped values (748 B; 6 values, bare
     and labeled forms): raw values absent, masked head-6 + U+2026 + tail-4
     forms present (12 masked occurrences) - legitimate redaction preserved
   - long-message / transcript-storage regression (44,520 B):
     byte-identical, 0 U+2026

3. Accepted trade-off (documented in the design doc): standalone-value
   detection retained on real-digit keys (~99.9%), keys without a leading
   slash (~98.4%), keys with at most 2 slashes (~97.7%). Rare true keys
   starting with `/` or containing 3+ slashes are no longer caught by the
   bare-value heuristic standalone, but remain redacted in labeled contexts
   because the labeled AWS patterns are separate and unchanged.

## Problems

- Authoring discipline: several draft driver/record files initially
  contained literal artifact bytes of the same class under study (the
  corrupted path shorthand and redaction-placeholder runs). All were
  scrubbed; the final drivers, leg inputs, and evidence files are verified
  byte-clean (0 U+2026 bytes, 0 asterisk runs, no token material - auth
  tokens are read at runtime only, never typed or echoed).
- The E2E leg driver initially used literal placeholder text on its auth
  line and failed to parse; corrected to read the gateway token at runtime
  via the verified-clean helper (`/tmp/oc11b-env/readtok.py`).
- Pre-existing literal U+2026 bytes remain in older SERVICE-ROADMAP.md
  sections quoting historical corruption examples (lines 686, 1481, 1751,
  1782, 1815). The new 11B section (lines 1832-1885) is byte-clean; older
  quoted artifacts were left untouched as out of scope.

## Decisions

- Smallest fix = tighten only `AWS_SECRET_ACCESS_KEY_VALUE_PATTERN`; do not
  disable redaction, do not alter `maskToken`, do not touch labeled AWS
  patterns or the prefilter.
- Guards chosen to kill the path false-positive family (leading-slash
  signature, 3+ slash separators, real-digit requirement) while preserving
  base64-shaped secret detection for values with digits and at most two
  slash/plus/equal characters.
- Validation proved byte-for-byte storage identity through the real CLI
  path against the isolated transcript DB (not just unit-level regex),
  matching the corruption's actual entry point (transcript persistence
  redaction).

## Next Phase

Pete reviews the isolated fix (diff, evidence, this report). On
authorization, apply the same single-line change to the production dist
`/opt/homebrew/lib/node_modules/openclaw/dist/redact-CquADQ9-.js` and
restart the production gateway on :18789 (PID 59990), then re-run the
discriminator, G-style path, long-path, true-secret, and long-message
checks against production. Step 12 (decode throughput qualification)
remains queued behind this review.

## Reproduction

- Regex unit check:
  `cd /Users/pmains/Code/openclaw/kimi && node /tmp/oc11b-env/fix-regexcheck.mjs`
  (expect ALL-AS-EXPECTED, exit 0)
- Patch application driver (isolated replica only):
  `node /tmp/oc11b-env/patch831.mjs`
- E2E validation legs (isolated gateway :18791 must run the patched
  replica): `python3 /tmp/oc11b-env/fix-legs.py`
- Evidence: `benchmarks/results/service-step-11b/fix/` (`line831.diff`,
  `patch-record.txt`, `fix-results.json`, per-leg `.msg` inputs)
- Boundary trace and design: `benchmarks/results/service-step-11b/
  localization-boundary-trace.md` and `service-progress/
  step-11b-u2026-trace-fix-design.md`


---

## Addendum: Production Application (2026-09-06 12:02-12:13)

Ran by the retained detached orchestrator
(`benchmarks/results/service-step-11b/prod-apply/orchestrate-prod-restart.sh`;
log `orchestrator.log`; marker FAIL). Steps: archived pristine active file
(`prod-apply/pristine-redact-CquADQ9-.js.pre-apply-20260906-1202`, sha
5505b775...); replaced the ACTIVE dist file with the validated replica fix;
verified active pathname sha == 8baf4746... (MATCH); removed the stray
sidecar; detached `launchctl kickstart -k` restart (gateway 59990 -> 13064,
healthz 200); ran the six production E2E legs against the production kimi
transcript DB.

Results: legs 1-4 and 6 PASS (byte-identical stored transcripts, 0 U+2026).
Leg 5 FAIL on masked_present (raw_absent True, 12 U+2026, 12 secrets).
Authorized rollback branch fired: pristine restored, gateway restarted,
healthz 200. Marker FAIL.

Diagnosis: production leg-5 stored content is byte-identical to the isolated
PASS stored content (400 B, 12 U+2026, mask shapes [(6,4),(10,4)]). The prod
driver's regex window extraction (12 windows incl. label-prefixed phantoms)
fails its own masked_present check even against the isolated PASS bytes,
so leg-5 FAIL is a checker artifact, not a production regression. Evidence:
`benchmarks/results/service-step-11b/prod-apply/leg5-diagnosis.txt`,
`benchmarks/results/service-step-11b/fix-prod/fix-prod-results.json`.

Corrected history: prior recorded "production applied" claim was FALSE; the
active production file stayed pristine (5505b775...) until this run, and is
pristine again after rollback. Production healthy on :18789; llama and all
config untouched. STOPPED before Step 12.

## Addendum 2: Requalification Run (2026-09-06 15:00-15:40)

Owner order 13:29:51 (requalification only). Changed only the production
E2E leg-5 checker to the isolated driver's known-secret method
(`benchmarks/results/service-step-11b/prod-apply/prod-fix-legs-req.py`;
diff vs the run-1 driver is the leg-5 block only; redaction patch
untouched). Proof PASS (`prod-apply/requal-checker-proof.json`): corrected
checker passes the retained isolated PASS transcript (6 secrets,
raw-absent, masked head-6 + U+2026 + tail-4, 400 B) and fails an
intentionally unredacted control.

Executed: patch reapplied to the ACTIVE pathname (sha 8baf4746...
verified); detached kickstart (gateway PID 17346, healthz 200, post-
restart sha 8baf4746..., llama 200); six production legs ran with the
corrected checker 15:01:51-15:38:29.

RESULT: all six legs FAILED timeout-no-stored-row. Per-leg CLI stderr:
leg 1 "agent turn was not durably admitted"; legs 2-6 SqliteIntegrityError
(foreign_key_check failed for the production kimi DB:
session_transcript_active_events row 56168 references transcript_events).
Root cause: the legs driver's reset_session deletes probe
transcript_events/session_windows rows but leaves
session_transcript_active_events references; against the live production
DB with FK enforcement, the deletes orphaned rows and every new admission
failed, so no stored transcript existed to compare. This is a harness
defect, not a redaction regression; no behavioral comparison was possible.

Authorized rollback executed: pristine restored (5505b775...), gateway
restarted, healthz 200. Marker FAIL. Run distinction (per owner order):
run 1 = test-oracle FAIL with correct runtime behavior (stored bytes
byte-identical to isolated PASS); run 2 = formal requalification,
inconclusive at harness level. Production pristine and healthy on :18789;
llama and all config untouched. STOPPED before Step 12.

## Addendum 3: Requalification 2 PASS (2026-09-06 21:05-21:23)

Root cause of the prior DB corruption: reset_session deleted probe parent
rows (transcript_events, session_windows) with PRAGMA foreign_keys OFF
(python sqlite3 default), so the schema's ON DELETE CASCADE never ran;
dependent rows (session_transcript_active_events and the other tables in
the FK graph) were orphaned and the agent's foreign_key_check failed on the
next admission. Fixed in the harness only (fk_reset.py): explicit
child-first deletion of probe-owned dependents across the full inspected
FK graph, FK enforcement ON, and a post-reset PRAGMA foreign_key_check
assert. Driver prod-fix-legs-req2.py adds a per-leg FK gate. Leg-5 checker
and redaction patch unchanged. Independent proof on a DB copy:
prod-apply/fkproof.json (FK check 0 rows; non-probe digests unchanged).

Requalification run: patch applied to active pathname (8baf4746...);
detached kickstart (gateway PID 56719, healthz 200, post-restart sha
8baf4746...); six legs ran once 21:22:41-21:22:59 - ALL PASS
(fix-prod-requal2/fix-prod-results.json: benign legs byte-identical with
0 U+2026; leg 5 raw_absent + masked_present). Final PRAGMA
foreign_key_check: 0 rows. Marker PASS; patch retained and live. STOPPED
before Step 12.
---

## Addendum 4: REQUALIFICATION — FAIL — OpenClaw 2026.9.3 (2026-09-08)

### Status

**11B REQUALIFICATION — FAIL.** The 11B behavioral contract regressed in
OpenClaw 2026.9.3 (gateway pid 62534). The retained 6-leg corpus run
through the LIVE redaction implementation fails: legs 1-4 and 6 (benign
content that must be byte-identical, 0 U+2026) are corrupted with U+2026;
only leg 5 (true secrets) behaves correctly because the buggy pattern
masks everything. The exact original corruption reproduces on the
discriminator (leg 1): `/Users/pmains/Code/openclaw/kimi/SERVICE-
ROADMAP.md` stored as `/Users<U+2026>VICE-ROADMAP.md`.

### Root cause

The 11B fix existed ONLY as a local patch to the previous generated dist
file `redact-CquADQ9-.js` (sha 8baf4746..., 257 B fixed pattern). No
stable source in the installed package carries it. OpenClaw 2026.9.3
replaced that generated file with fresh output containing the ORIGINAL
184-byte pre-fix pattern (`redact-DMnNBHXb.mjs`, sha b80161806f796ac4...):
no real-digit guard `{0,39}[0-9]`, no leading-slash rejection, no
3-slash negative lookahead. The upgrade silently dropped the fix.

### Durable behavioral gate (replaces filename/SHA as the 11B gate)

- Active redaction implementation is DISCOVERED at runtime/build-
  inspection time via the transcript-store import chain (currently
  dist/redact-DMnNBHXb.mjs, entry `redactSecrets` for user-message
  transcript persistence), never assumed by hashed filename.
- Artifact SHA is recorded as provenance only; a changed filename/SHA
  triggers requalification, not automatic failure.
- Qualification = deterministic 6-leg corpus
  (`benchmarks/results/service-step-11b/fix/leg-*.msg`) run through the
  live boundary:
  * legs 1-4, 6: stored output byte-identical to input, 0 U+2026
  * leg 5: raw secrets absent, masked head-6+U+2026+tail-4 present
- Evidence: benchmarks/results/service-step-11b/reconcile-2026-09-08/

### Next

FAIL gate reached per the reconciliation order. Production still runs
2026.9.3 with the regression live (benign long paths are corrupted again
at transcript persistence). No patch applied — remediation requires new
authorization. STOPPED at the 11B gate; 14D/14E not begun.

## Addendum 5: 11B(i) FOLLOW-UP FIX — PASS (2026-09-08 12:24-13:07)

Owner order 2026-09-08 12:24 authorized the durable re-fix of the 11B
regression (labeled 11B(i) — an 11B follow-up, not a new step; no "11C"
exists). The identical qualified three-guard transform from the requal2
patch (real-digit lookahead `[0-9]` replacing `[0-9/+=]`, leading-slash
rejection `(?!\/)`, 3-slash negative lookahead) was applied to the live
184-byte pre-fix template in `redact-DMnNBHXb.mjs`, producing the
257-byte fixed form verified byte-equal to the retained requal2 pattern
(sha256 of template c3bb074cba6f543d).

### Replica module qualification (PASS)

Byte-clone of the installed package at /tmp/oc11b-pkg-20260908; patch
applied -> module sha b54b13f1d79cb98a; retained 6-leg corpus run
through the patched `redactSecrets` (module export l): legs 1-4/6
byte-identical with 0 U+2026; leg 5 raw secrets absent + masked
head-6+U+2026+tail-4 present. Control run on the unpatched live module
reproduced the recorded FAIL signature (1/3/8/1/60 U+2026 counts).

### Production requalification (PASS, run 2)

Detached orchestrator (`orchestrate-11bi.sh`): archived pristine ->
applied the one-constant patch -> 60s turn-flush sleep -> kickstarted
the launchd gateway -> six production legs once with the FK-safe driver
(`prod-fix-legs-11bi.py`) -> PASS marker retained, no rollback.

Results (13:06:44-13:07:02 MST):

| leg | result |
|---|---|
| 1-discriminator (126 B) | byte-identical, 0 U+2026 | PASS |
| 2-gstyle (808 B) | byte-identical, 0 U+2026 | PASS |
| 3-longpath-match (710 B) | byte-identical, 0 U+2026 | PASS |
| 4-longpath-nomatch (528 B) | byte-identical, 0 U+2026 | PASS |
| 5-true-secret (748 B) | raw absent, masked head-6+U+2026+tail-4, 12 U+2026 | PASS |
| 6-long-message (44,520 B) | byte-identical, 0 U+2026 | PASS |

Final gates: 0 production FK violations; gateway :18789 healthz 200;
llama :18080 200 (pid 7021 constant); live module sha
b54b13f1d79cb98a...; 257-byte template with all three guards present.

### Run-1 harness note (not a redaction failure)

The first production run FAILED at the harness level only: the retained
legs driver read the stored user message at hardcoded seq=1, but the
2026.9.3 transcript schema inserts session/provider/thinking events
before the user message (user content now lands at seq=4). The stored
user row was already byte-identical under the patched module when the
driver crashed. Driver `stored_content` fixed to scan ascending seq and
return the first role=user string-content event; verified against the
run-1 leftover row before re-running. Rollback path verified: pristine
sha b80161806f796ac4 restored, gateway healthy, FK 0.

### Durability warning (unchanged)

The fix still exists only as a patch to a generated dist artifact — no
stable source in the installed package. A future OpenClaw upgrade will
regenerate `dist/redact-*.mjs` and drop the patch again. The PERMANENT
GATE (behavioral, not filename/SHA) is the protection: after every
upgrade, re-discover the active redaction module via the
transcript-store import chain and re-run the 6-leg corpus; on failure,
re-apply with `benchmarks/results/service-step-11b/fix-prod-11bi-20260908/
orchestrate-11bi.sh` (re-archives pristine, re-patches, restarts,
requalifies, rolls back).

Evidence: benchmarks/results/service-step-11b/fix-prod-11bi-20260908/
(replica-module-suite.json, patch-11bi.diff, pristine archive,
prod-fix-legs-11bi.py, fix-prod-results.json, orchestrator.log +
.marker).
