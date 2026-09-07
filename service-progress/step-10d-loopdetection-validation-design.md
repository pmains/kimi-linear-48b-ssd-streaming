# Step 10D Design — Apply + Live-Validate the LoopDetection Safeguard

Date: 2026-09-05 · Author: Alkaline (agent) · Authorization: Pete *** MST
Repo: `github.com/pmains/kimi-linear-48b-ssd-streaming` <!-- project: github.com/pmains/kimi-linear-48b-ssd-streaming -->

## Objective

Apply the Step 10C smallest liveness safeguard as a production config change
and validate it live:

- **Change (only change):** `agents.entries.kimi.tools.loopDetection.enabled: true`
  in `~/.openclaw/openclaw.json` (arms the shipped general rolling-history
  loop detector for the kimi agent; thresholds hardcoded by product: warn at
  10 identical calls, CRITICAL block at 20 identical no-progress outcomes,
  global breaker at 30, history window 30, per-run scoped).
- **Restart the gateway** (required for config load; launchd KeepAlive).
- **Focused live validation** confirming:
  1. a P1-r5-style identical-read loop is terminated by the general detector;
  2. legitimate multi-step tool use still completes (no false positives);
  3. the post-compaction guard behavior is unchanged.

Make **no other config or code changes** (no context size / sampler / prompt
/ tool-behavior / llama-server change).

## Baseline anchors (retained)

- P1-r5 (10B, aligned 64K): 130 llama tasks / 1202.5 s client timeout;
  117 byte-identical `read` calls of nonexistent `/Users…DMAP.md`, all
  byte-identical failures. Offline replay (10C) of this exact stream through
  the shipped detector: first warning ordinal 13, first CRITICAL ordinal 24,
  run-end ordinal 27.
- P2-r7 (10A): compaction-cycle variant; terminated by the post-compaction
  guard (17 tasks / 882.5 s). The general detector would NOT fire there
  (streak ≤ 8 < 20) — post-compaction guard remains its correct terminator.

## Validation design (live, real path, single-slot 64K server)

Fresh headless sessions (`step10d` namespace), instrumented per turn with the
retained `tools/service_step10a_turn.py` (llama log window, gateway log
window, /slots, final CLI doc, client.json). Evidence tree:
`benchmarks/results/service-step-10d/`.

Probes (all fresh sessions, run sequentially on the single-slot server):

| Probe | Reps | Prompt | Expected outcome |
|---|---|---|---|
| LOOP | 3 | engineered identical-read loop (`read` of a fixed nonexistent path, "keep retrying the exact same read; do not use other tools") | detector warns at 10, CRITICAL block at 20, run ends by second critical — terminated well before the 600 s client cap, gateway window shows loop events |
| P1 | 2 | retained P1 ("Respond only PLATANOS!") — the workload prompt that produced the P1-r5 anchor | rc=0 normal rep; if a loop ever starts, terminated by the general detector (not 1200 s) |
| P2 | 2 | retained P2 (exact JSON) — the other workload probe | rc=0 normal rep |
| NORM | 2 | genuine multi-tool task (read AGENTS.md, exec ls tools, read TOOLS.md, progress_card) | rc=0, multiple distinct tool calls, zero loop events (no false positive) |

Timeout per turn: 600 s (client cap) so a LOOP regression fails fast and
loudly (timeout + many llama tasks) instead of burning 1200 s.

### Confirmation 1 — loop terminated by the general detector

LOOP reps must end with loop-detection evidence in the gateway window
(generic_repeat warning lines, "Blocking … critical loop", tool-loop block
events) and a terminal state that is NOT a client timeout. Expected wall
≈ 3–6 min per looping rep (replay bound: first CRITICAL at ~call 24 /
~+235 s). If a LOOP rep times out at 600 s with many identical read tasks,
that is a regression signal.

### Confirmation 2 — legitimate multi-step tool use completes

NORM + P1/P2 reps: rc=0, expected tool calls executed, **zero** loop events
in their gateway windows.

### Confirmation 3 — post-compaction guard unchanged

Code/docs semantics: the post-compaction guard is disabled only when
`tools.loopDetection.enabled` is explicitly `false`; setting `true` keeps it
armed (docs/tools/loop-detection.md). Config diff must show ONLY the added
`loopDetection.enabled` key — no compaction/post-compaction setting touched.
Behavioral check: any rep that compacts must still show the guard armed path;
no change to the P2-r7 anchor's terminator is expected or observed.

## Procedure (gateway restart kills the agent's own host process — detached supervisor, 10B pattern)

1. Backup config → `~/.openclaw/openclaw.json.bak-step10d-20260905`; apply the
   single config line (surgical JSON edit; python json.load validation;
   diff shows one added key). No other change.
2. Retain prompts + leg/analyze drivers.
3. Launch detached supervisor `tools/service_step10d_supervise.sh`
   (own session via `os.setsid()`): sleep 30 s (let this turn finish) →
   `openclaw gateway restart --json` → wait gateway health + llama-server
   health (llama untouched, must stay 64K) → run leg
   (`tools/service_step10d_leg.sh` → `benchmarks/results/service-step-10d`)
   → run analyzer → log `supervise.log`.
4. On resume after the restart: verify config live, read supervise.log +
   analysis, classify reps against the three confirmations.

## Deliverables / STOP

- SERVICE-ROADMAP.md §10D updated (change + results) + report
  `service-progress/step-10d-loopdetection-validation-report.md`.
- **STOP for review.** Only the authorized config line is changed.
