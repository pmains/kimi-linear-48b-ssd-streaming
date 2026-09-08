# Step 11B Reconciliation — OpenClaw 2026.9.3 (2026-09-08)

Verdict: **11B REQUALIFICATION — FAIL** (behavioral regression confirmed).

## Scope

Narrow reconciliation only (owner order 2026-09-08 12:17 MST). No
production patch, no unrelated OpenClaw change, no llama.cpp change.
Behavior is the gate; module filename/SHA are provenance only.

## 1. Active redaction implementation (discovered, not assumed)

- Package: openclaw 2026.9.3 (gateway pid 62534, healthz 200).
- Active module (loaded by the transcript-store persistence path,
  verified via import chain from
  session-accessor.sqlite-transcript-store-*.mjs):
  `/opt/homebrew/lib/node_modules/openclaw/dist/redact-DMnNBHXb.mjs`
- Artifact SHA-256 prefix: b80161806f796ac4...
- Entry point used at the 11B corruption boundary (user-message
  transcript persistence): `redactSecrets` (module export `l`) ->
  redactText -> AWS_SECRET_ACCESS_KEY_VALUE_REDACT_PATTERN -> maskToken.
- Stable source: none in the installed package (dist only + .d.ts;
  patches/ dir holds unrelated third-party patches). The 11B fix
  existed ONLY as a local patch to the previous generated dist file
  `redact-CquADQ9-.js` (sha 8baf4746...). OpenClaw 2026.9.3 replaced
  that file with freshly generated output containing the ORIGINAL
  184-byte pre-fix pattern (no real-digit guard `[0-9]`, no leading-
  slash rejection, no 3-slash negative lookahead; 257-byte fixed form
  absent from the module).

## 2. Deterministic behavioral suite vs live 2026.9.3

Retained 11B corpus (benchmarks/results/service-step-11b/fix/leg-*.msg),
run through the LIVE redactSecrets implementation. Expected per the
qualified requal2 contract: legs 1-4/6 byte-identical, 0 U+2026;
leg 5 raw secrets absent + masked present.

| leg | expected | actual | result |
|---|---|---|---|
| 1-discriminator (126 B) | byte-identical, 0 U+2026 | 126 -> 97 B, 1 U+2026 | FAIL |
| 2-gstyle (808 B) | byte-identical, 0 U+2026 | 808 -> 721 B, 3 U+2026 | FAIL |
| 3-longpath-match (710 B) | byte-identical, 0 U+2026 | 710 -> 478 B, 8 U+2026 | FAIL |
| 4-longpath-nomatch (528 B) | byte-identical, 0 U+2026 | 528 -> 499 B, 1 U+2026 | FAIL |
| 5-true-secret (748 B) | secrets absent, masked present | 748 -> 400 B, 12 U+2026 (masked) | PASS (mask only) |
| 6-long-message (44,520 B) | byte-identical, 0 U+2026 | 44,520 -> 42,780 B, 60 U+2026 | FAIL |

Exact original-bug reproduction (leg 1):
- input : ".../Users/pmains/Code/openclaw/kimi/SERVICE-ROADMAP.md..."
- output: ".../Users<U+2026>VICE-ROADMAP.md..."  (head-6 + U+2026 +
  tail-4 of the 40-char path run) — byte-identical to the Step 11A/11B
  corruption signature the fix eliminated.

## 3. Conclusions

- The 11B fix is NOT present in OpenClaw 2026.9.3. The generated dist
  reverted to the pre-fix 184-byte AWS-secret heuristic, so benign
  absolute-path runs of 40+ contiguous [A-Za-z0-9/+=] are again
  corrupted with U+2026 during user-message transcript persistence.
- Because the fix lived only in a patched generated artifact (never in
  stable source in the installed package), the 2026.9.3 upgrade silently
  dropped it. This is the fragility the behavioral-gate rework addresses.
- No production secret values were printed into evidence; the true-secret
  corpus values used are the retained synthetic 11B test values.

## 4. Durable qualification record (for future upgrades)

- OpenClaw version: 2026.9.3
- Active redaction module: redact-DMnNBHXb.mjs (dist; resolved at
  runtime via the transcript-store import chain, not by hashed name)
- Artifact SHA-256 prefix: b80161806f796ac4 (provenance only)
- Behavioral suite result: FAIL (6-leg corpus, 5/6 legs corrupted)
- Gate statement: **SHA/path are provenance; behavior is the gate.**
  A changed filename/SHA triggers requalification, not automatic
  failure. Future qualification = run this 6-leg corpus through the
  live redactSecrets boundary and require legs 1-4/6 byte-identical +
  0 U+2026 and leg 5 masked.
