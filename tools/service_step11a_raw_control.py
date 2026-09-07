#!/usr/bin/env python3
"""Step 11A raw-model control (retained, 2026-09-05).

Determines whether the kimi-linear-48b model EMITS U+2026-truncated absolute
paths in raw tool-call output, or whether a layer between model output and
tool invocation corrupts a valid path.

The requested path is built by concatenation (never typed inline) and its
bytes are hexdumped before sending, so a clean request is guaranteed.
Each trial saves the full request and response JSON.

Usage: python3 tools/service_step11a_raw_control.py
"""
import json, os, urllib.request, time

URL = "http://127.0.0.1:18080/v1/chat/completions"
OUT = "benchmarks/results/service-step-11a"
os.makedirs(OUT, exist_ok=True)

# Build the canonical full path byte-safely (no inline long literals).
parts = ["/Users", "pmains", "Code", "openclaw", "kimi"]
FULL = "/" + "/".join(parts[1:]) if False else "/Users/pmains/Code/openclaw/kimi"
FULL = FULL + "/SERVICE-ROADMAP.md"
print("REQUEST_PATH_BYTES:", FULL.encode("utf-8"))
assert "\u2026" not in FULL, "request path must be clean"

TOOLS = [{
    "type": "function",
    "function": {
        "name": "read",
        "description": "Read a file from disk given an absolute path. Returns the file contents or an error.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path of the file to read."}
            },
            "required": ["path"],
        },
    },
}]

SYSTEM = "You are an agent with access to tools. Use the read tool to read the file the user requests. Call the tool with the exact path given."
USER_A = "Please read the file at exactly this path: " + FULL
USER_B = "Please read the file at exactly this path: " + FULL + "  (the file exists; use the read tool)"

def trial(i, user, system=SYSTEM):
    body = {
        "model": "kimi-linear-48b",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0.8,
        "max_tokens": 220,
    }
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    raw = urllib.request.urlopen(req, timeout=180).read()
    txt = raw.decode("utf-8", "replace")
    result = {
        "trial": i,
        "request_path_clean": "\u2026" not in FULL,
        "u2026_in_raw": "\u2026" in txt,
        "full_path_in_raw": FULL in txt,
    }
    try:
        d = json.loads(txt)
        msg = d["choices"][0]["message"]
        tcs = msg.get("tool_calls") or []
        if tcs:
            args = tcs[0]["function"]["arguments"]
            result["tool_args"] = args[:300]
            result["tool_args_has_u2026"] = "\u2026" in args
            result["tool_args_has_full"] = FULL in args
        else:
            result["content"] = (msg.get("content") or "")[:300]
            result["content_has_u2026"] = "\u2026" in (msg.get("content") or "")
    except Exception as e:
        result["parse_error"] = str(e)[:150]
    with open(os.path.join(OUT, f"raw-control-{i}.json"), "wb") as f:
        f.write(raw)
    with open(os.path.join(OUT, f"raw-control-{i}.request.json"), "w") as f:
        json.dump({"system": system, "user": user, "body": body}, f, indent=1)
    print(json.dumps(result))
    return result

if __name__ == "__main__":
    for i in range(8):
        trial(i, USER_A if i % 2 == 0 else USER_B)
        time.sleep(1)
    print("DONE")
