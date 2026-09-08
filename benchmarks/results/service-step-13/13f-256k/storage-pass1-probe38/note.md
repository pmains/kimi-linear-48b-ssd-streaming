# storage pass 1 (probe bug) - 2026-09-07 ~19:03-19:04 MST

Storage leg ran rc=0 but the gate flagged secret_verbatim=true. Root cause:
probe carried a 38-char "secret"; the live AWS_SECRET_ACCESS_KEY_VALUE_PATTERN
requires EXACTLY 40 chars of [A-Za-z0-9/+=] (verified against the module), so
the redactor correctly did nothing - probe invalid, NOT a redaction
regression. Benign 40+ alnum-run path WAS stored verbatim with 0 U+2026,
confirming the 11B fix is live. Driver patched: probe secret is now a
node-verified 40-char pattern-matching value. Rerun as storage
(RUN_SUFFIX=r2).
