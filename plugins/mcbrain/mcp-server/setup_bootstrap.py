"""Cross-platform setup driver for mcbrain-engine.

Stdlib-only — must run on whatever Python the user has on PATH, before any
venv exists. The mcbrain-setup SKILL invokes this script through Claude Code's
Bash tool with one of three flags:

  --check     env diagnostics (Python version, OS, rg, runtime presence)
  --install   create runtime venv at paths.runtime_root() and install deps
  --register  write the mcbrain-engine MCP entry into both Claude Desktop and
              Claude Code user-scope configs (idempotent)

All OS branching lives in paths.py and a single _rg_install_hint helper here.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402

ENGINE_FILES = ("mcbrain_engine.py", "schema.sql", "registry.py", "paths.py")
ENGINE_REQUIREMENTS = HERE / "requirements.txt"
PYTHON_MIN = (3, 10)
MCP_SERVER_NAME = "mcbrain-engine"


# ------------------------------- diagnostics --------------------------------


def _rg_install_hint() -> str:
    s = platform.system()
    if s == "Darwin":
        return "install with: brew install ripgrep"
    if s == "Windows":
        return "install with: winget install BurntSushi.ripgrep.MSVC  (or: scoop install ripgrep)"
    return "install via your package manager (apt install ripgrep, dnf install ripgrep, …)"


def cmd_check() -> int:
    py = sys.version_info
    print(f"OS: {platform.system()} ({platform.release()})")
    print(f"Python: {py.major}.{py.minor}.{py.micro}")
    if (py.major, py.minor) < PYTHON_MIN:
        print(
            f"WARN: Python {PYTHON_MIN[0]}.{PYTHON_MIN[1]}+ is required for "
            f"--install (running {py.major}.{py.minor}).",
            file=sys.stderr,
        )
    rg = shutil.which("rg")
    if rg:
        print(f"rg: {rg}")
    else:
        print(f"rg: not found — {_rg_install_hint()}")
    rr = paths.runtime_root()
    status = "exists" if rr.is_dir() else "missing"
    print(f"runtime_root: {rr}  [{status}]")
    print(f"registry_path: {paths.registry_path()}")
    return 0


# --------------------------------- install ----------------------------------


def cmd_install() -> int:
    py = sys.version_info
    if (py.major, py.minor) < PYTHON_MIN:
        print(
            f"error: Python {PYTHON_MIN[0]}.{PYTHON_MIN[1]}+ required, got "
            f"{py.major}.{py.minor}.",
            file=sys.stderr,
        )
        return 1

    rr = paths.runtime_root()
    if rr.is_dir() and (rr / "mcbrain_engine.py").is_file():
        print(f"runtime already at {rr} — skipping reinstall")
        return 0

    print(f"installing mcbrain-engine runtime at {rr}")
    print("(first install includes a ~30 MB FastEmbed model download)")
    rr.mkdir(parents=True, exist_ok=True)

    venv_root = rr / "venv"
    if not venv_root.is_dir():
        print(f"creating venv at {venv_root}")
        rc = subprocess.call([sys.executable, "-m", "venv", str(venv_root)])
        if rc != 0:
            print("venv creation failed", file=sys.stderr)
            return rc

    venv_py = paths.venv_python(rr)
    if not venv_py.is_file():
        print(
            f"venv created but interpreter missing at {venv_py}; the Python "
            "install may be incomplete",
            file=sys.stderr,
        )
        return 1

    for fname in ENGINE_FILES:
        src = HERE / fname
        if not src.is_file():
            print(f"missing source file: {src}", file=sys.stderr)
            return 1
        shutil.copy2(src, rr / fname)
        print(f"copied {fname}")

    print("pip install -r requirements.txt (this can take a minute)")
    rc = subprocess.call(
        [
            str(venv_py),
            "-m",
            "pip",
            "install",
            "--quiet",
            "-r",
            str(ENGINE_REQUIREMENTS),
        ]
    )
    if rc != 0:
        print("pip install failed", file=sys.stderr)
        return rc

    print(f"install complete → {rr}")
    return 0


# ---------------------------------- register --------------------------------


def cmd_register() -> int:
    rr = paths.runtime_root()
    if not (rr / "mcbrain_engine.py").is_file():
        print(
            f"no runtime at {rr} — run `python setup_bootstrap.py --install` first",
            file=sys.stderr,
        )
        return 1

    venv_py = paths.venv_python(rr)
    engine_script = rr / "mcbrain_engine.py"
    entry = {
        "command": str(venv_py),
        "args": [str(engine_script)],
    }

    desktop_path = paths.claude_desktop_config_path()
    desktop_changed = _merge_mcp_config(desktop_path, entry)
    print(
        f"Claude Desktop: {desktop_path}  "
        f"[{'updated' if desktop_changed else 'unchanged'}]"
    )

    code_changed = _register_with_claude_code(entry)
    return 0 if (desktop_changed or code_changed or True) else 0


def _register_with_claude_code(entry: dict) -> bool:
    """Prefer `claude mcp add --scope user`. Fall back to writing
    ~/.claude/settings.json directly if the CLI isn't on PATH."""
    if shutil.which("claude"):
        rc = subprocess.call(
            [
                "claude",
                "mcp",
                "add",
                "--scope",
                "user",
                MCP_SERVER_NAME,
                entry["command"],
                *entry["args"],
            ]
        )
        if rc == 0:
            print(f"Claude Code: registered via `claude mcp add --scope user`")
            return True
        print(
            f"WARN: `claude mcp add` exited {rc}; falling back to direct file write",
            file=sys.stderr,
        )
    code_settings = paths.claude_code_config_dir() / "settings.json"
    changed = _merge_mcp_config(code_settings, entry)
    print(
        f"Claude Code: {code_settings}  "
        f"[{'updated' if changed else 'unchanged'}]"
    )
    return changed


def _merge_mcp_config(path: Path, entry: dict) -> bool:
    """Idempotent merge of the mcbrain-engine entry under mcpServers.

    Returns True if the file changed, False if the existing entry already
    matched. JSON serialization (json.dump) handles backslash and quote
    escaping for paths-with-spaces on Windows.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            with path.open("r", encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"WARN: could not parse {path} ({exc}); rewriting from scratch",
                file=sys.stderr,
            )
            config = {}
    else:
        config = {}
    if not isinstance(config, dict):
        config = {}

    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        config["mcpServers"] = servers

    if servers.get(MCP_SERVER_NAME) == entry:
        return False
    servers[MCP_SERVER_NAME] = entry

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return True


# ---------------------------------- main ------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="setup_bootstrap.py",
        description="mcbrain-engine cross-platform setup driver",
    )
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="env diagnostics")
    group.add_argument(
        "--install", action="store_true", help="create runtime + venv + deps"
    )
    group.add_argument(
        "--register",
        action="store_true",
        help="write MCP entry to Claude Code + Desktop configs",
    )
    args = ap.parse_args(argv)

    if args.check:
        return cmd_check()
    if args.install:
        return cmd_install()
    if args.register:
        return cmd_register()
    return 2


if __name__ == "__main__":
    sys.exit(main())
