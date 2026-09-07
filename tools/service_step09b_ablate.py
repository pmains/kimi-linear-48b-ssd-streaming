#!/usr/bin/env python3
"""Step 9B — agent-trajectory quality localization ladder (retained driver, v2).

Replays conditions C0..C4 (+C1a-D/C1a-T/C2a-D/C2a-T), R1, R2 against the LIVE
llama-server endpoint (127.0.0.1:18080) exactly as the agent path calls it
(no sampler fields -> llama defaults; model id kimi-linear-48b; tools in the
OpenAI wrapped form llama-server requires). Nothing is patched: prompts and
expected outputs come from the frozen suite; system text and tool definitions
come from the retained Phase-0 payloads plus the frozen dist that built the
agent requests (trajectory store truncates oversized JSON at write time, so
the 29-tool wire array is reconstructed: entries 0-8 + read/write byte-exact
from retention, remaining 18 from the frozen dist tool factories).

Usage:
  python3 tools/service_step09b_ablate.py [--conditions C0,C1,C1a-D,C1a-T,C2,C2a-D,C2a-T]
                                          [--probes P1,P1b,P3,P2]
  python3 tools/service_step09b_ablate.py --quick     # validation subset
  python3 tools/service_step09b_ablate.py --replays   # R1/R2 Class-II replays
  python3 tools/service_step09b_ablate.py --full      # ladder + replays

Frozen-match semantics mirror tools/service_step09_qualify.sh. This file does
NOT modify the frozen suite.
"""
import argparse, hashlib, json, os, re, sys, time, urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(BASE, "benchmarks/results/service-step-09b-verify")
PHASE0 = os.path.join(RESULTS, "phase0")
SUITE = os.path.join(BASE, "benchmarks/results/service-step-09")  # frozen prompts/expected
URL = "http://127.0.0.1:18080/v1/chat/completions"
MODEL = "kimi-linear-48b"
N_REPS = 3

# ---------- frozen suite reads ----------
def frozen_prompt(probe):
    with open(os.path.join(SUITE, "prompts", f"{probe}.md"), "rb") as f:
        return f.read().decode("utf-8")

def frozen_expected(probe):
    with open(os.path.join(SUITE, "expected", f"{probe}.json")) as f:
        return json.load(f)

# ---------- retained phase-0 payloads ----------
def tools_full_29():
    """Full 29-tool wire catalog: 11 byte-exact from retained payloads
    (apply_patch..intent + read/write) + 18 reconstructed from the frozen
    dist tool factories (trajectory store truncated entries 9-28 to literal
    '[Truncated]' at write time). Flat internal form; wrap for llama-server."""
    with open(os.path.join(PHASE0, "tools-full-29.json")) as f:
        return json.load(f)

def tools_wrapped():
    return [{"type": "function", "function": t} for t in tools_full_29()]

def sys_text_r2():
    with open(os.path.join(PHASE0, "system-prompt-r2-p1.txt")) as f:
        return f.read()

def phase0_compiled(run):
    with open(os.path.join(PHASE0, "payloads", f"{run}.compiled.json")) as f:
        return json.load(f)

def phase0_traj(run):
    evs = []
    with open(os.path.join(PHASE0, f"traj-{run}.jsonl")) as f:
        for chunk in f.read().split("\n\n"):
            chunk = chunk.strip()
            if chunk:
                try: evs.append(json.loads(chunk))
                except Exception: pass
    return evs

# ---------- system variants (one-variable deletions, diffs retained) ----------
DIRECTIVES_HDR = "## Assistant Output Directives"
TOOLPOLICY_LINES = [
    "Tools policy-filtered. Names case-sensitive; call exact.",
    "## Tool Call Style",
    "Routine low-risk: call silently.",
    "Narrate only complex, sensitive/destructive, or requested steps.",
    "First-class tool exists: use it; never ask user for equivalent CLI/slash.",
    "/approve is user command; never execute via shell/tool.",
    "allow-once = one command. Another elevated command needs fresh /approve.",
    "Approval preview: exact full command/script, including chains/multiline. Keep preview separate from /approve; never use script as approval id/slug.",
    "## OpenClaw Control",
    "Do not invent commands.",
    "System controls unavailable; ask human.",
]

