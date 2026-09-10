#!/usr/bin/env python3
"""Decisive probe: does an exact-prompt request reuse a RESTORED slot state?"""
import json, os, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:18080"
EVID = "/Users/pmains/Code/openclaw/kimi/benchmarks/results/service-step-14/14di-smoke"
text = open(os.path.join(EVID, "corpus.txt")).read()
lines = text.splitlines(keepends=True)
prompt = "".join(lines[:70])  # ~3000 tokens


def req(method, path, body=None, timeout=1800):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data,
                               headers={"Content-Type": "application/json"} if data else {},
                               method=method)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read()), time.monotonic() - t0, resp.status
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read()), time.monotonic() - t0, e.code
        except Exception:
            return {"_raw": "<nonjson>"}, time.monotonic() - t0, e.code


def tj(d):
    t = d.get("timings", {})
    return {"cache_n": t.get("cache_n"), "prompt_n": t.get("prompt_n"),
            "prompt_ms": round(t.get("prompt_ms", 0), 1)}


out = {}
d, w, h = req("POST", "/completions", {"prompt": prompt, "n_predict": 1, "temperature": 0, "cache_prompt": True})
out["1_prefill"] = {**tj(d), "wall_s": round(w, 2), "http": h}
print("prefill:", out["1_prefill"], flush=True)

d, w, h = req("POST", "/slots/0?action=save", {"filename": "probe.bin"})
out["2_save"] = {"n_saved": d.get("n_saved"), "n_written": d.get("n_written"), "http": h, "wall_s": round(w, 3)}
print("save:", out["2_save"], flush=True)

d, w, h = req("POST", "/slots/0?action=erase", {})
print("erase http:", h, flush=True)

d, w, h = req("POST", "/slots/0?action=restore", {"filename": "probe.bin"})
out["3_restore"] = {"n_restored": d.get("n_restored"), "http": h, "wall_s": round(w, 3)}
print("restore:", out["3_restore"], flush=True)

# A) exact same prompt
d, w, h = req("POST", "/completions", {"prompt": prompt, "n_predict": 1, "temperature": 0, "cache_prompt": True})
out["4_exact_prompt_after_restore"] = {**tj(d), "wall_s": round(w, 2), "http": h}
print("exact after restore:", out["4_exact_prompt_after_restore"], flush=True)

# B) prompt + suffix
d, w, h = req("POST", "/completions", {"prompt": prompt + "\n\nPhrase:", "n_predict": 4, "temperature": 0, "cache_prompt": True})
out["5_suffix_after_restore"] = {**tj(d), "wall_s": round(w, 2), "http": h}
print("suffix after restore:", out["5_suffix_after_restore"], flush=True)

json.dump(out, open(os.path.join(EVID, "probe-restore-reuse.json"), "w"), indent=1)
print("WROTE probe-restore-reuse.json")
