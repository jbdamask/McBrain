"""Vault registry I/O — atomic-rename writes to platform-resolved vaults.json.

Schema:
    {
      "version": 1,
      "vaults": {
        "<name>": {
          "path": "<abs>",
          "registered": "<UTC ISO>",
        }
      }
    }
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import paths

REGISTRY_VERSION = 1


def _empty() -> dict:
    return {"version": REGISTRY_VERSION, "vaults": {}}


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_registry() -> dict:
    """Return the current registry, or the empty default if the file is absent."""
    path = paths.registry_path()
    if not path.is_file():
        return _empty()
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    version = data.get("version")
    if version != REGISTRY_VERSION:
        raise ValueError(
            f"unsupported registry version {version!r} at {path}; "
            f"expected {REGISTRY_VERSION}"
        )
    if not isinstance(data.get("vaults"), dict):
        raise ValueError(f"registry at {path} is missing a 'vaults' object")
    return data


def write_registry(data: dict) -> None:
    """Atomic write — temp file + fsync + os.replace into place."""
    path = paths.registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def put_vault(name: str, vault_path: Path) -> dict:
    """Insert or update a vault entry idempotently. Returns the resulting registry."""
    data = read_registry()
    entry = {
        "path": str(Path(vault_path).expanduser().resolve()),
        "registered": _iso_now(),
    }
    data["vaults"][name] = entry
    write_registry(data)
    return data


def remove_vault(name: str) -> bool:
    """Delete the named entry. Returns True if removed, False if absent."""
    data = read_registry()
    if name not in data["vaults"]:
        return False
    del data["vaults"][name]
    write_registry(data)
    return True


def find_vault_by_name(name: str) -> dict | None:
    return read_registry()["vaults"].get(name)


def find_vault_by_path(vault_path: Path) -> tuple[str, dict] | None:
    """Reverse-lookup by canonicalized path. Returns (name, entry) or None."""
    target = Path(vault_path).expanduser().resolve()
    for name, entry in read_registry()["vaults"].items():
        try:
            if Path(entry["path"]).resolve() == target:
                return name, entry
        except (OSError, KeyError):
            continue
    return None
