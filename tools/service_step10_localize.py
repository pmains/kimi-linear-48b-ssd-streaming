#!/usr/bin/env python3
"""Step 10 - offline sampler/env localization driver (retained, 2026-09-04).

Runs the pre-registered Step 10 offline legs against the SAME live llama-server
endpoint the agent path uses (127.0.0.1:18080, 64K, MXFP4, --parallel 1):

  Leg 1 (sampler axis): payloads {C0, E2} x probes {P1, P2} x temperature
        {0.0 (greedy floor), 0.8 (llama default), 1.6}, n = REPS.
        temp-0.8 cells send NO sampler fields (agent-identical); 0.0 and 1.6
        cells send only "temperature" (request-scoped; the production agent
        path never sends sampler fields, so production behavior is untouched).
  Leg 2 (environment axis, agent-identical default sampler): payloads
        {C0, C1, C1a-D, E2, E2T} x probes {P1, P2}, n = REPS.

Payload conditions (wire shape, OpenAI chat format):
  C0    bare user only
  C1    full retained agent system text (phase0 system-prompt-r2-p1.txt)
  C1a-D full minus the ENTIRE "## Assistant Output Directives" section
        (the 9B/9C reduced baseline; byte-identity vs retained 9B payloads
        is asserted at startup)
  E2    full minus the TWO 9D-gated reply-directive lines only (the true
        current headless probe prompt; hasDeliverySurface=false semantics)
  E2T   E2 + the 29-tool wrapped catalog (tools-full-29.json)

Scoring/families use the exact frozen Step 9 semantics and the Step 9D
real-path classifier taxonomy (identical code semantics), so offline cells are
directly comparable with the real-path classified.csv rows.

No %-format specifiers / f-strings in this file (transit-corruption watch).

Usage:
  python3 tools/service_step10_localize.py [--reps 10] [--smoke]
  python3 tools/service_step10_localize.py --reps 10   # full pre-registered run
"""
import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.request
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(BASE, "benchmarks/results/service-step-10", "offline")
PHASE0 = os.path.join(BASE, "benchmarks/results/service-step-09b-verify", "phase0")
SUITE = os.path.join(BASE, "benchmarks/results/service-step-09")
URL = "http://127.0.0.1:18080/v1/chat/completions"
MODEL = "kimi-linear-48b"

GATED_LINES = [
    "- Native reply starts with `[[reply_to_current]]`; explicit id only: `[[reply_to:<id>]]`.",
    "- Directives stripped before render; channel config controls delivery.",
]
DIRECTIVES_HDR = "## Assistant Output Directives"

PROBES = ["P1", "P2"]
ENV_CONDS = ["C0", "C1", "C1a-D", "E2", "E2T"]          # leg 2 (default sampler)
TEMP_CONDS = ["C0", "E2"]                                # leg 1 sweep payloads
TEMPS = [0.0, 1.6]                                       # beyond default 0.8


# ---------------- frozen suite reads ----------------
def frozen_prompt(probe):
    with open(os.path.join(SUITE, "prompts", probe + ".md"), "rb") as f:
        return f.read().decode("utf-8")


def frozen_expected(probe):
    with open(os.path.join(SUITE, "expected", probe + ".json")) as f:
        return json.load(f)


def load_sys_text():
    with open(os.path.join(PHASE0, "system-prompt-r2-p1.txt")) as f:
        return f.read()


def tools_wrapped():
    with open(os.path.join(PHASE0, "tools-full-29.json")) as f:
        flat = json.load(f)
    return [{"type": "function", "function": t} for t in flat]


# ---------------- system variants ----------------
def sys_variants():
    base = load_sys_text()
    lines = base.split("\n")
    # E2: drop the two 9D-gated lines only (exact whole-line matches).
    nfound = 0
    e2_lines = []
    for ln in lines:
        if ln in GATED_LINES:
            nfound += 1
            continue
        e2_lines.append(ln)
    if nfound != 2:
        raise RuntimeError("expected 2 gated lines in retained system text, found " + str(nfound))
    e2 = "\n".join(e2_lines)
    # C1a-D: remove the whole directives section (9B algorithm: header plus
    # following "- " bullets and blank lines up to the next non-blank line).
    keep = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == DIRECTIVES_HDR:
            i += 1
            while i < len(lines) and (lines[i].startswith("- ") or lines[i].strip() == ""):
                i += 1
            continue
        keep.append(lines[i])
        i += 1
    c1a_d = "\n".join(keep)
    return {"full": base, "E2": e2, "C1a-D": c1a_d}


# ---------------- payload construction ----------------
def cond_messages(cond, probe, variants, tools29):
    p = frozen_prompt(probe)
    if cond == "C0":
        return [{"role": "user", "content": p}], None
    if cond == "C1":
        s = variants["full"]
    elif cond == "C1a-D":
        s = variants["C1a-D"]
    elif cond == "E2":
        s = variants["E2"]
    elif cond == "E2T":
        s = variants["E2"]
        return [{"role": "system", "content": s}, {"role": "user", "content": p}], tools29
    else:
        raise ValueError("unknown cond " + str(cond))
    return [{"role": "system", "content": s}, {"role": "user", "content": p}], None


