#!/usr/bin/env python3
"""Step 9D real-path reply classifier (retained, 2026-09-04).

Classifies each per-rep reply from a Step 9D verification run
(benchmarks/results/service-step-09d-verify/<probe>/<probe>-r<rep>.reply.txt)
under the exact frozen Step 9 scorer semantics
(tools/service_step09_qualify.sh heredoc + expected/<probe>.json) plus the
pre-registered 9B/9C failure families, and prints family/PASS tables for
comparison against the frozen C1a-D baseline (P1: terse bare tokens, no
directive/empty family; P2: exact json_eq).

Usage: service_step09d_classify.py <outdir> <suite>
  outdir: run directory (e.g. benchmarks/results/service-step-09d-verify)
  suite:  frozen suite root (e.g. benchmarks/results/service-step-09)
"""
import csv
import glob
import json
import os
import re
import sys
from collections import Counter

OUT = sys.argv[1]
SUITE = sys.argv[2]
PROBES = ["P1", "P2"]

EXP = {}
for p in PROBES:
    with open(os.path.join(SUITE, "expected", p + ".json")) as f:
        EXP[p] = json.load(f)


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


rows = []
for p in PROBES:
    for rp in sorted(glob.glob(os.path.join(OUT, p, p + "-r*.reply.txt"))):
        label = os.path.basename(rp).replace(".reply.txt", "")
        cj = os.path.join(OUT, p, label + ".client.json")
        rc = None
        wall = None
        if os.path.exists(cj):
            try:
                with open(cj) as f:
                    c = json.load(f)
                rc = c.get("rc")
                wall = c.get("wall_s")
            except Exception:
                pass
        with open(rp, encoding="utf-8") as f:
            reply = (f.read() or "").strip()
        fr = frozen(p, reply)
        if rc != 0:
            row = {"probe": p, "rep": label, "rc": rc, "wall_s": wall,
                   "family": ("client-error" if not reply else family(p, reply, fr)),
                   "instruction": "INVALID", "format": "INVALID", "result": "INVALID",
                   "note": "client rc=%s" % rc, "leaks": count_leaks(reply),
                   "text": reply.replace("\n", " ")[:220]}
        else:
            fam = family(p, reply, fr)
            row = {"probe": p, "rep": label, "rc": rc, "wall_s": wall,
                   "family": fam,
                   "instruction": fr["instruction"], "format": fr["format"],
                   "result": fr["result"],
                   "note": fr["note"], "leaks": count_leaks(reply),
                   "text": reply.replace("\n", " ")[:220]}
        rows.append(row)

with open(os.path.join(OUT, "classified.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["probe", "rep", "rc", "wall_s", "family", "instruction",
                "format", "result", "leaks", "note", "text"])
    for r in rows:
        w.writerow([r["probe"], r["rep"], r["rc"], r["wall_s"], r["family"],
                    r["instruction"], r["format"], r["result"], r["leaks"],
                    r["note"], r["text"]])

lines = []
lines.append("===== Step 9D real-path per-rep classification =====")
for r in rows:
    lines.append(" ".join([
        r["probe"], r["rep"], "rc=" + str(r["rc"]), "wall=" + str(r["wall_s"]),
        "family=" + r["family"], "instr=" + r["instruction"],
        "fmt=" + r["format"], "res=" + r["result"], "leaks=" + str(r["leaks"]),
        "| " + r["text"],
    ]))
for p in PROBES:
    sub = [r for r in rows if r["probe"] == p]
    fam = Counter(r["family"] for r in sub)
    npass = sum(1 for r in sub if r["instruction"] == "PASS")
    nleak = sum(1 for r in sub if r["leaks"] > 0)
    passline = str(npass) + "/" + str(len(sub))
    lines.append("----- " + p + "  PASS=" + passline +
                 "  leaky=" + str(nleak) + "  families=" + str(dict(fam)))
txt = "\n".join(lines)
print(txt)
with open(os.path.join(OUT, "summary.txt"), "w") as f:
    f.write(txt + "\n")
print("classified.csv -> %s" % os.path.join(OUT, "classified.csv"))
