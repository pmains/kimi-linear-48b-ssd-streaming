#!/usr/bin/env python3
"""Reclassify all Step 9B ladder/replay rows against the exact frozen scorer
semantics (tools/service_step09_qualify.sh heredoc): result_correct vs
format_followed vs instruction_followed, plus pre-registered failure family.
Reads raw per-rep JSON (already saved); writes a classified CSV + tables."""
import json, glob, re, csv, datetime, os
from collections import Counter

SUITE = 'benchmarks/results/service-step-09'
EXP = {}
for p in ['P1', 'P1b', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7']:
    EXP[p] = json.load(open(SUITE + '/expected/' + p + '.json'))

def get_number(s):
    m = re.search(r"-?\d+(?:\.\d+)?", s or "")
    return float(m.group()) if m else None

def frozen(probe, reply):
    exp = EXP[probe]
    m = exp["match"]
    out = {"instruction": "FAIL", "format": "FAIL", "result": "FAIL", "note": ""}
    if m == "exact":
        ok = reply == exp["value"]
        out = {"instruction": "PASS" if ok else "FAIL",
               "format": "PASS" if ok else "FAIL",
               "result": "PASS" if ok else "FAIL", "note": ""}
    elif m == "single_word_not":
        toks = reply.split()
        word = (toks[0].strip(". ,;:!?\"'()") if toks else "")
        ok = len(toks) == 1 and word.lower() != exp["forbidden"].lower()
        out = {"instruction": "PASS" if ok else "FAIL",
               "format": "PASS" if len(toks) == 1 else "FAIL",
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
    elif m == "number_eq":
        n = get_number(reply)
        ok = n is not None and abs(n - exp["value"]) < 1e-9
        fmt = ok and re.fullmatch(r"-?\d+(?:\.\d+)?", reply)
        out = {"instruction": "PASS" if ok else "FAIL",
               "format": "PASS" if fmt else ("FAIL" if reply else "N/A"),
               "result": "PASS" if ok else "FAIL", "note": ""}
    elif m == "count_eq":
        n = get_number(reply)
        ok = n is not None and abs(n - exp["expected_count"]) < 1e-9
        fmt = ok and re.fullmatch(r"-?\d+", reply)
        out = {"instruction": "PASS" if ok else "FAIL",
               "format": "PASS" if fmt else ("FAIL" if reply else "N/A"),
               "result": "PASS" if ok else "FAIL", "note": ""}
    elif m == "file_roundtrip":
        fp = os.path.join(SUITE, exp["path"])
        content_ok = False
        if os.path.exists(fp):
            data = open(fp, "rb").read().decode("utf-8", "replace")
            content_ok = data.rstrip("\n") == exp["content"] or data == exp["content"]
        in_reply = exp["content"] in reply
        ok = content_ok and in_reply
        out = {"instruction": "PASS" if ok else "FAIL", "format": "N/A",
               "result": "PASS" if ok else "FAIL",
               "note": "file_content_ok=%s reply_contains=%s" % (content_ok, in_reply)}
    elif m == "ends_complete_with_date":
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        lines = [ln for ln in reply.splitlines() if ln.strip()]
        ends = bool(lines) and lines[-1].strip() == "COMPLETE"
        has_date = today in reply
        ok = ends and has_date
        out = {"instruction": "PASS" if ok else "FAIL",
               "format": "PASS" if ends else "FAIL",
               "result": "PASS" if ok else "FAIL",
               "note": "ends_COMPLETE=%s has_utc_date=%s" % (ends, has_date)}
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

rows = []
for f in sorted(glob.glob('benchmarks/results/service-step-09b-verify/ladder/*/*/rep*.json')) + \
         sorted(glob.glob('benchmarks/results/service-step-09b-verify/replay/*/*/rep*.json')):
    d = json.load(open(f))
    probe = d['probe']
    score_probe = 'P1' if probe == 'P1-opt1-1' else probe
    reply = (d.get('text') or '').strip()
    fr = frozen(score_probe, reply)
    d['_instruction'] = fr['instruction']
    d['_format'] = fr['format']
    d['_result'] = fr['result']
    d['_family'] = family(score_probe, reply, fr)
    d['_note'] = fr['note']
    rows.append(d)

with open('benchmarks/results/service-step-09b-verify/classified.csv', 'w', newline='') as fh:
    w = csv.writer(fh)
    w.writerow(['cond', 'probe', 'rep', 'family', 'instruction', 'format', 'result', 'note', 'wall_s', 'text'])
    for r in rows:
        w.writerow([r['cond'], r['probe'], r['rep'], r['_family'], r['_instruction'],
                    r['_format'], r['_result'], r['_note'], r.get('wall_s'),
                    (r.get('text') or '').replace('\n', ' ')[:220]])
print("classified", len(rows), "rows -> classified.csv")

order = ['C0', 'C1', 'C1a-D', 'C1a-T', 'C2', 'C2a-D', 'C2a-T', 'C3', 'C4']
out = []
for probe in ['P1', 'P1b', 'P2', 'P3']:
    out.append("----- " + probe)
    for cond in order:
        sub = [r for r in rows if r['cond'] == cond and r['probe'] == probe]
        if not sub:
            continue
        fam = Counter(r['_family'] for r in sub)
        npass = sum(1 for r in sub if r['_instruction'] == 'PASS')
        out.append("  %-7s PASS=%s  families=%s" % (cond, npass, dict(fam)))
out.append("----- Class II replays")
for cond in ['R1', 'R2']:
    for probe in ['P4', 'P5', 'P6', 'P7']:
        sub = [r for r in rows if r['cond'] == cond and r['probe'] == probe]
        if not sub:
            continue
        fam = Counter(r['_family'] for r in sub)
        npass = sum(1 for r in sub if r['_instruction'] == 'PASS')
        out.append("  %s/%-3s PASS=%s  families=%s" % (cond, probe, npass, dict(fam)))
txt = "\n".join(out)
print(txt)
open('benchmarks/results/service-step-09b-verify/summary-tables.txt', 'w').write(txt + "\n")
