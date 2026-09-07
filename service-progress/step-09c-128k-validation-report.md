# Step 9C Report — Narrow 128K Context Validation

## Status

COMPLETE — stopped for review per authorization. No patches. Step 10 untouched.
The only environment change was a temporary llama-server instance at
`--ctx-size 131072` (128K); the launchd-managed 64K server was restored and
verified afterward.

## Objective

Narrow validation of the Step 9B findings under a doubled context window:

1. Confirm whether condition C1a-D (full agent environment minus the
   reply-directive instruction lines) still eliminates the
   directive-tag/empty-response failure family at 128K context.
2. Confirm whether the P1 trailing-`!` drop and the prose-wrapped /
   prose-content-ok failures persist when the context window is not a
   constraint (64K baseline vs 128K test).
3. Record everything under a new Step 9C artifact/report; leave Step 10
   untouched; no code/config changes beyond the temporary 128K test config.

Context framing note: Pete's instruction referenced "32K context pressure";
the retained 9B baseline actually ran at 64K (the launchd-managed server's
`--ctx-size 65536`). This 9C run therefore compares 64K vs 128K. Probe
prompts are tiny (P1 payloads ~14 prompt tokens), so neither window was ever
close to utilization pressure; the comparison tests window-size independence
of the failure families.

## Probe Selection

Per instruction, P1 plus the best available representatives of the
P3/P4/P5/P7 prose/tool-catalog family:

- P1 (mandated) — the `PLATANOS!` exact-output probe whose `!` drops on the
  agent path.
- P3 — the only member of the P3/P4/P5/P7 prose family that is answerable in
  the tool-less C0/C1a-D ladder conditions (P4/P5/P7 are tool-content probes,
  replay-only in 9B; they cannot be exercised without tool declarations).
- P1b — additional Class-I response-quality probe (prose/clarifying tail in
  9B), has a 64K baseline.
- P2 — the passing control (`{"ok": true}` exact JSON).

Conditions: C0 (bare floor at the pinned sampler profile) and C1a-D (full
agent payload minus reply-directive lines). 3 reps each -> 8 cells, 24 reps.

## Changes

- `benchmarks/results/service-step-09c-verify/` (new artifact tree):
  - `ladder/{C0,C1a-D}/{P1,P1b,P2,P3}/rep{1..3}.json` — 24 durable per-rep
    records (cond/probe/rep, text, family, pass, finish, tool_calls, usage,
    wall_s, payload_sha).
  - `ladder-summary.csv` — 24 rows, rebuilt from the per-rep JSONs.
  - `classified.csv` — 24 rows reclassified with the exact frozen Step 9
    scorer semantics (imported `frozen()`/`family()` from
    `tools/service_step09b_classify.py`; no scorer changes).
  - `ladder-manifest.json`, `run-128k.log`, `run-128k-p1b.log`,
    `stream/{cache_layers,mem,retr,stats}.csv` (server stats redirected to
    the 9C stream dir while the temp 128K server ran).
- `service-progress/step-09c-128k-validation-report.md` — this report.
- No code, config, prompt, tool-policy, dist, or roadmap changes.

Execution mechanics: the 9B driver was imported unchanged
(`tools/service_step09b_ablate.py`) with only its output root redirected to
the 9C tree via a thin launcher; per-rep JSONs are therefore identical in
schema to 9B's. The temp server was started with the same command line as
the launchd job except `--ctx-size 131072`, same model file, same env, same
port 18080. After the run the launchd job was re-bootstrapped from
`~/Library/LaunchAgents/com.openclaw.kimi-llama-server.plist` and verified
(`--ctx-size 65536`, `KIMI_CTX=65536`, `/health` ok).

## Results

PASS counts use the frozen `instruction_followed` semantics (identical to the
9B classified.csv). Families use the pre-registered 9B taxonomy.

| probe | cond | ctx | PASS/3 | families |
|---|---|---|---|---|
| P1 | C0 | 64K | 1/3 | 1 exact, 2 terse-wrong |
| P1 | C0 | 128K | 2/3 | 2 exact, 1 terse-wrong |
| P1 | C1a-D | 64K | 0/3 | 3 terse-wrong |
| P1 | C1a-D | 128K | 0/3 | 3 terse-wrong |
| P1b | C0 | 64K | 3/3 | 3 exact |
| P1b | C0 | 128K | 3/3 | 3 exact |
| P1b | C1a-D | 64K | 3/3 | 3 exact |
| P1b | C1a-D | 128K | 2/3 | 2 exact, 1 prose-wrapped |
| P2 | C0 | 64K | 3/3 | 3 exact |
| P2 | C0 | 128K | 3/3 | 3 exact |
| P2 | C1a-D | 64K | 3/3 | 3 exact |
| P2 | C1a-D | 128K | 2/3 | 2 exact, 1 terse-wrong |
| P3 | C0 | 64K | 3/3 | 3 exact |
| P3 | C0 | 128K | 3/3 | 3 exact |
| P3 | C1a-D | 64K | 2/3 | 1 exact, 1 prose-content-ok, 1 prose-wrapped |
| P3 | C1a-D | 128K | 2/3 | 2 exact, 1 prose-wrapped |

