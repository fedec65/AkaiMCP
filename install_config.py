#!/usr/bin/env python3
"""Install the MPC SDK MCP server into Claude Desktop (Option A: repo-local install).

Usage:
    python3 mcp/install_config.py [--mpc-host 192.168.1.50] [--workspace DIR] [--no-venv]
                                  [--config PATH] [--dry-run]

1. Creates mcp/.venv and installs the server's dependencies into it (skip with --no-venv).
2. Finds claude_desktop_config.json, backs it up, and adds or updates only the "mpc-sdk" entry.
   Other servers' entries are never touched. Running it twice changes nothing the second time.

Needs Python 3.10+ (the mcp package requires it). The SDK scripts themselves still run on 3.9+.
Standard library only.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server.py"
VENV = HERE / ".venv"
REQUIREMENTS = ["mcp>=1.8,<2", "jsonschema>=4"]
ENTRY = "mpc-sdk"


def default_config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "Claude" / \
            "claude_desktop_config.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "Claude" / \
        "claude_desktop_config.json"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_venv() -> Path:
    py = venv_python()
    if not py.is_file():
        print("Creating %s" % VENV)
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    print("Installing dependencies: %s" % " ".join(REQUIREMENTS))
    subprocess.run([str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check"] + REQUIREMENTS,
                   check=True)
    return py


def build_entry(python: str, existing: dict, mpc_host, workspace) -> dict:
    env = dict(existing.get("env") or {})
    if mpc_host is not None:
        env["MPC_HOST"] = mpc_host
    env.setdefault("MPC_HOST", "")
    if workspace:
        env["WORKSPACE"] = str(Path(workspace).expanduser().resolve())
    return {"command": python, "args": [str(SERVER)], "env": env}


def update_config(config_path: Path, entry_fn, dry_run: bool = False) -> bool:
    """Adds/updates the mpc-sdk entry. Returns True if the file changed."""
    data = {}
    if config_path.is_file():
        text = config_path.read_text(encoding="utf-8")
        if text.strip():
            try:
                data = json.loads(text)
            except ValueError as e:
                raise SystemExit("ERROR: %s is not valid JSON (%s). Fix it by hand; nothing was changed." %
                                 (config_path, e))
    if not isinstance(data, dict):
        raise SystemExit("ERROR: %s does not contain a JSON object; nothing was changed." % config_path)
    servers = data.get("mcpServers")
    if servers is None:
        servers = data["mcpServers"] = {}
    if not isinstance(servers, dict):
        raise SystemExit("ERROR: mcpServers in %s is not an object; nothing was changed." % config_path)
    new = entry_fn(servers.get(ENTRY) or {})
    if servers.get(ENTRY) == new:
        return False
    servers[ENTRY] = new
    if dry_run:
        print(json.dumps({ENTRY: new}, indent=2))
        return True
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.is_file():
        backup = config_path.with_name(config_path.name + ".bak-%s" % time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(config_path, backup)
        print("Backed up the old config to %s" % backup)
    tmp = config_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, config_path)
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Add the MPC SDK MCP server to Claude Desktop.")
    ap.add_argument("--mpc-host", help="IP address of the MPC for the mpc_* tools (keeps the current value if omitted)")
    ap.add_argument("--workspace", help="writable folder for plugins/ and build output (default: the SDK folder)")
    ap.add_argument("--no-venv", action="store_true", help="use this Python instead of creating mcp/.venv")
    ap.add_argument("--config", help="path to claude_desktop_config.json (default: Claude Desktop's location)")
    ap.add_argument("--dry-run", action="store_true", help="print the entry without writing anything")
    args = ap.parse_args(argv)

    if sys.version_info < (3, 10):
        print("ERROR: the MCP server needs Python 3.10 or newer (this is %s)." % sys.version.split()[0])
        print("       Install a newer Python (python.org or brew install python) and run this again with it.")
        return 1

    python = sys.executable if (args.no_venv or args.dry_run) else str(ensure_venv())
    config = Path(args.config).expanduser() if args.config else default_config_path()
    changed = update_config(config, lambda old: build_entry(python, old, args.mpc_host, args.workspace),
                            args.dry_run)
    if args.dry_run:
        return 0
    if changed:
        print("Added the '%s' server to %s" % (ENTRY, config))
        print("Restart Claude Desktop (quit it fully, then reopen) to load it.")
    else:
        print("'%s' is already set up in %s; nothing changed." % (ENTRY, config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
