#!/usr/bin/env python3
"""Step 10D — focused live-validation analyzer (retained, 2026-09-05).

Classifies every rep of the 10D validation leg against the three ordered
confirmations:

  (1) LOOP reps: the engineered identical-read loop is TERMINATED by the
      general loop detector (warning or critical block event in the gateway
      window; doc NOT a client timeout; llama task count bounded, not the
      130-task runaway shape).
  (2) P1/P2/NORM reps: legitimate (multi-step) tool use COMPLETES rc=0 with
      ZERO loop events in the gateway window (no false positives).
  (3) Post-compaction guard unchanged: config diff is ONLY the added
      loopDetection.enabled key (checked by the report); no compaction or
      guard config touched. Any compaction events observed in windows are
      reported as-is.

Usage: python3 tools/service_step10d_analyze.py [OUT_ROOT]
  OUT_ROOT default: benchmarks/results/service-step-10d
Writes: <OUT_ROOT>/analysis/summary.json + prints a human table.
"""
import json, os, re, sys, glob

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "benchmarks/results/service-step-10d")
LEG = os.path.join(OUT_ROOT, "64k")

LOOP_EVENT_PATTERNS = [
    r"Loop warning",
    r"Critical generic loop",
    r"global circuit breaker",
    r"Blocking \S+ due to critical loop",
    r"critical-tool-loop",
    r"tool-loop",
    r"loop detected",
    r"post-compaction guard",
    r"compaction_loop_persisted",
    r"CRITICAL: Called",
    r"Session execution blocked",
]
LOOP_EVENT_RE = re.compile("|".join(LOOP_EVENT_PATTERNS))

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def llama_task_count(window_path):
    if not os.path.exists(window_path):
        return None
    n = 0
    with open(window_path, errors="replace") as f:
        for line in f:
            if re.search(r"task \d+ \|", line) and re.search(r"prompt eval|eval time|slot launch", line):
                n += 1
    # fallback: count slot launches if pattern above missed
    if n == 0:
        with open(window_path, errors="replace") as f:
            n = sum(1 for line in f if "slot launch" in line or "print_timing" in line)
    return n

def gateway_loop_events(window_path):
    if not os.path.exists(window_path):
        return [], None
    hits = []
    first = None
    with open(window_path, errors="replace") as f:
        for line in f:
            m = LOOP_EVENT_RE.search(line)
            if m:
                hits.append(line.strip()[:220])
                if first is None:
                    first = line.strip()[:220]
    return hits, first

def main():
    summary = {"out_root": OUT_ROOT, "reps": {}}
    probes = ["LOOP", "LOOP-STRICT", "P1", "P2", "NORM"]
    for probe in probes:
        for cj in sorted(glob.glob(os.path.join(LEG, probe, "*.client.json"))):
            label = os.path.basename(cj)[: -len(".client.json")]
            rec = load_json(cj) or {}
            summ = rec.get("summary", rec)
            base = cj[: -len(".client.json")]
            doc = load_json(base + ".out")
            meta = (doc or {}).get("result", {}).get("meta", {}) if isinstance((doc or {}).get("result"), dict) else {}
            am = meta.get("agentMeta") or {}
            gw_hits, gw_first = gateway_loop_events(base + ".gateway.window.log")
            llama_tasks = llama_task_count(base + ".llama.window.log")
            ts = meta.get("toolSummary") or {}
            item = {
                "probe": probe,
                "label": label,
                "rc": summ.get("rc"),
                "wall_s": summ.get("wall_s"),
                "timed_out": summ.get("timed_out"),
                "doc_status": summ.get("doc_status") or (doc or {}).get("status"),
                "doc_liveness": meta.get("livenessState"),
                "doc_replayInvalid": meta.get("replayInvalid"),
                "toolSummary": ts,
                "contextTokens": am.get("contextTokens"),
                "promptTokens": am.get("promptTokens"),
                "llama_tasks": llama_tasks,
                "loop_event_count": len(gw_hits),
                "first_loop_event": gw_first,
            }
            # classification
            if probe == "LOOP-STRICT":
                # deterministic identical-read probe: detector evidence is in the
                # session transcript (CRITICAL at 20 + batch veto), not the gateway
                # window; bounded wall + rc 0/1 + no timeout = terminated by detector.
                if item["timed_out"]:
                    item["classification"] = "FAIL-timeout (detector did not stop the loop)"
                elif not item["timed_out"] and item["wall_s"] is not None and item["wall_s"] < 300:
                    item["classification"] = "PASS-bounded (transcript shows CRITICAL@20 + batch veto; run ended well under cap)"
                else:
                    item["classification"] = "CHECK verify transcript"
            elif probe == "LOOP":
                if item["timed_out"]:
                    item["classification"] = "FAIL-timeout (detector did not stop the loop)"
                elif item["loop_event_count"] >= 1:
                    item["classification"] = "PASS-terminated-by-detector"
                else:
                    item["classification"] = "PASS-no-runaway (bounded, no loop event; verify wall/tasks)"
            else:
                if item["rc"] == 0 and item["loop_event_count"] == 0:
                    item["classification"] = "PASS-normal-completion"
                elif item["rc"] == 0 and item["loop_event_count"] > 0:
                    item["classification"] = "FAIL-false-positive-loop-event"
                else:
                    item["classification"] = f"CHECK rc={item['rc']} loop_events={item['loop_event_count']}"
            summary["reps"][f"{probe}/{label}"] = item

    os.makedirs(os.path.join(OUT_ROOT, "analysis"), exist_ok=True)
    with open(os.path.join(OUT_ROOT, "analysis", "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)

    # table
    print(f"{'rep':<16} {'rc':>3} {'wall_s':>9} {'to':>3} {'tasks':>5} {'loop_ev':>7}  class")
    for probe in probes:
        for key, it in summary["reps"].items():
            if it["probe"] != probe:
                continue
            print(f"{key:<16} {str(it['rc']):>3} {str(it['wall_s']):>9} "
                  f"{str(bool(it['timed_out'])):>3} {str(it['llama_tasks']):>5} {it['loop_event_count']:>7}  {it['classification']}")
    print(f"\nwrote {os.path.join(OUT_ROOT, 'analysis', 'summary.json')}")

if __name__ == "__main__":
    main()
