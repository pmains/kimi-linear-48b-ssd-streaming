# Phase 9G Full Experiment — Analysis/Reporting Conventions

Status: **ACTIVE — recorded 2026-08-28 17:25 MST (Pete), after harness acceptance**

These are analysis/reporting conventions for the full Phase 9G experiment.
They do NOT change the frozen harness (`tools/phase09g_run_brackets.sh`),
the frozen analyzer (`tools/phase09g_analyze.py`), or the frozen protocol
(TOOLS.md "Phase 9G Benchmark Protocol"). Freeze means freeze: no harness
or protocol changes before Session 1.

## Note 1 — A-before / A-after are A1/A2 replicates

Because each bracket's three labeled runs execute in a seeded random
permutation, "A-before" and "A-after" are not necessarily temporally
before/after B. In analysis and reporting, treat them as the two A control
replicates (A1, A2) of the bracket:

- A1 = the run labeled `A-before` (role: frozen A control)
- A2 = the run labeled `A-after`  (role: frozen A control)

The harness labels and directory names stay as-is (`b<i>-A-before`,
`b<i>-A-after`); do NOT rename them. Only the analysis/reporting language
changes (A1/A2), and only where temporal order is not implied.

Paired speedup remains:

    S_i = tok/s(B_i) / mean(tok/s(A1,i), tok/s(A2,i))

## Note 2 — analysis variables to retain and report

Retain these per-bracket variables for the full experiment:

1. **B temporal position** — first / second / third within the bracket.
   Already recorded: `harness.json` → `bracket_orders` (seeded random
   permutation); analyzer already reports `b_position`
   (first/middle/last) and `s_by_position`.
2. **Session ID** — the session directory under
   `benchmarks/results/phase-09g/<session-dir>/`. Analyzer runs per
   session (per OUTDIR), so S_i is naturally grouped by session.
3. **Live-server activity/idle state** — already captured cheaply by the
   frozen harness: `env/b<i>-before.json` / `env/b<i>-after.json` contain
   `live_server_health` and `live_server_procs` (plus loadavg, memory
   pressure, top CPU, thermal) before and after every bracket. Derive a
   per-bracket live-server state from these existing snapshots.

Reporting requirements:

- Report S_i **by session** (do not pool brackets across sessions into a
  single IID sample).
- Inspect S_i **by B temporal position**, especially for the null
  experiment (position effects are part of the phenomenon).
- Between-session variation is a studied component, not noise to
  aggregate away.

## Operational note — idle-window watcher fix (2026-08-28 17:33 MST)

The `9g-idle-window-watcher` cron job was failing every evaluation with
`code mode module access is disabled`: its trigger script used Node
`require('child_process')`, which the trigger sandbox disallows. The
script was rewritten to the documented trigger API
(`tools.call('exec', ...)` + `json({ fire })`) with IDENTICAL thresholds
(loadavg 1-min/5-min < 1.5, free memory >= 50%, live llama-server
healthy, no phase09g/llama-cli running, 21:00-08:00 host-local idle
window). No thresholds were tuned; this is an API-compatibility fix only.
Verified: syntax check passes; at 17:25 MST the trigger correctly
returns fire:false (outside idle window). Next scheduled evaluation
~17:37 MST.

## Provenance — watcher fix disposition (Pete, 2026-08-28 18:09 MST)

Pete reviewed the watcher fix and the analysis conventions and confirmed:

1. **Watcher fix is infrastructure repair, not experimental tuning** —
   it does not violate the freeze. The fix changed *how* the
   predetermined eligibility test executes (trigger API: Node
   `require('child_process')` → `tools.call('exec', ...)`), not *what*
   constitutes an eligible window. Thresholds and the resulting boolean
   behavior are unchanged; the relevant cases were validated.
2. **Analysis variables are latent in frozen artifacts** — B temporal
   position (`bracket_orders` / `b_position`), session ID, and
   live-server state (`env/b<i>-{before,after}.json`) were already
   recorded by the frozen harness before the pilot was analyzed. Notes
   1–3 above only specify how already-recorded fields will be labeled
   and analyzed; they do not modify data collection after seeing the
   pilot.
3. **No further changes.** Freeze stands. Let the watcher operate; when
   it identifies the first qualifying window, propose Session 1 to Pete,
   and on approval run the frozen null + positive-control protocol.

**Phase 9G report requirement:** the provenance section of
`progress/phase-09g-report.md` MUST document the watcher failure/fix,
including that it occurred BEFORE Session 1 and BEFORE collection of the
full experiment data — making it transparent that the change could not
have been motivated by the full experiment's results.

## Usage

The phase-09g report (`progress/phase-09g-report.md`) and any
cross-session aggregation must follow these conventions. Session 1 runs
exactly as frozen; these notes only govern how the data are labeled and
analyzed afterward.
