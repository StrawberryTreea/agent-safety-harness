"""Public-only container worker. Private score inputs are never accepted here."""
from __future__ import annotations

from contextlib import redirect_stdout
import json
import importlib.metadata
import os
from pathlib import Path
import socket
import sys


def main() -> None:
    request = json.load(sys.stdin)
    if request.get("action") == "probe":
        read_only = bool(os.statvfs("/").f_flag & os.ST_RDONLY)
        network = False
        try:
            with socket.create_connection(("1.1.1.1", 443), timeout=2):
                network = True
        except OSError:
            pass
        interfaces = sorted(p.name for p in Path("/sys/class/net").iterdir())
        status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
        print(json.dumps({"uid": os.getuid(), "root_read_only": read_only, "external_network_available": network,
                          "interfaces": interfaces, "effective_capabilities": status["CapEff"].strip(),
                          "no_new_privileges": status["NoNewPrivs"].strip() == "1", "python": sys.version,
                          "dependencies": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}}))
        return
    if request.get("action") != "capture" or set(request) - {"action", "agent", "agents_config"}:
        raise ValueError("Worker accepts public capture requests only")
    os.environ["ASH_AGENT_SAFETYBENCH_ROOT"] = "/public/source"
    from .cli import build_parser, command_capture
    argv = ["capture", "--agent", request["agent"], "--input", "/public/tasks.jsonl", "--out", "/output"]
    if request.get("agents_config"):
        if request["agents_config"] != "/public/agents.json":
            raise ValueError("Invalid public agent configuration path")
        argv += ["--agents-config", "/public/agents.json"]
    with redirect_stdout(sys.stderr):
        code = command_capture(build_parser().parse_args(argv))
    if code:
        raise RuntimeError("Native capture failed")
    print(json.dumps({"phase": "capture", "status": "completed", "contains_private_oracle": False}))


if __name__ == "__main__":
    main()
