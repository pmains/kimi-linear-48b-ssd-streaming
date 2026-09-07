#!/usr/bin/env node
// service_step10c_replay.mjs — Step 10C offline replay
// Replays retained real anchor tool-call streams (10A P2-r7, 10B P1-r5) through
// the ACTUAL shipped OpenClaw rolling-history loop detector
// (dist/tool-loop-detection-*.js) as if tools.loopDetection.enabled were true.
//
// Mirrors the runtime admission/outcome flow per call:
//   1. pre-call: detectToolCallLoop(state, name, args, {enabled:true}, {runId})
//      -> record first warning / first critical (would-block) firing point
//   2. post-call: recordToolCall + recordToolCallOutcome with the real observed
//      result/error text so hashes match what the runtime would have recorded.
//
// Usage: node tools/service_step10c_replay.mjs [streams.json] [out.json]
// Repo: github.com/pmains/kimi-linear-48b-ssd-streaming

import { readFileSync, writeFileSync } from "node:fs";

const DIST = "/opt/homebrew/lib/node_modules/openclaw/dist/tool-loop-detection-CWrUtzrR.js";
const mod = await import(`file://${DIST}`);
const detectToolCallLoop = mod.t; // detectToolCallLoop
const recordToolCall = mod.r;     // recordToolCall
const recordToolCallOutcome = mod.i; // recordToolCallOutcome

const streamsPath = process.argv[2] ?? "benchmarks/results/service-step-10c/anchor-call-streams.json";
const outPath = process.argv[3] ?? "benchmarks/results/service-step-10c/replay/replay-results.json";
const streams = JSON.parse(readFileSync(streamsPath, "utf8"));

const CONFIG = { enabled: true }; // resolveLoopDetectionConfig fills defaults

function outcomeOf(call) {
  // Reconstruct the tool outcome the runtime would have hashed.
  // isError calls -> error branch (noProgress:true). All 117 read failures are
  // byte-identical, so identical resultHash regardless of exact shape.
  if (call.isError) {
    return { error: { message: call.resultText ?? "error" } };
  }
  let parsed = null;
  try { parsed = JSON.parse(call.resultText ?? ""); } catch { parsed = null; }
  if (parsed !== null && typeof parsed === "object") return { result: parsed };
  return { result: { text: call.resultText ?? "" } };
}

const summary = {};
for (const [label, stream] of Object.entries(streams)) {
  const state = { toolCallHistory: [] };
  const events = [];
  let firstWarning = null;
  let firstCritical = null;
  let warnings = 0;
  let criticals = 0;
  for (const call of stream.calls) {
    const scope = call.runId ? { runId: call.runId } : undefined;
    // 1) pre-call detection on history-so-far (excludes the pending call)
    const verdict = detectToolCallLoop(state, call.name, call.args, CONFIG, scope);
    if (verdict.stuck) {
      const ev = {
        ordinal: call.ordinal,
        level: verdict.level,
        detector: verdict.detector,
        count: verdict.count,
        toolName: call.name,
        warningKey: verdict.warningKey ?? null,
      };
      events.push(ev);
      if (verdict.level === "critical") {
        criticals += 1;
        if (!firstCritical) firstCritical = { ...ev, message: verdict.message };
      } else {
        warnings += 1;
        if (!firstWarning) firstWarning = { ...ev, message: verdict.message };
      }
    }
    // 2) admission + outcome recording (what the runtime stores when enabled)
    recordToolCall(state, call.name, call.args, call.id, CONFIG, scope);
    const outcome = outcomeOf(call);
    recordToolCallOutcome(state, {
      toolName: call.name,
      toolParams: call.args,
      toolCallId: call.id,
      result: outcome.result,
      error: outcome.error,
      config: CONFIG,
      runId: call.runId ?? undefined,
    });
  }
  summary[label] = {
    sessionId: stream.session_id,
    observedCalls: stream.calls.length,
    observedWallMs: stream.calls.length ? (stream.calls.at(-1).tsResult ?? stream.calls.at(-1).ts) - stream.t0 : 0,
    firstWarning,
    firstCritical,
    warnings,
    criticals,
    totalEvents: events.length,
    events: events.slice(0, 40), // cap retained event list
  };
  const fc = summary[label].firstCritical;
  const fw = summary[label].firstWarning;
  const wallAt = (ordinal) => {
    const c = stream.calls.find((x) => x.ordinal === ordinal);
    if (!c) return null;
    const t = c.tsResult ?? c.ts;
    return { wallMsFromT0: t - stream.t0, iso: new Date(t).toISOString() };
  };
  summary[label].firstWarningWall = fw ? wallAt(fw.ordinal) : null;
  summary[label].firstCriticalWall = fc ? wallAt(fc.ordinal) : null;
  // Documented semantics: first critical blocks the whole tool batch; the model
  // gets one more response; a SECOND critical in the same run ends the run.
  const criticalEvents = events.filter((e) => e.level === "critical");
  summary[label].secondCritical = criticalEvents.length >= 2 ? criticalEvents[1] : null;
  summary[label].secondCriticalWall = summary[label].secondCritical ? wallAt(summary[label].secondCritical.ordinal) : null;
  const fwW = summary[label].firstWarningWall;
  const fcW = summary[label].firstCriticalWall;
  const sc = summary[label].secondCritical;
  console.log(`\n=== ${label} (${stream.calls.length} observed calls) ===`);
  console.log(`  warnings: ${warnings}${fw ? `; first at ordinal ${fw.ordinal} (${fw.detector}, count=${fw.count})` : ""}`);
  console.log(`  criticals: ${criticals}${fc ? `; first at ordinal ${fc.ordinal} (${fc.detector}, count=${fc.count})` : ""}`);
  if (fc) console.log(`    -> would-block message: ${fc.message.slice(0, 160)}`);
  if (fw && fwW) console.log(`    -> first warning wall: +${(fwW.wallMsFromT0 / 1000).toFixed(1)}s`);
  if (fc && fcW) console.log(`    -> first critical wall: +${(fcW.wallMsFromT0 / 1000).toFixed(1)}s`);
  if (sc) console.log(`    -> second critical (run-end per docs) at ordinal ${sc.ordinal}`);
}

writeFileSync(outPath, JSON.stringify(summary, null, 1));
console.log(`\nwrote ${outPath}`);