Answers to the two validation questions:

1. C1a-D still eliminates the directive-tag/empty family at 128K — YES.
   Zero `empty/directive-only` and zero `directive-prefixed` classifications
   in any 128K cell (C0 or C1a-D). The C1a-D/P1 cells are uniformly
   terse-wrong bare tokens — the same shape as 64K — with no `[[reply...]]`
   or empty emissions. The Step 9B fix-candidate effect is unchanged at 128K.

2. The P1 `!` drop and the prose failures persist without window pressure —
   YES, families are window-size independent between 64K and 128K:
   - P1 `!` drop: C0/128K drops `!` in 1/3 reps (2 exact `PLATANOS!`,
     1 terse `PLATANOS`); C1a-D/128K drops it 3/3, identical to 64K.
   - Prose family: C1a-D/P3 128K has 1/3 prose-wrapped (64K had prose in
     2/3); C1a-D/P1b 128K has 1/3 prose-wrapped (64K was 3/3 exact — n=3
     variance within the known P1b tail). No prose appears at C0/128K, same
     as 64K, consistent with the 9B finding that prose is driven by the
     added environment layers (tool-catalog/assistant-style behavior), not
     by the raw model floor.

## Problems

- One C1a-D/P1 128K rep returned the literal token `NO_REPLY` as its text
  (rep3). It classifies as terse-wrong (single bare token, not a directive
  tag, not empty) but is worth noting: the silent-reply instruction wording
  that survives in C1a-D can occasionally leak as literal output. Same
  family bucket as the bare `PLATANOS` reps, so it does not change the
  verdict.
- The second driver invocation (P1b cells) overwrote `ladder-summary.csv`
  with only its 6 rows; the summary was rebuilt from the 24 durable rep
  JSONs. Per-rep JSONs were always the authoritative record.
- Tool-written scripts using percent-format strings were mangled in transit;
  worked around with dict-print scripts and file-based rebuilds. Cosmetic
  tooling issue only; no evidence affected.

## Decisions

- Probe set: P1 + P3 (the only tool-less answerable member of the
  P3/P4/P5/P7 family) + P1b + P2 control. P4/P5/P7 cannot run under
  C0/C1a-D (their content requires tool declarations); their 128K behavior
  is out of scope for this tool-less ladder.
- Classification reuses the exact frozen scorer via module import
  (`service_step09b_classify.py` -> `frozen()`/`family()`); no scorer edit.
- Summary CSVs are derived files; per-rep JSONs are the durable record.
- The launchd 64K server is authoritative runtime config; the 128K instance
  was a temporary replacement and was removed after the run.

## Next Phase

Awaiting Pete's decision, same fork as after 9B, now with the 128K
confirmation in hand:

- The one measured fix candidate (make reply-directive lines conditional on
  an actual delivery surface) is re-confirmed: C1a-D eliminates the entire
  directive-tag/empty family across P1/P2 at 64K and 128K, with no
  window-size dependence.
- P1 `!`-loss and P3/P4/P5/P7 prose families persist at 128K -> consistent
  with attribution to tool catalog + assistant-style behavior and the pinned
  sampler profile (temp 0.8 defaults), not context window. Any further test
  of the fix candidate would be a new controlled experiment outside 9B/9C
  scope.

## Reproduction

Boot the temporary 128K server (same command line as the launchd job, only
the ctx flag changed; port 18080 must be free first):

    # stop launchd job
    launchctl bootout gui/$(id -u)/com.openclaw.kimi-llama-server
    runtime/live/bin/llama-server -m models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
      -ngl 999 --no-mmap --ctx-size 131072 --host 127.0.0.1 --port 18080 \
      --parallel 1 --slot-save-path runtime/state/slot-cache
    curl -s http://127.0.0.1:18080/health   # expect {"status":"ok"}

Run the ladder (imports the unchanged 9B driver, redirects output root):

    python3 - <<'EOF'
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "s9b", "tools/service_step09b_ablate.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.RESULTS = "benchmarks/results/service-step-09c-verify"
    import sys
    sys.argv = ["x", "--conditions", "C0,C1a-D", "--probes", "P1,P3,P2", "--reps", "3"]
    m.main()
    EOF

Reclassify with the frozen semantics and compare against 64K baselines
(`benchmarks/results/service-step-09b-verify/classified.csv`), then restore
the 64K server:

    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.openclaw.kimi-llama-server.plist
    # verify: ps shows --ctx-size 65536; /health ok
