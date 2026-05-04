"""Vault registry I/O — atomic-rename writes to platform-resolved vaults.json.

Schema:
    {
      "version": 1,
      "vaults": {
        "<name>": {
          "path": "<abs>",
          "registered": "<UTC ISO>",
          "notion_enabled": true,           # optional, present if vault opts in
          "notion_db_id": "<32-char hex>",  # optional, paired with notion_enabled
        }
      }
    }

Older entries without the notion_* keys are valid — read_registry() does not
require them. The Notion ingest tool checks for `notion_enabled is True`
before doing any work.
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
    """Insert or update a vault entry idempotently. Preserves any existing
    Notion config on the entry. Returns the resulting registry."""
    data = read_registry()
    existing = data["vaults"].get(name, {})
    entry = {
        "path": str(Path(vault_path).expanduser().resolve()),
        "registered": _iso_now(),
    }
    # Carry over Notion config if previously set — re-registration must not
    # silently un-enable Notion ingest for a vault.
    for key in ("notion_enabled", "notion_db_id"):
        if key in existing:
            entry[key] = existing[key]
    data["vaults"][name] = entry
    write_registry(data)
    return data


def set_notion_config(name: str, db_id: str | None) -> dict:
    """Enable or disable Notion ingest on the named vault.

    Pass `db_id=None` to clear (sets notion_enabled=False, drops notion_db_id).
    Pass a 32-char hex (with or without dashes) to enable.
    Raises KeyError if the vault is not registered.
    """
    data = read_registry()
    if name not in data["vaults"]:
        raise KeyError(f"vault {name!r} is not registered")
    entry = data["vaults"][name]
    if db_id is None:
        entry["notion_enabled"] = False
        entry.pop("notion_db_id", None)
    else:
        entry["notion_enabled"] = True
        entry["notion_db_id"] = _normalize_db_id(db_id)
    write_registry(data)
    return data


def _normalize_db_id(db_id: str) -> str:
    """Normalize a Notion DB ID to dashed canonical form (32 hex with 4 dashes)."""
    raw = db_id.strip().replace("-", "").lower()
    if len(raw) != 32 or any(c not in "0123456789abcdef" for c in raw):
        raise ValueError(
            f"invalid Notion database id {db_id!r}: expected 32 hex chars "
            "(with or without dashes)"
        )
    return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"


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