# ---------------- wire call ----------------
def call_llm(msgs, tools, temperature=None, timeout=900):
    body = {"model": MODEL, "messages": msgs}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if temperature is not None:
        body["temperature"] = temperature
        if temperature == 0.0:
            body["seed"] = 42  # pre-registered design: fixed seed for the deterministic greedy floor
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


# ---------------- frozen scorer + 9D family semantics (copied, no edits) ---
EXP = {}
for _p in PROBES:
    with open(os.path.join(SUITE, "expected", _p + ".json")) as _f:
        EXP[_p] = json.load(_f)


def frozen(probe, reply):
    exp = EXP[probe]
    m = exp["match"]
    out = {"instruction": "FAIL", "format": "FAIL", "result": "FAIL", "note": ""}
    if m == "exact":
        ok = reply == exp["value"]
        out = {"instruction": "PASS" if ok else "FAIL",
               "format": "PASS" if ok else "FAIL",
               "result": "PASS" if ok else "FAIL", "note": ""}
    elif m == "json_eq":
        try:
            parsed = json.loads(reply)
            ok = parsed == exp["value"]
        except Exception:
            ok = False
        out = {"instruction": "PASS" if ok else "FAIL",
               "format": "PASS" if ok else "FAIL",
               "result": "PASS" if ok else "FAIL", "note": ""}
    return out


def family(probe, reply, fr):
    if reply.strip() == "":
        return "empty/directive-only"
    if re.fullmatch(r"\[\[[a-z_:<>]+\]\]\s*", reply):
        return "empty/directive-only"
    if fr["instruction"] == "PASS" and fr["format"] == "PASS":
        return "exact"
    if re.search(r"clarify|could you|what (exactly|specifically)|i need to understand|not sure what you", reply, re.I):
        return "clarifying"
    if fr["result"] == "PASS" and fr["format"] != "PASS":
        return "prose-content-ok"
    if re.search(r"\[\[[a-z_:<>]+\]\]", reply):
        return "directive-prefixed"
    if len(reply.split()) > 8:
        return "prose-wrapped"
    return "terse-wrong"


def count_leaks(text):
    n = 0
    for pat in (r"\[\[reply", r"\[\[audio", r"MEDIA:", r"\[\[embed", r"\[\[reply_to"):
        n += len(re.findall(pat, text))
    return n


# ---------------- cell runner ----------------
def temp_str(temperature):
    if temperature is None:
        return "default"
    return str(temperature)