def system_variants():
    base = sys_text_r2()
    d = base.split("\n")
    # C1a-D: remove the reply-directive instruction section only.
    keep, i = [], 0
    while i < len(d):
        if d[i].strip() == DIRECTIVES_HDR:
            i += 1
            while i < len(d) and (d[i].startswith("- ") or d[i].strip() == ""):
                i += 1
            continue
        keep.append(d[i]); i += 1
    sys_d = "\n".join(keep)
    # C1a-T: remove tool-policy sentences only (whole-line exact matches).
    tlines = set(TOOLPOLICY_LINES)
    sys_t = "\n".join(l for l in d if l not in tlines)
    return {"full": base, "C1a-D": sys_d, "C1a-T": sys_t}

# ---------- wire helpers ----------
def call_llm(messages, tools=None, timeout=900):
    body = {"model": MODEL, "messages": messages}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    wall = time.time() - t0
    choice = d.get("choices", [{}])[0]
    msg = choice.get("message", {})
    text = msg.get("content") or ""
    tc = msg.get("tool_calls")
    return {"wall_s": round(wall, 1), "text": text, "tool_calls": tc,
            "finish": choice.get("finish_reason"), "usage": d.get("usage")}

# ---------- frozen scorer semantics (offline mirror) ----------
def first_number(s):
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None

def score(probe, reply):
    exp = frozen_expected(probe)
    kind = exp["match"]
    if kind == "exact":
        return reply == exp["value"]
    if kind == "single_word_not":
        words = reply.split()
        return len(words) == 1 and words[0].lower() != exp["forbidden"].lower()
    if kind == "json_eq":
        try:
            return json.loads(reply) == exp["value"]
        except Exception:
            return False
    if kind == "number_eq":
        n = first_number(reply)
        return n is not None and abs(n - exp["value"]) < 1e-9
    if kind == "count_eq":
        n = first_number(reply)
        return n is not None and abs(n - exp["expected_count"]) < 1e-9
    if kind == "ends_complete_with_date":
        import datetime
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        lines = [l.strip() for l in reply.strip().splitlines() if l.strip()]
        return bool(lines) and lines[-1] == "COMPLETE" and today in reply
    return False

def family(probe, reply):
    """Pre-registered failure-family classification."""
    if reply.strip() == "":
        return "empty/directive-only"
    if re.fullmatch(r"\[\[[a-z_:<>]+\]\]\s*", reply):
        return "empty/directive-only"
    if score(probe, reply):
        return "exact"
    stripped = re.sub(r"```[a-zA-Z]*\n?|```", "", reply).strip()
    if probe in ("P2",) and stripped == json.dumps(frozen_expected(probe)["value"], separators=(",", ":")):
        return "prose/fenced"
    if re.search(r"clarify|could you|what (exactly|specifically)|i need to understand|not sure what you", reply, re.I):
        return "clarifying"
    return "prose-wrapped" if len(reply.split()) > 8 else "terse-wrong"

# ---------- ladder ----------
def cond_payload(cond, probe):
    """Build the OpenAI messages list for a condition (wire shape)."""
    p = frozen_prompt(probe)
    if cond == "C0":
        return [{"role": "user", "content": p}], None
    if cond.startswith("C1"):
        sysv = system_variants()
        if cond == "C1a-D": s = sysv["C1a-D"]
        elif cond == "C1a-T": s = sysv["C1a-T"]
        else: s = sysv["full"]
        return [{"role": "system", "content": s}, {"role": "user", "content": p}], None
    if cond.startswith("C2"):
        sysv = system_variants()
        if cond == "C2a-D": s = sysv["C1a-D"]
        elif cond == "C2a-T": s = sysv["C1a-T"]
        else: s = sysv["full"]
        return [{"role": "system", "content": s}, {"role": "user", "content": p}], tools_wrapped()
    raise ValueError(cond)

