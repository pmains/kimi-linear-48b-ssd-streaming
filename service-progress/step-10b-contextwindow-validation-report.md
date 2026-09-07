# Step 10B Report — Validate the 65536-Aligned Provider Context Window

## Status

**PASS (determination) — COMPLETE.** STOPPED for review. Config change applied,
gateway restarted, retained liveness workload rerun at the aligned 65536
resolved context window. No sampler, prompt, tool, or llama-server-context
change. One code fix made to the retained analyzer (single-leg crash), no
production patch.

## Objective

Owner order (Pete, 2026-09-05 08:09 MST): align the `llama-server` provider
`contextWindow` from 32768 to 65536 so OpenClaw matches the production
llama-server's actual `--ctx-size 65536`; restart the gateway; rerun the
retained Step 10A real-path liveness workload with identical prompts and
instrumentation; compare overflow rate, task count, wall time, compaction
behavior, and failure families against the frozen 32K-declared baseline
(`benchmarks/results/service-step-10a/64k/`). Do not change sampler, prompt,
tool behavior, or llama-server context. Record config change and results,
update SERVICE-ROADMAP.md, stop for review.

## Changes

- Config (production, authorized): `~/.openclaw/openclaw.json`
  `models.providers.llama-server` model `kimi-linear-48b` `contextWindow`
  32768 → **65536** (surgical; qwen3-32b-a3b/qwen3-8b/oss-120b entries
  untouched). JSON validated. Backup:
  `~/.openclaw/openclaw.json.bak-step10b-20260905`.
- Gateway restarted (detached supervisor `tools/service_step10b_supervise.sh`,
  `openclaw gateway restart`); launchd KeepAlive brought it back. New gateway
  PID 47134, started 2026-09-05 08:20:45 MST; llama-server untouched (64K,
  healthy throughout).
- `tools/service_step10a_leg.sh` + `tools/service_step10a_analyze.py`:
  parameterized with `STEP10A_OUT` / `STEP10A_KEYNS` / `STEP10A_LEGS` so the
  rerun writes to a NEW tree (`benchmarks/results/service-step-10b`) with
  fresh `step10b` session keys, never colliding with the frozen `step10a`
  sessions. Defaults unchanged (10A reproduction identical).
- `tools/service_step10a_analyze.py`: fixed single-leg crash — the
  determination block hardcoded `out["legs"]["128k"]`, so the supervisor's
  auto-analysis of a 64k-only tree died with KeyError before writing
  artifacts. Made 128k-leg references tolerant of absence; re-ran clean.
- Results: `benchmarks/results/service-step-10b/{64k,analysis}/` +
  `supervise.log`.

## Results

### Environment (rerun leg)

Same live endpoint the agent uses: 127.0.0.1:18080, MXFP4, `--parallel 1`,
llama-server `--ctx-size 65536` (unchanged). Gateway restarted with the 65536
config. P1/P2 x 9 = 18 fresh headless real-path turns, `step10b` session
namespace, identical prompts (`benchmarks/results/service-step-09/prompts/`)
and instrumentation (llama/gateway log windows, /slots, final CLI docs).

### Config discriminator — HIT

| metric | frozen 32K baseline (10A 64k) | 10B rerun (aligned 65536) |
|---|---|---|
| resolved `contextTokens` | 32768 (17/17 parsed docs) | **65536 (18/18 docs)** |
| precheck `context_overflow` | 0/18 | **0/18** |
| rc=0 | 17/18 | 17/18 |
| rc=1 | 1/18 (P2-r7, 882.5 s, compaction loop-guard abort) | 1/18 (P1-r5, 1202.5 s, client timeout — NOT a precheck refusal) |
| wall min / median / max (s) | 26.8 / 110.1 / 882.5 | 13.0 / 22.5 / 1202.5 |
| prompt tokens at last OK call, median | 12,734 | 12,736 |
| llama tasks per turn (typical) | 2 (multi-round common) | 1 (mostly single-task) |

Every 10B rep reports `contextTokens: 65536 (contextTokensSource: resolved)`
— the gateway now resolves the precheck bound from the aligned config, and
the bound matches the server's real 64K window.

