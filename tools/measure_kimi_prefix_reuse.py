#!/usr/bin/env python3
"""Replay a captured Caveman request against llama-server and measure reuse.

This replays a captured OpenClaw /v1/chat/completions payload directly against
the local Kimi server. It records:
  - wall time
  - time to first SSE line
  - time to first assistant content token
  - final prompt/eval timings and cached token count when available

The intent is to measure raw llama-server prefix reuse on the real full-tool
Caveman bootstrap, independent of OpenClaw abort behavior.
"""

from __future__ import annotations

import argparse
import copy
import http.client
import json
import sys
import time
from pathlib import Path


DEFAULT_URL = "http://127.0.0.1:18082/v1/chat/completions"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--request", required=True, help="captured request JSON")
    ap.add_argument("--url", default=DEFAULT_URL, help="target completions URL")
    ap.add_argument(
        "--mutated-suffix",
        default="",
        help="if set, replace the last user text in the final message with this text for the final run",
    )
    ap.add_argument(
        "--output",
        default="",
        help="optional JSON output path for the collected results",
    )
    return ap.parse_args()


def build_body(template: dict, mutated_suffix: str | None = None) -> dict:
    body = copy.deepcopy(template)
    if mutated_suffix is None:
        return body

    for msg in reversed(body.get("messages", [])):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for item in reversed(content):
                if item.get("type") == "text":
                    item["text"] = mutated_suffix
                    return body
        elif isinstance(content, str):
            msg["content"] = mutated_suffix
            return body

    raise RuntimeError("could not locate a user text field to mutate")


def split_url(url: str) -> tuple[str, int, str]:
    if not url.startswith("http://"):
        raise ValueError("only http:// URLs are supported for this probe")
    rest = url[len("http://") :]
    host_port, path = rest.split("/", 1)
    if ":" in host_port:
        host, port_s = host_port.rsplit(":", 1)
        port = int(port_s)
    else:
        host, port = host_port, 80
    return host, port, "/" + path


def run_once(url: str, body: dict, label: str) -> dict:
    host, port, path = split_url(url)
    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(payload)),
        "Accept": "text/event-stream",
    }

    conn = http.client.HTTPConnection(host, port, timeout=7200)
    t0 = time.perf_counter()
    conn.request("POST", path, body=payload, headers=headers)
    resp = conn.getresponse()

    first_line_s = None
    first_progress_s = None
    first_content_s = None
    final_chunk = None
    line_count = 0

    while True:
        line = resp.fp.readline()
        if not line:
            break
        line_count += 1
        if first_line_s is None:
            first_line_s = time.perf_counter() - t0
        if not line.startswith(b"data: "):
            continue
        payload_text = line[6:].strip()
        if payload_text == b"[DONE]":
            break
        try:
            chunk = json.loads(payload_text)
        except Exception:
            continue
        if first_progress_s is None and "prompt_progress" in chunk:
            first_progress_s = time.perf_counter() - t0
        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})
            if first_content_s is None and delta.get("content") not in (None, ""):
                first_content_s = time.perf_counter() - t0
        final_chunk = chunk

    total_s = time.perf_counter() - t0
    resp.read()
    conn.close()

    usage = None
    timings = None
    model = None
    if isinstance(final_chunk, dict):
        usage = final_chunk.get("usage")
        timings = final_chunk.get("timings")
        model = final_chunk.get("model")

    return {
        "label": label,
        "status": resp.status,
        "reason": resp.reason,
        "wall_s": round(total_s, 3),
        "first_line_s": round(first_line_s, 3) if first_line_s is not None else None,
        "first_progress_s": round(first_progress_s, 3) if first_progress_s is not None else None,
        "first_content_s": round(first_content_s, 3) if first_content_s is not None else None,
        "line_count": line_count,
        "usage": usage,
        "timings": timings,
        "model": model,
    }


def main() -> int:
    args = parse_args()
    template = json.loads(Path(args.request).read_text())

    mutated_suffix = args.mutated_suffix.strip() or None
    runs = [
        ("cold", build_body(template)),
        ("identical_replay", build_body(template)),
    ]
    if mutated_suffix is None:
        mutated_suffix = "This is a different suffix for prefix reuse testing."
    runs.append(("mutated_suffix", build_body(template, mutated_suffix)))

    results = []
    for label, body in runs:
        print(f"==> {label}", flush=True)
        res = run_once(args.url, body, label)
        results.append(res)
        print(json.dumps(res, indent=2, sort_keys=True), flush=True)

    if args.output:
        Path(args.output).write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
