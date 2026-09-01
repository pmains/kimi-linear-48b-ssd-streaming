#!/usr/bin/env python3
"""Phase 11 E5 — client-side timed runner for `openclaw infer model run`.

Spawns the OpenClaw CLI one-shot model turn, timestamps every stdout line as
it arrives, and reports:
  - total wall time (client-visible turn duration)
  - TTFT (time from spawn to first line containing the response body, i.e.
    the first non-header content line after the CLI's "outputs: 1" header)
  - exit code
Also dumps the timestamped stdout to <outdir>/<label>.timed.out and stderr to
<outdir>/<label>.err so arrival timing is reproducible.

Usage: phase11_e5_ttft.py <outdir> <label> <promptfile> [--json]
"""
import json
import os
import subprocess
import sys
import time

OUTDIR, LABEL, PROMPT_FILE = sys.argv[1], sys.argv[2], sys.argv[3]
USE_JSON = "--json" in sys.argv[4:]

with open(PROMPT_FILE, "r", encoding="utf-8") as f:
    prompt = f.read()

cmd = [
    "openclaw", "infer", "model", "run",
    "--model", "kimi-local/kimi-linear-48b",
    "--prompt", prompt,
]
if USE_JSON:
    cmd.append("--json")

t0 = time.monotonic()
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

timed_lines = []          # (monotonic seconds since t0, line)
first_content_at = None   # seconds since t0
seen_outputs_header = False

assert proc.stdout is not None
for line in proc.stdout:
    now = time.monotonic() - t0
    timed_lines.append((now, line.rstrip("\n")))
    # CLI prints transport diagnostics ([...]) then a header block
    # "model.run via local / provider: / model: / outputs: 1" then the body.
    if not seen_outputs_header:
        if line.startswith("outputs:"):
            seen_outputs_header = True
        continue
    if first_content_at is None and line.strip():
        first_content_at = now

proc.wait()
t1 = time.monotonic()
err = proc.stderr.read() if proc.stderr else ""
rc = proc.returncode

os.makedirs(OUTDIR, exist_ok=True)
with open(os.path.join(OUTDIR, f"{LABEL}.timed.out"), "w", encoding="utf-8") as f:
    for ts, ln in timed_lines:
        f.write(f"{ts:.6f}\t{ln}\n")
with open(os.path.join(OUTDIR, f"{LABEL}.err"), "w", encoding="utf-8") as f:
    f.write(err)

result = {
    "label": LABEL,
    "rc": rc,
    "wall_s": round(t1 - t0, 4),
    "ttft_client_s": None if first_content_at is None else round(first_content_at, 4),
    "first_content_at_s": None if first_content_at is None else round(first_content_at, 4),
    "n_lines": len(timed_lines),
    "json_mode": USE_JSON,
}
with open(os.path.join(OUTDIR, f"{LABEL}.client.json"), "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2)
print(json.dumps(result))