### Overflow family

Zero precheck `context_overflow` refusals in the rerun (0/18), same as the
frozen baseline sample (0/18). The bound that produced the retained Step-10
failures at 582–824 s (client-side precheck at 32768, config-resolved) has
moved to 65536; assembled prompts in this leg stayed far below it
(promptTokens max 28,964 on an rc=0 long-wall rep; 23,819 on the timed-out
rep). At this n the overflow-rate comparison is not statistically
discriminating (0 vs 0), but the mechanism is confirmed: the precheck ceiling
is now the server-aligned 64K, not the previous 32K config artifact.

### Long-wall family (rc=1)

- Baseline P2-r7 (882.5 s, 17 llama tasks): auto-compaction loop-guard abort
  (`tool read repeated 3 times ... after auto-compaction ... Aborting`).
- 10B P1-r5 (1202.5 s, 130 llama tasks, `doc_status: timeout`): a read-tool
  loop ("Read failed") that ran until the 1200 s client timeout —
  `liveness: paused`, `replayInvalid: true`. Same runaway-tool-loop family as
  the baseline abort; terminated by the client timeout rather than the
  compaction loop-guard on this draw. NOT a context refusal (promptTokens
  23,819 < 65536).

### Wall time / task count

Median wall dropped 110.1 s -> 22.5 s and most reps became single-task
(1 llama task vs the baseline's typical 2+). The trajectory shape is
agent-stochastic, so this n=18 difference is reported as observed, not
causally attributed to the config change.

## Problems

1. Supervisor's auto-analysis crashed with `KeyError: '128k'` (analyzer
   hardcoded the absent 128k leg in the determination block) — the supervisor
   still logged `analysis rc=0` while writing a traceback to analysis-run.log
   because the `$?` captured the tee'd tail, not the python rc; artifacts were
   absent until I patched and re-ran. Fixed + verified; analysis artifacts now
   present.
2. P1-r5 hit the 1200 s client timeout (read-tool loop, 130 llama tasks) —
   same liveness family as 10A's compaction abort, preserved as evidence.

## Decisions

- Applied the owner-authorized config alignment (32768 → 65536) with backup;
  gateway restarted via detached supervisor so the change is live.
- Reran the retained workload into a NEW results tree with fresh session keys
  to avoid contaminating the frozen baseline.
- Kept the analyzer crash fix scoped to single-leg tolerance (no analysis
  semantics changed for the 10A two-leg path).
- No production patch beyond the authorized config change; no sampler/prompt/
  tool/llama-server-context changes.

## Next Phase

- Owner review. The precheck overflow family's config-side cause is removed
  (bound now 65536 == server 64K); if residual long-wall tool-loop failures
  (compaction guard / client timeout) matter for liveness, they are the
  Step 10A-identified second family and belong in the latency/liveness
  workstream, not a context-config change.
- Step 11 (Qualify Usable Agent Context) remains the next roadmap step after
  review.

## Reproduction

```sh
cd /Users/pmains/Code/openclaw/kimi
# supervisor (gateway restart + rerun + analyze) — run detached:
python3 -c "import os,sys; os.setsid(); os.execv(sys.argv[1], sys.argv[1:])" \
    /bin/bash tools/service_step10b_supervise.sh
# leg alone into a fresh tree:
STEP10A_OUT="$(pwd)/benchmarks/results/service-step-10b" STEP10A_KEYNS=step10b \
    STEP10A_REPS=9 bash tools/service_step10a_leg.sh 64k
# analysis (single-leg tree):
STEP10A_OUT="$(pwd)/benchmarks/results/service-step-10b" STEP10A_LEGS=64k \
    python3 tools/service_step10a_analyze.py
```

Artifacts: `benchmarks/results/service-step-10b/supervise.log`,
`benchmarks/results/service-step-10b/64k/` (18 per-turn record/client/llama-
window/gateway-window files, env.txt), `benchmarks/results/service-step-10b/
analysis/` (analysis.json, analysis-summary.txt). Config backup:
`~/.openclaw/openclaw.json.bak-step10b-20260905`. Frozen baseline:
`benchmarks/results/service-step-10a/64k/`.
