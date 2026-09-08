# storage pass 2 (client timeout, gate still PASS) - 2026-09-07 19:24-19:34 MST

Storage leg rerun (fixed 40-char secret) hit the 600s client cap again
(rc=1, timed_out) - the openclaw agent turn did not return within 600s,
apparently due to single-slot contention (a ~15.4K-token prefill task at
33 tok/s for 466s occupied the slot during the window). The byte-level
storage gate PASSED regardless because it asserts on the PERSISTED
transcript (written at message-submit time): benign 40+ alnum-run path
stored verbatim (11B fix live), labeled AWS secret masked (not verbatim),
1 U+2026 = the redaction mask rendering. For a clean leg record (rc=0),
storage is rerun as pass 3 with a 1500s client budget.
