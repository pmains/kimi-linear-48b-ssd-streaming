#!/usr/bin/env python3
"""
15B diagnostic (Option B): restore the retained ~48K Stage 14D(i) snapshot and
submit the same small append-only continuation, to observe the reuse-branch
values emitted by the temporary [15B-DIAG] instrumentation.

Observation only: does NOT test a reconstructed checkpoint, does not begin 15C.
"""
import json, os, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:18080"
EVID = "/Users/pmains/Code/openclaw/kimi/benchmarks/results/service-step-15/15b"
CORPUS_DIR = "/Users/pmains/Code/openclaw/kimi/benchmarks/results/service-step-14/14di"
SLOT_FILE = "14di-48k.bin"
SUFFIX = "\n\n### Provenance check\nThe verification phrase for this session is SUFFIX-OK-7412. Reply with only that phrase.\n\nPhrase:"


def req(method, path, body=None, timeout=3600):
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
    except Exception as e:
        return {"_error": str(e)}, time.monotonic() - t0, None


def slots():
    s, _, _ = req("GET", "/slots", timeout=30)
    if isinstance(s, list) and s:
        return {k: s[0].get(k) for k in ("id", "is_processing", "n_prompt_tokens",
                                         "n_prompt_tokens_cache", "n_prompt_tokens_processed", "id_task")}
    return s


def timings(d):
    t = d.get("timings", {}) or {}
    c = t.get("cache_n", 0) or 0
    p = t.get("prompt_n", 0) or 0
    return {"assembled": c + p, "cache_n": c, "prompt_n": p,
            "reuse": round(c / (c + p), 4) if (c + p) else None,
            "prompt_ms": round(t.get("prompt_ms", 0), 1),
            "predicted_n": t.get("predicted_n"),
            "content": (d.get("content") or "")[:120]}


# rebuild the identical prompt used by the 14D(i) run
meta = json.load(open(os.path.join(CORPUS_DIR, "corpus-meta.json")))
text = open(os.path.join(CORPUS_DIR, "corpus.txt")).read()
need = int(48000 / meta["tokens_per_line"]) + 1
prompt = "".join(text.splitlines(keepends=True)[:need])

out = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
out["slot_pre"] = slots()
print("slot_pre:", out["slot_pre"], flush=True)

d, w, h = req("POST", "/slots/0?action=restore", {"filename": SLOT_FILE})
out["restore"] = {"http": h, "wall_s": round(w, 3), "n_restored": d.get("n_restored"),
                  "err": d.get("error")}
print("restore:", out["restore"], flush=True)
time.sleep(1)
out["slot_after_restore"] = slots()
print("slot_after_restore:", out["slot_after_restore"], flush=True)

# the same small append-only continuation as 14D(i)
d, w, h = req("POST", "/completions", {"prompt": prompt + SUFFIX, "n_predict": 16,
                                       "temperature": 0, "cache_prompt": True})
out["continuation"] = {**timings(d), "http": h, "wall_s": round(w, 2)}
print("continuation:", out["continuation"], flush=True)

time.sleep(1)
out["slot_after"] = slots()
out["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

json.dump(out, open(os.path.join(EVID, "diagnostic-request.json"), "w"), indent=1)
print("WROTE diagnostic-request.json")