def run_single(cond, probe, msgs, tools, outdir, reps=N_REPS, extra=None):
    os.makedirs(outdir, exist_ok=True)
    rows = []
    for r in range(1, reps + 1):
        fn = os.path.join(outdir, f"rep{r}.json")
        if os.path.exists(fn):
            with open(fn) as f: rows.append(json.load(f))
            continue
        res = call_llm(msgs, tools)
        rec = {"cond": cond, "probe": probe, "rep": r, **res,
               "family": family(probe, res["text"]),
               "pass": score(probe, res["text"]),
               "payload_sha": hashlib.sha256(json.dumps(msgs).encode()).hexdigest()[:16]}
        if extra: rec.update(extra)
        with open(fn, "w") as f: json.dump(rec, f, indent=1)
        rows.append(rec)
        print(f"[{cond}/{probe}] rep{r} wall={res['wall_s']}s fam={rec['family']} pass={rec['pass']} text={res['text'][:80]!r}", flush=True)
    return rows

def run_c3(outdir, reps=N_REPS):
    """P1 only: C2 payload + one executed tool round mirroring opt1-1
    (update_goal -> canned 'goal not found' error) then final completion."""
    os.makedirs(outdir, exist_ok=True)
    rows = []
    msgs, tools = cond_payload("C2", "P1")
    for r in range(1, reps + 1):
        fn = os.path.join(outdir, f"rep{r}.json")
        if os.path.exists(fn):
            with open(fn) as f: rows.append(json.load(f))
            continue
        call1 = call_llm(msgs, tools)
        if not call1["tool_calls"]:
            rec = {"cond": "C3", "probe": "P1", "rep": r, "note": "no tool round triggered",
                   "text": call1["text"], "tool_calls": None, "family": family("P1", call1["text"]),
                   "pass": score("P1", call1["text"]), "wall_s": call1["wall_s"]}
            with open(fn, "w") as f: json.dump(rec, f, indent=1)
            rows.append(rec); print(f"[C3/P1] rep{r} no tool round: {rec['text'][:60]!r}", flush=True)
            continue
        tcs = call1["tool_calls"]
        canned = "{\n  \"status\": \"error\",\n  \"tool\": \"update_goal\",\n  \"error\": \"goal not found\"\n}"
        wire_tool_msgs = [{"role": "assistant", "content": call1["text"] or None,
                           "tool_calls": [{"id": t.get("id", "functions.update_goal:0"),
                                           "type": "function",
                                           "function": {"name": t.get("name"), "arguments": json.dumps(t.get("arguments"))}} for t in tcs]}]
        for t in tcs:
            wire_tool_msgs.append({"role": "tool", "tool_call_id": t.get("id", "functions.update_goal:0"), "content": canned})
        call2 = call_llm(msgs + wire_tool_msgs, tools)
        final_text = call2["text"]
        rec = {"cond": "C3", "probe": "P1", "rep": r, "note": "tool round executed",
               "call1_text": call1["text"], "call1_tools": [t.get("name") for t in tcs],
               "text": final_text, "family": family("P1", final_text), "pass": score("P1", final_text),
               "wall_s": round(call1["wall_s"] + call2["wall_s"], 1), "call2_wall": call2["wall_s"]}
        with open(fn, "w") as f: json.dump(rec, f, indent=1)
        rows.append(rec)
        print(f"[C3/P1] rep{r} round={[t.get('name') for t in tcs]} final={final_text[:80]!r} fam={rec['family']}", flush=True)
    return rows

