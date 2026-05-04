"""mcbrain-engine launcher — bootstraps the venv on first launch.

Stdlib-only. Claude Desktop / Claude Code register this script as the MCP
`command`; on every launch we either exec straight into the venv's Python
(fast path) or provision the venv first (one-time, ~30 sec). The launcher
then re-execs into the venv's Python running mcbrain_engine.py — Claude
sees a single MCP process throughout.

Why a launcher exists at all: setup writes pure files via Cowork's Write
tool (which bridges to the user's machine through a granted folder mount),
but Cowork cannot run `pip install` against the host's Python interpreter
from inside its Linux sandbox. So the venv has to be provisioned the first
time Claude Desktop natively launches the MCP, which is what this script
does. Cross-platform: macOS, Windows, and Linux are all supported — the
launcher branches on `os.name` for venv interpreter paths.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIREMENTS = HERE / "requirements.txt"
ENGINE = HERE / "mcbrain_engine.py"
PYTHON_MIN = (3, 10)


def venv_dir() -> Path:
    return HERE / "venv"


def venv_python() -> Path:
    if os.name == "nt":
        return venv_dir() / "Scripts" / "python.exe"
    return venv_dir() / "bin" / "python"


def log(msg: str) -> None:
    print(f"[mcbrain-engine launcher] {msg}", file=sys.stderr, flush=True)


def venv_is_ready() -> bool:
    """True if the venv interpreter exists and the runtime deps are importable."""
    py = venv_python()
    if not py.is_file():
        return False
    try:
        result = subprocess.run(
            [str(py), "-c", "import fastembed, numpy, mcp"],
            capture_output=True,
            timeout=20,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def provision_venv() -> None:
    py = sys.version_info
    if (py.major, py.minor) < PYTHON_MIN:
        log(
            f"FATAL: Python {PYTHON_MIN[0]}.{PYTHON_MIN[1]}+ required, "
            f"got {py.major}.{py.minor}.{py.micro} ({sys.executable})"
        )
        sys.exit(1)

    log(
        f"first launch — provisioning runtime venv at {venv_dir()} "
        "(this takes ~30 seconds; Claude Desktop may show a connection error "
        "if it times out — restart Claude Desktop and try again, the venv "
        "will already be ready)"
    )

    if venv_dir().exists():
        log("removing partial venv from a prior interrupted run")
        shutil.rmtree(venv_dir())

    log(f"creating venv with {sys.executable}")
    rc = subprocess.call([sys.executable, "-m", "venv", str(venv_dir())])
    if rc != 0:
        log(f"FATAL: venv creation failed (exit {rc})")
        sys.exit(rc)

    if not venv_python().is_file():
        log(f"FATAL: venv created but interpreter missing at {venv_python()}")
        sys.exit(1)

    log("installing requirements (fastembed, numpy, mcp) — first install only")
    rc = subprocess.call(
        [
            str(venv_python()),
            "-m",
            "pip",
            "install",
            "--quiet",
            "-r",
            str(REQUIREMENTS),
        ]
    )
    if rc != 0:
        log(
            f"FATAL: pip install failed (exit {rc}). Check network access and "
            f"that {sys.executable} can reach pypi.org."
        )
        sys.exit(rc)

    log("venv ready — handing off to mcbrain_engine")


def refuse_if_in_cowork_sandbox() -> None:
    """Refuse to run if launched from inside Claude Cowork's Linux sandbox.

    Cowork mounts user-granted folders under /sessions/<id>/mnt/. If the
    launcher was invoked via Cowork's Bash tool against a mounted path, it
    would create a venv with sandbox-baked shebangs — producing the
    'bad interpreter: /sessions/.../python3: no such file or directory'
    error when the user later tries to use the venv from a host terminal.

    The launcher is only meant to be invoked natively by Claude Desktop /
    Claude Code on the user's machine (macOS, Windows, or Linux), not from
    inside a sandboxed chat session.
    """
    here = str(HERE)
    if here.startswith("/sessions/") or "/mnt/" in here:
        log(
            f"FATAL: launcher invoked from inside Cowork's sandbox ({HERE}). "
            "This script must be launched natively by Claude Desktop or "
            "Claude Code, not via Cowork's Bash tool. If you're running "
            "setup, just register the MCP entry and restart Claude Desktop "
            "— Claude Desktop will launch the engine natively on first use."
        )
        sys.exit(1)


def main() -> None:
    refuse_if_in_cowork_sandbox()

    for required in (REQUIREMENTS, ENGINE):
        if not required.is_file():
            log(f"FATAL: missing {required} — runtime install is incomplete")
            sys.exit(1)

    if not venv_is_ready():
        provision_venv()

    py = str(venv_python())
    os.execv(py, [py, str(ENGINE), *sys.argv[1:]])


if __name__ == "__main__":
    main()
