"""Cross-platform path resolver for mcbrain-engine.

Stdlib-only — must be importable by setup_bootstrap.py before the runtime
venv exists, and by the engine itself once it does. Branching on os.name /
sys.platform happens here and nowhere else.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_windows() -> bool:
    return os.name == "nt"


def is_macos() -> bool:
    return sys.platform == "darwin"


def runtime_root() -> Path:
    """Where the mcbrain-engine runtime lives (venv + script + schema)."""
    if is_windows():
        local = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local"
        )
        return Path(local) / "mcbrain-engine"
    if is_macos():
        return Path.home() / "Library" / "Application Support" / "mcbrain-engine"
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "mcbrain-engine"
    return Path.home() / ".local" / "share" / "mcbrain-engine"


def registry_path() -> Path:
    """Vault registry — vaults.json under the platform user-config dir."""
    if is_windows():
        appdata = os.environ.get("APPDATA") or str(
            Path.home() / "AppData" / "Roaming"
        )
        return Path(appdata) / "mcbrain" / "vaults.json"
    if is_macos():
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "mcbrain"
            / "vaults.json"
        )
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        return Path(xdg_config) / "mcbrain" / "vaults.json"
    return Path.home() / ".config" / "mcbrain" / "vaults.json"


def venv_python(runtime_dir: Path) -> Path:
    """Python interpreter inside <runtime_dir>/venv/, branched per OS."""
    if is_windows():
        return runtime_dir / "venv" / "Scripts" / "python.exe"
    return runtime_dir / "venv" / "bin" / "python"


def claude_desktop_config_path() -> Path:
    """Claude Desktop / Cowork MCP config file."""
    if is_windows():
        appdata = os.environ.get("APPDATA") or str(
            Path.home() / "AppData" / "Roaming"
        )
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    if is_macos():
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        return Path(xdg_config) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def claude_code_config_dir() -> Path:
    """Claude Code user-scope config dir — ~/.claude on every OS."""
    return Path.home() / ".claude"
