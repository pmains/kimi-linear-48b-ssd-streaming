#!/usr/bin/env python3
"""Step 13E - calibrated long-context message builder (retained, 2026-09-07).

Builds the USER MESSAGE for a real-path agent turn so the ASSEMBLED OpenClaw
prompt (system + project context + tools + message) exceeds a token boundary,
with a deterministic needle whose position inside the message is MEASURED
against the live llama-server tokenizer (never estimated from char depth).

Usage:
  service_step13e_corpus.py <port> <needle> <out_msg_file> <out_cal_json> \
      <target_message_tokens> <needle_depth>

Needle depth: fraction of the FILLER char stream at which the needle is
inserted (0.96 = 96%). The final instruction is appended AFTER the needle, so
the needle never sits in the trailing instruction region.
"""
import json
import sys
import time
import urllib.request

port = int(sys.argv[1])
needle = sys.argv[2]
out_msg = sys.argv[3]
out_cal = sys.argv[4]
target_m = int(sys.argv[5])
depth = float(sys.argv[6])
base = f"http://127.0.0.1:{port}"


def tok(content, add_special=False, timeout=600):
    body = json.dumps({"content": content, "add_special": add_special}).encode()
    req = urllib.request.Request(f"{base}/tokenize", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r).get("tokens", [])


def dtok(ids, timeout=600):
    body = json.dumps({"tokens": ids}).encode()
    req = urllib.request.Request(f"{base}/detokenize", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r).get("content", "")


unit = ("The quick brown fox surveys the quiet valley under a wide sky. "
        "Rivers wind between granite ridges while hawks circle slowly. "
        "Each stone holds a story older than any written word. "
        "Morning light crosses the meadow and the air carries the scent of pine. ")
instr = ("\n\nCarefully find the hidden marker string in the text above. "
         "It has the form NEEDLE- followed by four digits. "
         "Do not use any tools. "
         "Reply with ONLY that marker string and nothing else.")

tpu = len(tok(unit))
units = max(1, int((target_m - len(tok(instr)) - 8) / tpu))


def build_content(u):
    filler = unit * u
    idx = int(len(filler) * depth)
    return filler[:idx] + f" {needle} " + filler[idx:] + instr


content = build_content(units)
m = len(tok(content))
if abs(m - target_m) > max(200, target_m * 0.01):
    units = max(1, int(units * target_m / max(1, m)))
    content = build_content(units)
    m = len(tok(content))

ids = tok(content)
cal = {"unit_tokens": tpu, "units": units, "message_tokens": m,
       "target_message_tokens": target_m, "char_depth": depth, "needle": needle,
       "needle_method": "not-measured", "needle_message_token_pos": None}
print(f"corpus: unit_tokens={tpu} units={units} message_tokens={m} target={target_m}")

if len(ids) > 0:
    try:
        lo, hi = 0, len(ids)
        if needle in dtok(ids[:hi]):
            while lo < hi:
                mid = (lo + hi) // 2
                if needle in dtok(ids[:mid]):
                    hi = mid
                else:
                    lo = mid + 1
            cal["needle_message_token_pos"] = lo
            cal["needle_method"] = "detokenize-binary-search"
            print(f"corpus: needle starts at message token {lo} of {len(ids)}")
        else:
            print("corpus warn: needle not found in full detokenize output")
    except Exception as e:
        print("corpus warn: needle measurement failed:", e)

with open(out_msg, "w", encoding="utf-8") as f:
    f.write(content)
json.dump(cal, open(out_cal, "w"), indent=2)
print(f"corpus: wrote {out_msg} chars={len(content)} cal={out_cal}")