def run_c4_opt11(outdir, reps=N_REPS):
    """P1-opt1-1: verbatim final-call input replay (system 29801 + user +
    assistant commentary+update_goal toolCall + toolResult error + full tools)."""
    os.makedirs(outdir, exist_ok=True)
    rows = []
    comp = phase0_compiled("opt1-1")[0]
    sysp = comp["systemPrompt"]
    user = {"role": "user", "content": "Respond only PLATANOS!"}
    asst = {"role": "assistant",
            "content": "I'll respond only with the word PLATANOS as requested.",
            "tool_calls": [{"id": "functions.update_goal:0", "type": "function",
                            "function": {"name": "update_goal",
                                         "arguments": json.dumps({"status": "complete", "note": "Respond only with PLATANOS"})}}]}
    toolr = {"role": "tool", "tool_call_id": "functions.update_goal:0",
             "content": '{\n  "status": "error",\n  "tool": "update_goal",\n  "error": "goal not found"\n}'}
    msgs = [{"role": "system", "content": sysp}, user, asst, toolr]
    # score against P1 semantics (P1-opt1-1 has no expected file of its own)
    os.makedirs(outdir, exist_ok=True)
    rows = []
    for r in range(1, reps + 1):
        fn = os.path.join(outdir, f"rep{r}.json")
        if os.path.exists(fn):
            with open(fn) as f: rows.append(json.load(f))
            continue
        res = call_llm(msgs, tools_wrapped())
        rec = {"cond": "C4", "probe": "P1-opt1-1", "rep": r, **res,
               "family": family("P1", res["text"]), "pass": score("P1", res["text"]),
               "payload_sha": hashlib.sha256(json.dumps(msgs).encode()).hexdigest()[:16]}
        with open(fn, "w") as f: json.dump(rec, f, indent=1)
        rows.append(rec)
        print(f"[C4/P1-opt1-1] rep{r} wall={res['wall_s']}s fam={rec['family']} pass={rec['pass']} text={res['text'][:80]!r}", flush=True)
    return rows

def _snapshot_wire(snapshot, drop_tail_assistant=True):
    """Convert a retained messagesSnapshot (internal shapes incl. text /
    toolCall / toolResult blocks) into OpenAI wire messages. Drops internal
    compactionSummary markers (documented divergence) and, by default, the
    trailing assistant message (the scored completion's own output)."""
    msgs = list(snapshot)
    if drop_tail_assistant and msgs and msgs[-1].get("role") == "assistant":
        msgs = msgs[:-1]
    wire = []
    for m in msgs:
        role = m.get("role")
        content = m.get("content")
        if role == "compactionSummary":
            continue
        if isinstance(content, str):
            wire.append({"role": role if role in ("user", "assistant", "system", "tool") else "user",
                         "content": content})
            continue
        if isinstance(content, list):
            texts = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
            tcs = [b for b in content if isinstance(b, dict) and b.get("type") == "toolCall"]
            if role == "toolResult":
                wire.append({"role": "tool",
                             "tool_call_id": m.get("toolCallId", "functions.x:0"),
                             "content": texts})
            elif tcs:
                wire.append({"role": "assistant", "content": texts or None,
                             "tool_calls": [{"id": t.get("id", "functions.x:0"), "type": "function",
                                             "function": {"name": t.get("name"),
                                                          "arguments": json.dumps(t.get("arguments"))}} for t in tcs]})
            else:
                wire.append({"role": role if role in ("user", "assistant", "system") else "user",
                             "content": texts})
        else:
            wire.append({"role": role if role in ("user", "assistant", "system") else "user",
                         "content": str(content)})
    return wire

