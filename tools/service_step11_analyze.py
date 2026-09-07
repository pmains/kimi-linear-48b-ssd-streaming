#!/usr/bin/env python3
"""Step 11 — usable-context analyzer (retained, 2026-09-05).

Reads the per-turn artifacts of the three sustained sessions
(benchmarks/results/service-step-11/64k/{R,C,G}/<label>.*) and produces:

  per-turn: rc, wall_s, timed_out, promptTokens (assembled ctx), contextTokens,
            usage {input, output, cacheRead}, llama prompt-eval tasks,
            first-prompt-eval ms/tokens (TTFT proxy), decode tok/s,
            compaction-event count (gateway window), loop-event count,
            reply length
  per-session: promptTokens progression (growth), cacheRead share, wall vs
            promptTokens (latency curve), compaction point(s), failures

Usage: python3 tools/service_step11_analyze.py [OUT_ROOT]
Writes: <OUT_ROOT>/analysis/summary.json + printed session tables.
"""
import json, os, re, sys, glob

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "benchmarks/results/service-step-11")
LEG = os.path.join(OUT_ROOT, "64k")

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def llama_stats(window_path):
    """Return dict of llama-window aggregate stats."""
    if not os.path.exists(window_path):
        return None
    pe_tokens, pe_ms, ev_tokens, ev_ms = [], [], [], []
    pe_task_ms = []
    with open(window_path, errors="replace") as f:
        for line in f:
            m = re.search(r"task (\d+) \|\s+prompt eval time =\s+([\d.]+) ms /\s+(\d+) tokens", line)
            if m:
                pe_task_ms.append((float(m.group(2)), int(m.group(3))))
                continue
            m = re.search(r"task \d+ \|\s+eval time =\s+([\d.]+) ms /\s+(\d+) tokens", line)
            if m:
                ev_ms.append(float(m.group(1)))
                ev_tokens.append(int(m.group(2)))
    tasks = len(pe_task_ms)
    first_pe_ms = pe_task_ms[0][0] if pe_task_ms else None
    first_pe_tok = pe_task_ms[0][1] if pe_task_ms else None
    tot_pe_tok = sum(t for _, t in pe_task_ms)
    decode_tps = (sum(ev_tokens) / (sum(ev_ms) / 1000.0)) if ev_ms and sum(ev_ms) > 0 else None
    return {
        "prompt_eval_tasks": tasks,
        "first_prompt_eval_ms": first_pe_ms,
        "first_prompt_eval_tokens": first_pe_tok,
        "total_prefill_tokens": tot_pe_tok,
        "decode_tokens_per_s": round(decode_tps, 2) if decode_tps else None,
        "eval_lines": len(ev_ms),
    }

def event_counts(window_path, patterns):
    if not os.path.exists(window_path):
        return 0, []
    hits = []
    rx = re.compile(patterns, re.IGNORECASE)
    with open(window_path, errors="replace") as f:
        for line in f:
            if rx.search(line):
                hits.append(line.strip()[:160])
    return len(hits), hits[:4]

def main():
    summary = {"out_root": OUT_ROOT, "sessions": {}}
    for session in ("R", "C", "G"):
        reps = {}
        for cj in sorted(glob.glob(os.path.join(LEG, session, "*.client.json"))):
            label = os.path.basename(cj)[: -len(".client.json")]
            rec = load_json(cj) or {}
            summ = rec.get("summary", rec)
            base = cj[: -len(".client.json")]
            doc = load_json(base + ".out")
            meta = ((doc or {}).get("result", {}) or {}).get("meta", {}) if doc else {}
            am = meta.get("agentMeta") or {}
            ll = llama_stats(base + ".llama.window.log")
            compact_n, compact_hits = event_counts(base + ".gateway.window.log",
                r"auto-compaction|compaction start|context.*compact|compact.*context")
            loop_n, loop_hits = event_counts(base + ".gateway.window.log",
                r"Loop warning|Critical generic loop|circuit breaker|tool-loop|post-compaction guard")
            reply = ""
            try:
                reply = open(base + ".reply.txt", errors="replace").read().strip()
            except OSError:
                pass
            item = {
                "session": session, "label": label,
                "rc": summ.get("rc"), "wall_s": summ.get("wall_s"),
                "timed_out": summ.get("timed_out"),
                "doc_status": (doc or {}).get("status"),
                "promptTokens": am.get("promptTokens"),
                "contextTokens": am.get("contextTokens"),
                "usage": am.get("usage"),
                "llama": ll,
                "compaction_events": compact_n,
                "compaction_first": compact_hits[0] if compact_hits else None,
                "loop_events": loop_n,
                "reply_chars": len(reply),
                "reply_head": reply[:200],
            }
            reps[label] = item
        summary["sessions"][session] = reps

    os.makedirs(os.path.join(OUT_ROOT, "analysis"), exist_ok=True)
    with open(os.path.join(OUT_ROOT, "analysis", "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)

    for session in ("R", "C", "G"):
        reps = summary["sessions"][session]
        print(f"\n=== Session {session} ({len(reps)} turns) ===")
        print(f"{'turn':<5}{'rc':>3}{'wall_s':>9}{'pTok':>7}{'in':>7}{'cr':>8}{'out':>5}"
              f"{'tasks':>6}{'TTFTms':>8}{'dec/tps':>8}{'cmp':>4}{'loop':>5}")
        prev = None
        for label in sorted(reps, key=lambda s: int(re.sub(r"\D", "", s) or 0)):
            it = reps[label]
            u = it["usage"] or {}
            ll = it["llama"] or {}
            pt = it["promptTokens"]
            growth = f"+{pt - prev}" if (prev is not None and pt and prev) else ""
            prev = pt if pt else prev
            print(f"{label:<5}{str(it['rc']):>3}{str(round(it['wall_s'],1) if it['wall_s'] else '-'):>9}"
                  f"{str(pt):>7}{str(u.get('input')):>7}{str(u.get('cacheRead')):>8}{str(u.get('output')):>5}"
                  f"{str(ll.get('prompt_eval_tasks')):>6}{str(round(ll['first_prompt_eval_ms']) if ll.get('first_prompt_eval_ms') else '-'):>8}"
                  f"{str(ll.get('decode_tokens_per_s')):>8}{str(it['compaction_events']):>4}{str(it['loop_events']):>5}")
    print(f"\nwrote {os.path.join(OUT_ROOT, 'analysis', 'summary.json')}")

if __name__ == "__main__":
    main()