def run_cell(cond, probe, temperature, reps, variants, tools29, outdir):
    keydir = os.path.join(outdir, cond + "__" + probe + "__t" + temp_str(temperature))
    os.makedirs(keydir, exist_ok=True)
    rows = []
    msgs, tools = cond_messages(cond, probe, variants, tools29)
    payload_sha = hashlib.sha256(json.dumps(msgs).encode()).hexdigest()[:16]
    for r in range(1, reps + 1):
        fn = os.path.join(keydir, "rep" + str(r) + ".json")
        if os.path.exists(fn):
            with open(fn) as f:
                rows.append(json.load(f))
            continue
        res = call_llm(msgs, tools, temperature=temperature)
        fr = frozen(probe, res["text"])
        fam = family(probe, res["text"], fr)
        rec = {"cond": cond, "probe": probe, "rep": r, "temp": temperature,
               "temp_key": temp_str(temperature), "text": res["text"],
               "family": fam, "instruction": fr["instruction"],
               "format": fr["format"], "result": fr["result"],
               "leaks": count_leaks(res["text"]),
               "wall_s": res["wall_s"], "finish": res["finish"],
               "tool_calls": res["tool_calls"], "usage": res["usage"],
               "payload_sha": payload_sha,
               "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        with open(fn, "w") as f:
            json.dump(rec, f, indent=1)
        rows.append(rec)
        line = ("[" + cond + "/" + probe + "/t" + temp_str(temperature) + "] rep "
                + str(r) + " wall=" + str(res["wall_s"]) + "s fam=" + fam
                + " pass=" + rec["instruction"] + " leaks=" + str(rec["leaks"])
                + " text=" + repr(res["text"][:100]))
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    return rows


def wilson_ci(k, n):
    if n == 0:
        return (float("nan"), float("nan"))
    z = 1.959963985
    p = k / float(n)
    denom = 1.0 + z * z / float(n)
    centre = (p + z * z / (2.0 * float(n))) / denom
    half = z * math.sqrt(p * (1.0 - p) / float(n) + z * z / (4.0 * float(n) * float(n))) / denom
    return (centre - half, centre + half)


# ---------------- preflight: byte-identity vs retained 9B C1a-D -------------
def preflight(variants):
    ok = True
    msgs, _ = cond_messages("C1a-D", "P1", variants, None)
    sha = hashlib.sha256(json.dumps(msgs).encode()).hexdigest()[:16]
    ref = os.path.join(BASE, "benchmarks/results/service-step-09b-verify",
                       "ladder", "C1a-D", "P1", "rep1.json")
    if os.path.exists(ref):
        with open(ref) as f:
            rec = json.load(f)
        if rec.get("payload_sha") == sha:
            sys.stdout.write("preflight OK: C1a-D/P1 payload byte-identical to frozen 9B (sha " + sha + ")\n")
        else:
            ok = False
            sys.stdout.write("preflight FAIL: C1a-D/P1 sha " + sha
                             + " != 9B retained " + str(rec.get("payload_sha")) + "\n")
    else:
        sys.stdout.write("preflight WARN: 9B C1a-D/P1 rep1.json not found; identity check skipped\n")
    n = load_sys_text().count("[[reply_to_current]]")
    sys.stdout.write("preflight: gated-line marker count in retained full system text = " + str(n) + "\n")
    return ok


# ---------------- orchestration ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()

    os.makedirs(RESULTS, exist_ok=True)
    variants = sys_variants()
    tools29 = tools_wrapped()
    if not preflight(variants):
        sys.stdout.write("ABORT: preflight failed\n")
        sys.exit(3)

    if args.smoke:
        # validation subset: 1 rep per representative cell
        cells = [("C0", "P1", 0.0), ("E2", "P1", 0.0), ("E2", "P2", None),
                 ("C1a-D", "P1", None), ("E2T", "P2", None), ("C1", "P2", None)]
        reps = 1
        planned = ("smoke cells: " + str([c[0] + "/" + c[1] + "/" + temp_str(c[2]) for c in cells]))
    else:
        reps = args.reps
        cells = []
        for probe in PROBES:
            for cond in ENV_CONDS:
                cells.append((cond, probe, None))       # leg 2: default sampler
            for cond in TEMP_CONDS:
                for t in TEMPS:
                    cells.append((cond, probe, t))      # leg 1: temp sweep
        planned = ("full run: " + str(len(cells)) + " cells x " + str(reps) + " reps")

    sys.stdout.write("Step 10 offline: " + planned + "\n")
    sys.stdout.flush()

    manifest = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "reps": reps, "smoke": args.smoke,
                "model": MODEL, "url": URL,
                "sampler_note": ("default cells send no sampler fields (agent-identical; llama defaults "
                                 "temp 0.8/top_p 0.95/min_p 0.05/top_k 40); temp cells send only temperature"),
                "env_conds": ENV_CONDS, "temp_conds": TEMP_CONDS,
                "probes": PROBES,
                "sys_text_source": "phase0/system-prompt-r2-p1.txt (9A-era verbatim = current full text; 9D changed no text, only the gate)"}
    with open(os.path.join(RESULTS, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)

    all_rows = []
    for (cond, probe, temperature) in cells:
        all_rows += run_cell(cond, probe, temperature, reps, variants, tools29, RESULTS)

    # ---- summary csv + table ----
    csv_path = os.path.join(RESULTS, "offline-classified.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cond", "probe", "temp", "rep", "family", "instruction",
                    "format", "result", "leaks", "wall_s", "payload_sha", "text"])
        for rec in all_rows:
            w.writerow([rec["cond"], rec["probe"], rec["temp_key"], rec["rep"],
                        rec["family"], rec["instruction"], rec["format"],
                        rec["result"], rec["leaks"], rec["wall_s"],
                        rec["payload_sha"], rec["text"][:220].replace("\n", " ")])
    sys.stdout.write("rows: " + str(len(all_rows)) + " -> " + csv_path + "\n")

    # group keys: (cond, probe, temp_key)
    groups = {}
    for rec in all_rows:
        k = (rec["cond"], rec["probe"], rec["temp_key"])
        groups.setdefault(k, []).append(rec)
    lines = []
    lines.append("===== Step 10 offline per-cell summary =====")
    hdr = "cond     probe temp     PASS    exact_rate(CI)     families"
    lines.append(hdr)
    for k in sorted(groups.keys()):
        cond, probe, tk = k
        sub = groups[k]
        n = len(sub)
        npass = sum(1 for x in sub if x["instruction"] == "PASS")
        fam = Counter(x["family"] for x in sub)
        lo, hi = wilson_ci(npass, n)
        rate = str(npass) + "/" + str(n)
        ci = "[" + str(round(lo, 3)) + "," + str(round(hi, 3)) + "]"
        row = cond.ljust(8) + " " + probe + " " + tk.ljust(7) + " " + rate.ljust(6) + " " + ci.ljust(20) + " " + str(dict(fam))
        lines.append(row)
        sys.stdout.write(row + "\n")
    summary_path = os.path.join(RESULTS, "offline-summary.txt")
    with open(summary_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    manifest["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest["n_rows"] = len(all_rows)
    with open(os.path.join(RESULTS, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    sys.stdout.write("DONE: " + str(len(all_rows)) + " rows -> " + RESULTS + "\n")


if __name__ == "__main__":
    main()