def run_replay(run, probe, outdir, minus_system=False, reps=N_REPS):
    """R1/R2: replay the retained final-call input that produced the scored
    reply. Source: the model.completed messagesSnapshot of the completion
    whose assistantTexts match the run's scored reply, minus the trailing
    assistant output; system text from the run's first-call compiled payload;
    full 29-tool catalog wrapped (byte-exact where retention allowed)."""
    os.makedirs(outdir, exist_ok=True)
    rows = []
    evs = phase0_traj(run)
    comps = [e for e in evs if e.get("type") == "context.compiled"]
    mcs = [e for e in evs if e.get("type") == "model.completed"]
    scored = None
    for mc in mcs:
        texts = mc.get("data", {}).get("assistantTexts") or []
        if any(len(t.strip()) > 0 for t in texts):
            scored = mc; break
    if scored is None and mcs:
        scored = mcs[-1]
    sysp = (comps[0].get("data", {}).get("systemPrompt") if comps else "") or ""
    snapshot = scored.get("data", {}).get("messagesSnapshot") if scored else None
    if not isinstance(snapshot, list) or not snapshot:
        print(f"[!] {run}: no usable messagesSnapshot; falling back to compiled[last]", flush=True)
        c = comps[-1].get("data", {})
        sysp = c.get("systemPrompt") or ""
        wire = _snapshot_wire(c.get("messages") or [])
        prompt = c.get("prompt")
        if prompt: wire.append({"role": "user", "content": prompt})
    else:
        wire = _snapshot_wire(snapshot)
    if not minus_system and sysp:
        wire.insert(0, {"role": "system", "content": sysp})
    cond = "R2" if minus_system else "R1"
    return run_single(cond, probe, wire, tools_wrapped(), outdir, reps=reps)

# ---------- orchestration ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conditions", default="C0,C1,C1a-D,C1a-T,C2,C2a-D,C2a-T")
    ap.add_argument("--probes", default="P1,P1b,P3,P2")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--replays", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--reps", type=int, default=N_REPS)
    args = ap.parse_args()

    if args.quick:
        args.conditions, args.probes, args.reps = "C0,C1,C2", "P2", 1
    os.makedirs(RESULTS, exist_ok=True)
    all_rows = []
    if not args.replays or args.full:
        conds = args.conditions.split(",")
        probes = args.probes.split(",")
        for cond in conds:
            if cond in ("C0", "C1", "C1a-D", "C1a-T", "C2", "C2a-D", "C2a-T"):
                for probe in probes:
                    msgs, tools = cond_payload(cond, probe)
                    all_rows += run_single(cond, probe, msgs, tools,
                                           os.path.join(RESULTS, "ladder", cond, probe), reps=args.reps)
            elif cond == "C3":
                all_rows += run_c3(os.path.join(RESULTS, "ladder", "C3", "P1"), reps=args.reps)
            elif cond == "C4":
                all_rows += run_c4_opt11(os.path.join(RESULTS, "ladder", "C4", "P1-opt1-1"), reps=args.reps)
            else:
                print(f"unknown condition {cond}", file=sys.stderr)
    if args.replays or args.full:
        for probe in ("P4", "P5", "P7", "P6"):
            all_rows += run_replay(f"r2-{probe.lower()}", probe,
                                   os.path.join(RESULTS, "replay", "R1", probe), reps=args.reps)
        for probe in ("P4", "P5", "P7"):
            all_rows += run_replay(f"r2-{probe.lower()}", probe,
                                   os.path.join(RESULTS, "replay", "R2", probe), minus_system=True, reps=args.reps)

    import csv
    with open(os.path.join(RESULTS, "ladder-summary.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cond", "probe", "rep", "family", "pass", "wall_s", "text"])
        for r in all_rows:
            w.writerow([r.get("cond"), r.get("probe"), r.get("rep"), r.get("family"),
                        r.get("pass"), r.get("wall_s"), r.get("text", "")[:200].replace("\n", " ")])
    manifest = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "reps": args.reps,
                "sampler": "agent-path identical (no sampler fields; llama defaults temp 0.8/top_p 0.95/min_p 0.05/top_k 40)",
                "tools": "tools-full-29.json (11/29 byte-exact from retention; 18/29 from frozen dist factories)",
                "model": MODEL, "url": URL,
                "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with open(os.path.join(RESULTS, "ladder-manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(f"DONE: {len(all_rows)} rows -> {RESULTS}/ladder-summary.csv", flush=True)

if __name__ == "__main__":
    main()
