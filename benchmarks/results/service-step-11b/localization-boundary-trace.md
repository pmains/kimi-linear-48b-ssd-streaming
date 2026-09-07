# Step 11B - Gate 2: boundary-trace localization (first clean-to-U+2026 function)

Date: 2026-09-05 ~23:10 MST. Status: LOCALIZATION PROVEN - stopped at gate for review. No fix.

## Result

First function whose output contains U+2026 (E2 80 A6) given byte-clean input:

    maskToken(token)  in /tmp/oc11b-pkg/dist/redact-CquADQ9-.js (~line 1108)

    return `${sliceUtf16Safe(token, 0, DEFAULT_REDACT_KEEP_START)}<U+2026>${sliceUtf16Safe(token, -4)}`;
    (DEFAULT_REDACT_KEEP_START = 6: keeps first 6 chars + U+2026 + last 4 chars)

Reached from the transcript-store persistence redaction of the USER message
content field (serializeForStorage), NOT from CLI steering. The redaction runs
DEFAULT_REDACT_PATTERNS over the content; an AWS-secret-style heuristic
(AWS_SECRET_ACCESS_KEY_VALUE_PATTERN, same file ~line 832: 40+ contiguous
[A-Za-z0-9/+=] with mixed case, a digit-or-slash, and a non-hex char)
false-positives on the 40-char absolute-path prefix and masks it.

## Boundary chain proven clean on entry (isolated replica :18791, instrumented byte-copy)

Content clean (126 B, 0 U+2026) at every boundary through:

  B1   CLI read decoded (probe-clean.md)      clean 126 B
  B2a  CLI dispatch body                       clean
  B3   gateway content-phase entry raw         clean
  B4   content-phase exit message              clean
  B4b  effective input                         clean
  B4.9 user-turn-prep entry                    clean
  B5.1 persistUserTurnTranscript message       clean
  B5.7 appendTranscriptMessageInTransaction    clean
  B5.2 before-message-write hook entry         clean
  B5.6 redactTranscriptMessage entry           clean (126 B, u2026=0)
  B6   redactTranscriptStructuredFieldValue    clean
  B7   redactSensitiveFieldValueWithConfig     clean
  B8   redactSensitiveFieldValueWithOptions    clean (post-registered clean)
  --> maskToken ENTRY clean (40 B, u2026=0) -> maskToken EXIT (13 B, u2026=1)  FIRST FLIP
  --> B8 post-redactText corrupted; B5.6 exit corrupted (99 B, u2026=1);
      B5 store append corrupted (matches isolated DB seq=1)

## Byte-accurate evidence (trace-7 / trace-8 raw logs)

maskToken INPUT (40 bytes, CLEAN; label decoded from the authoritative hex):
  hex   2f55736572732f706d61696e732f436f64652f6f70656e636c61772f6b696d692f53455256494345
  text  /Users/pmains/Code/openclaw/kimi/SERVICE

maskToken OUTPUT (13 bytes, one U+2026 inserted; shown with <U+2026> marker):
  hex   2f5573657273e280a656494345
  text  /Users<U+2026>VICE

The matched run was the 40-char path prefix (shown clean):
  /Users/pmains/Code/openclaw/kimi/SERVICE
(the "-" after SERVICE is not in [A-Za-z0-9/+=], so the run ends there). maskToken
keeps head-6 "/Users" + U+2026 + tail-4 "VICE"; the remaining "-ROADMAP.md" is
untouched, so the persisted content reads:

  ...path: /Users<U+2026>VICE-ROADMAP.md...

which is exactly the Step 11A stored signature (apparent "tail-15" = mask
tail-4 "VICE" plus the untouched "-ROADMAP.md" suffix).

## Trigger pattern (captured live by B9 probe inside redactMatch)

Pattern source: default AWS secret-access-key heuristic, redact-CquADQ9-.js ~line 832.
Live capture: MATCH = " " + the 40-char clean path run shown above.

Explains the Step 11A "not a pure length rule" puzzle: an 88-char path stored
clean because its character mix never formed a 40+ contiguous [A-Za-z0-9/+=] run
(hyphens/dots/underscores break the class), while the SERVICE-ROADMAP path prefix did.

## Call path (runtime-instrumented on the isolated replica)

CLI openclaw agent --message-file -> gateway agent turn -> user-turn recorder
-> persistUserTurnTranscript (user-turn-transcript-Bpg8tm8n.js)
-> persistSessionTranscriptTurn -> appendTranscriptMessage
-> appendTranscriptMessageInTransaction (session-accessor-D8yLi-lE.js ~1966)
   serializeForStorage -> redactTranscriptMessageForStorage
-> redactTranscriptMessage (session-accessor.sqlite-transcript-store-C2gLatTZ.js ~711)
-> redactTranscriptStructuredValue (~614)
-> redactTranscriptStructuredFieldValue("content", value, cfg) (~431)
-> redactSensitiveFieldValueWithConfig (redact-CquADQ9-.js ~1550)
-> redactSensitiveFieldValueWithOptions (~1524)
-> redactText (~1383) -> pattern replace
-> redactMatch (~1366) -> maskSecretValue (~1158)
-> maskToken (~1108)   <-- U+2026 first produced here
-> appendTranscriptEventInTransaction persists the corrupted event_json

## Multiple transformations

Same maskToken/redact path re-fires on later assistant/tool messages that already
contain the corrupted path (model copies it into args/results), compounding u2026
counts downstream. Earliest proven corruption boundary is the user-message
persistence redaction above; later fires are propagation, not origin.

## Scope note

Isolated replica only (byte-copy /tmp/oc11b-pkg, cloned config, :18791). No
production change. No fix implemented per the gate instruction. Trace artifacts:
benchmarks/results/service-step-11b/trace{1,3,7,8}-raw.log. Next authorized phase:
smallest fix preserving full user-message content/paths, then validation (clean-file
repro, G-style path cases, long-message regression), roadmap + report, stop for review.
