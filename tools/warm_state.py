#!/usr/bin/env python3
"""
warm_state.py — Stage 6B warm-state registry for the caveman/Kimi Linear service.

OpenClaw must *know* whether the Caveman bootstrap is warm in the live
llama-server instead of inferring it from experiments. This tool is the
registry + probe behind that knowledge.

Registry key:   (agent_id, model_id, bootstrap_fingerprint)
Entry fields:   status, server_pid, cached_tokens, warmed_at,
                updated_at, history (bounded transition log)

State machine (SERVICE-ROADMAP.md, Stage 6B):

    COLD -> PREFILLING -> READY

    READY      -> STALE  when the bootstrap fingerprint changes
    READY      -> COLD   when the inference-server PID changes
    PREFILLING -> FAILED
    FAILED     -> PREFILLING on explicit retry (begin-prefill)

The PID rule matters: Stage 5B established that restart persistence is
unsupported in this configuration, so a server restart (new PID) must
invalidate the prior READY state. The fingerprint rule matters: if the
canonical bootstrap a normal first turn would receive changes, the old
warm state no longer corresponds to what the agent will use.

Commands:

    status                  print registry state plus live probe info
    probe                   apply PID/fingerprint transitions, write, print
    begin-prefill           transition -> PREFILLING (explicit retry path)
    ready --pid P --cached-tokens N
                            PREFILLING -> READY (binds state to server PID)
    fail [--reason R]       PREFILLING -> FAILED
    reset                   any -> COLD
    fingerprint             print the current bootstrap fingerprint

Options:

    --agent A               registry key agent id (default: caveman)
    --model M               registry key model id (default: kimi-linear-48b)
    --registry PATH         registry file (default: runtime/state/warm-state.json)
    --fingerprint-source P  canonical bootstrap body JSON (default: the captured
                            stage6a4 ordinary Caveman request body)
    --json                  machine-readable output

The bootstrap fingerprint is defined as sha256 over the canonical bootstrap
body a normal first turn would serialize: the system prompt, the tool
declarations, and tool_choice from the captured ordinary Caveman request
(dev-openclaw/state/stage6a4-request-body.json). If that body changes, the
fingerprint changes, and the probe marks any READY entry STALE.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = REPO / "runtime" / "state" / "warm-state.json"
DEFAULT_SOURCE = REPO / "dev-openclaw" / "state" / "stage6a4-request-body.json"
PIDFILE = Path("/tmp/kimi-llama-server.pid")
LIVE_BIN_MARKER = "runtime/live/bin/llama-server"
SCHEMA = "warm-state/v1"
MAX_HISTORY = 20


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compute_fingerprint(source: Path) -> str:
    """sha256 over the canonical bootstrap body (system + tools + tool_choice)."""
    with open(source, "r", encoding="utf-8") as fh:
        body = json.load(fh)
    system = None
    for msg in body.get("messages", []):
        if msg.get("role") == "system":
            system = msg.get("content")
            break
    if system is None:
        raise SystemExit(f"fingerprint source {source} has no system message")
    canonical = json.dumps(
        {
            "system": system,
            "tools": body.get("tools"),
            "tool_choice": body.get("tool_choice"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def discover_live_pid() -> int | None:
    """Live llama-server PID: pidfile first, then pgrep fallback. Read-only."""
    try:
        pid = int(PIDFILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["pgrep", "-f", LIVE_BIN_MARKER],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.split()
        if out:
            return int(out[0])
    except Exception:
        pass
    return None


def load_registry(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"schema": SCHEMA, "entries": {}}


def save_registry(path: Path, reg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def key_for(agent: str, model: str) -> str:
    return f"{agent}:{model}"


def ensure_entry(reg: dict, agent: str, model: str, fingerprint: str) -> dict:
    k = key_for(agent, model)
    entry = reg["entries"].get(k)
    if entry is None:
        entry = {
            "agent_id": agent,
            "model_id": model,
            "bootstrap_fingerprint": fingerprint,
            "status": "COLD",
            "server_pid": None,
            "cached_tokens": None,
            "warmed_at": None,
            "updated_at": now_iso(),
            "history": [],
        }
        reg["entries"][k] = entry
    return entry


def record(entry: dict, new_status: str, reason: str) -> None:
    old = entry["status"]
    if old == new_status:
        return
    entry["status"] = new_status
    entry["updated_at"] = now_iso()
    entry.setdefault("history", []).append(
        {"at": now_iso(), "from": old, "to": new_status, "reason": reason}
    )
    entry["history"] = entry["history"][-MAX_HISTORY:]


def probe_transitions(entry: dict, live_pid: int | None, fingerprint: str) -> list[str]:
    """Apply the automatic PID/fingerprint rules. Returns reasons recorded."""
    reasons = []
    status = entry["status"]
    if status == "READY":
        recorded_pid = entry.get("server_pid")
        if live_pid is None:
            r = "server not running; READY invalidated"
            record(entry, "COLD", r)
            reasons.append(r)
        elif recorded_pid is None or live_pid != recorded_pid:
            r = f"inference-server PID changed {recorded_pid} -> {live_pid}; READY invalidated"
            record(entry, "COLD", r)
            reasons.append(r)
        elif fingerprint != entry.get("bootstrap_fingerprint"):
            r = "bootstrap fingerprint changed; READY marked STALE"
            record(entry, "STALE", r)
            reasons.append(r)
    elif status == "PREFILLING":
        target = entry.get("server_pid")
        if target is not None and live_pid is not None and live_pid != target:
            r = f"prefill target server died (pid {target} -> live {live_pid})"
            record(entry, "FAILED", r)
            reasons.append(r)
    return reasons


def render_human(agent: str, model: str, entry: dict, live_pid: int | None,
                 fingerprint: str) -> str:
    fp_match = fingerprint == entry.get("bootstrap_fingerprint")
    lines = [
        f"{agent} / {model}",
        f"  status:           {entry['status']}",
        f"  fingerprint:      {entry.get('bootstrap_fingerprint', '(none)')}",
        f"  fingerprint now:  {fingerprint}  (match: {'yes' if fp_match else 'no'})",
        f"  server_pid:       {entry.get('server_pid')}",
        f"  cached_tokens:    {entry.get('cached_tokens')}",
        f"  warmed_at:        {entry.get('warmed_at')}",
        f"  updated_at:       {entry.get('updated_at')}",
        f"  live llama-server pid: {live_pid if live_pid is not None else 'NOT RUNNING'}",
    ]
    hist = entry.get("history", [])
    if hist:
        lines.append("  history (last %d):" % len(hist))
        for h in hist[-5:]:
            lines.append(
                f"    {h['at']}  {h['from']} -> {h['to']}  ({h['reason']})"
            )
    return "\n".join(lines)


def cmd_status(args) -> int:
    reg = load_registry(args.registry)
    entry = ensure_entry(reg, args.agent, args.model, args.fingerprint)
    live = discover_live_pid()
    if args.json:
        print(json.dumps(
            {
                "agent_id": args.agent,
                "model_id": args.model,
                "status": entry["status"],
                "bootstrap_fingerprint": entry.get("bootstrap_fingerprint"),
                "current_fingerprint": args.fingerprint,
                "fingerprint_match": args.fingerprint == entry.get("bootstrap_fingerprint"),
                "server_pid": entry.get("server_pid"),
                "cached_tokens": entry.get("cached_tokens"),
                "warmed_at": entry.get("warmed_at"),
                "updated_at": entry.get("updated_at"),
                "live_pid": live,
                "history": entry.get("history", [])[-MAX_HISTORY:],
            },
            indent=2,
        ))
    else:
        print(render_human(args.agent, args.model, entry, live, args.fingerprint))
    return 0


def cmd_probe(args) -> int:
    reg = load_registry(args.registry)
    entry = ensure_entry(reg, args.agent, args.model, args.fingerprint)
    live = discover_live_pid()
    reasons = probe_transitions(entry, live, args.fingerprint)
    save_registry(args.registry, reg)
    if args.json:
        print(json.dumps(
            {
                "agent_id": args.agent,
                "model_id": args.model,
                "status": entry["status"],
                "live_pid": live,
                "transitions": reasons,
                "fingerprint_match": args.fingerprint == entry.get("bootstrap_fingerprint"),
            },
            indent=2,
        ))
    else:
        print(render_human(args.agent, args.model, entry, live, args.fingerprint))
        if reasons:
            print("transitions applied: " + "; ".join(reasons))
    return 0


def cmd_begin_prefill(args) -> int:
    reg = load_registry(args.registry)
    entry = ensure_entry(reg, args.agent, args.model, args.fingerprint)
    live = discover_live_pid()
    record(entry, "PREFILLING", "explicit begin-prefill")
    if live is not None:
        entry["server_pid"] = live  # bind prefill to the live server
    entry["cached_tokens"] = None
    entry["warmed_at"] = None
    entry["updated_at"] = now_iso()
    save_registry(args.registry, reg)
    print(render_human(args.agent, args.model, entry, live, args.fingerprint))
    return 0


def cmd_ready(args) -> int:
    reg = load_registry(args.registry)
    entry = ensure_entry(reg, args.agent, args.model, args.fingerprint)
    if entry["status"] != "PREFILLING":
        print(f"error: cannot mark READY from {entry['status']}; run begin-prefill first",
              file=sys.stderr)
        return 2
    record(entry, "READY", f"prefill completed (cached_tokens={args.cached_tokens})")
    entry["server_pid"] = args.pid
    entry["cached_tokens"] = args.cached_tokens
    entry["warmed_at"] = now_iso()
    entry["updated_at"] = now_iso()
    save_registry(args.registry, reg)
    live = discover_live_pid()
    print(render_human(args.agent, args.model, entry, live, args.fingerprint))
    return 0


def cmd_fail(args) -> int:
    reg = load_registry(args.registry)
    entry = ensure_entry(reg, args.agent, args.model, args.fingerprint)
    if entry["status"] != "PREFILLING":
        print(f"error: cannot mark FAILED from {entry['status']}", file=sys.stderr)
        return 2
    record(entry, "FAILED", args.reason or "prefill failed")
    entry["updated_at"] = now_iso()
    save_registry(args.registry, reg)
    live = discover_live_pid()
    print(render_human(args.agent, args.model, entry, live, args.fingerprint))
    return 0


def cmd_reset(args) -> int:
    reg = load_registry(args.registry)
    entry = ensure_entry(reg, args.agent, args.model, args.fingerprint)
    record(entry, "COLD", "explicit reset")
    entry["server_pid"] = None
    entry["cached_tokens"] = None
    entry["warmed_at"] = None
    entry["updated_at"] = now_iso()
    save_registry(args.registry, reg)
    live = discover_live_pid()
    print(render_human(args.agent, args.model, entry, live, args.fingerprint))
    return 0


def cmd_fingerprint(args) -> int:
    fp = compute_fingerprint(args.fingerprint_source)
    if args.json:
        print(json.dumps({"agent_id": args.agent, "model_id": args.model,
                          "bootstrap_fingerprint": fp}, indent=2))
    else:
        print(fp)
    return 0


def main() -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true")

    p = argparse.ArgumentParser(description="Stage 6B warm-state registry", parents=[common])
    p.add_argument("--agent", default="caveman")
    p.add_argument("--model", default="kimi-linear-48b")
    p.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    p.add_argument("--fingerprint-source", type=Path, default=DEFAULT_SOURCE)
    sub = p.add_subparsers(dest="command", required=True)

    for name in ("status", "probe"):
        sp = sub.add_parser(name, parents=[common])
        sp.set_defaults(func=cmd_status if name == "status" else cmd_probe)

    sp = sub.add_parser("begin-prefill", parents=[common])
    sp.set_defaults(func=cmd_begin_prefill)

    sp = sub.add_parser("ready", parents=[common])
    sp.add_argument("--pid", type=int, required=True)
    sp.add_argument("--cached-tokens", type=int, required=True)
    sp.set_defaults(func=cmd_ready)

    sp = sub.add_parser("fail", parents=[common])
    sp.add_argument("--reason", default=None)
    sp.set_defaults(func=cmd_fail)

    sp = sub.add_parser("reset", parents=[common])
    sp.set_defaults(func=cmd_reset)

    sp = sub.add_parser("fingerprint", parents=[common])
    sp.set_defaults(func=cmd_fingerprint)

    args = p.parse_args()
    if args.command == "fingerprint":
        args.fingerprint = compute_fingerprint(args.fingerprint_source)
    else:
        try:
            args.fingerprint = compute_fingerprint(args.fingerprint_source)
        except SystemExit as exc:
            print(f"warning: {exc}", file=sys.stderr)
            args.fingerprint = "unknown"
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
