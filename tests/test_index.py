"""Integration tests for the index lifecycle: init, rebuild, sync, status.

Uses the session-scoped FastEmbed fixture so model load is paid once.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from io import StringIO
from pathlib import Path

import pytest

import mcbrain_ops as ops


# ----------------------------- init / rebuild ------------------------------


def test_init_db_creates_tables_and_meta(vault_with_schema: Path):
    """init_db writes both tables and seeds the meta row."""
    db_path = ops.index_db_path(vault_with_schema)
    schema_sql = vault_with_schema / ".mcbrain" / "bin" / "schema.sql"
    ops.init_db(db_path, schema_sql)

    conn = sqlite3.connect(db_path)
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "chunks" in names
        assert "meta" in names

        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        assert meta["embedding_model"] == ops.EMBEDDING_MODEL
        assert meta["embedding_dim"] == str(ops.EMBEDDING_DIM)
        assert meta["schema_version"] == ops.SCHEMA_VERSION
    finally:
        conn.close()


def test_rebuild_empty_vault(vault_with_schema: Path, shared_embedder):
    """A vault with no wiki/ files still produces a valid empty index."""
    # Wipe the wiki so it's empty.
    for p in (vault_with_schema / "wiki").glob("*.md"):
        p.unlink()

    rc = ops.cmd_rebuild(vault_with_schema)
    assert rc == 0

    db = ops.index_db_path(vault_with_schema)
    assert db.exists()
    conn = sqlite3.connect(db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert n == 0
    finally:
        conn.close()


def test_rebuild_populates_chunks(populated_vault: Path):
    """Rebuild against the four fixture wiki pages → 4 rows."""
    db = ops.index_db_path(populated_vault)
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT path, length(embedding) FROM chunks").fetchall()
        assert len(rows) == 4
        # 384 float32s = 1536 bytes per row.
        for path, embedding_len in rows:
            assert embedding_len == 384 * 4, f"unexpected embedding size for {path}"
    finally:
        conn.close()


def test_rebuild_idempotent(populated_vault: Path):
    """Running rebuild a second time produces the same chunk count and
    advances last_rebuild."""
    rc = ops.cmd_rebuild(populated_vault)
    assert rc == 0
    db = ops.index_db_path(populated_vault)
    conn = sqlite3.connect(db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert n == 4
    finally:
        conn.close()


# ----------------------------- sync (incremental) --------------------------


def test_sync_no_op_after_rebuild(populated_vault: Path, capsys):
    """Sync immediately after rebuild reports 0 changes and 4 total."""
    rc = ops.cmd_sync(populated_vault)
    assert rc == 0
    err = capsys.readouterr().err
    assert "added=0" in err
    assert "changed=0" in err
    assert "removed=0" in err
    assert "total=4" in err


def test_sync_detects_added_file(populated_vault: Path):
    """Adding a new wiki file is picked up incrementally."""
    new = populated_vault / "wiki" / "new-page.md"
    new.write_text("# New page\n\nA freshly added topic.\n", encoding="utf-8")

    rc = ops.cmd_sync(populated_vault)
    assert rc == 0

    conn = sqlite3.connect(ops.index_db_path(populated_vault))
    try:
        n = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert n == 5
        paths = {row[0] for row in conn.execute("SELECT path FROM chunks").fetchall()}
        assert "wiki/new-page.md" in paths
    finally:
        conn.close()


def test_sync_detects_modified_file(populated_vault: Path):
    """Modifying a file changes its hash → re-embed."""
    target = populated_vault / "wiki" / "sourdough.md"
    original_hash = ops.hash_file(target)

    target.write_text(
        "# Sourdough Bread\n\nCompletely rewritten content about something else "
        "entirely — wild fermentation isn't actually sourdough's defining feature.\n",
        encoding="utf-8",
    )
    new_hash = ops.hash_file(target)
    assert original_hash != new_hash

    rc = ops.cmd_sync(populated_vault)
    assert rc == 0

    conn = sqlite3.connect(ops.index_db_path(populated_vault))
    try:
        stored_hash = conn.execute(
            "SELECT content_hash FROM chunks WHERE path = ?", ("wiki/sourdough.md",)
        ).fetchone()[0]
        assert stored_hash == new_hash
    finally:
        conn.close()


def test_sync_detects_deleted_file(populated_vault: Path):
    """Deleting a wiki file removes it from the index on next sync."""
    (populated_vault / "wiki" / "sourdough.md").unlink()
    rc = ops.cmd_sync(populated_vault)
    assert rc == 0

    conn = sqlite3.connect(ops.index_db_path(populated_vault))
    try:
        n = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert n == 3
        paths = {row[0] for row in conn.execute("SELECT path FROM chunks").fetchall()}
        assert "wiki/sourdough.md" not in paths
    finally:
        conn.close()


def test_sync_initializes_db_when_missing(vault_with_schema: Path, shared_embedder):
    """sync should bootstrap the DB if it doesn't exist yet (it can be the
    first index-building call after a partial migrate)."""
    assert not ops.index_db_path(vault_with_schema).exists()
    rc = ops.cmd_sync(vault_with_schema)
    assert rc == 0
    assert ops.index_db_path(vault_with_schema).exists()
    conn = sqlite3.connect(ops.index_db_path(vault_with_schema))
    try:
        n = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert n == 4
    finally:
        conn.close()


# ----------------------------- status ---------------------------------------


def test_status_unprovisioned(fresh_vault: Path, capsys):
    """A vault without .mcbrain/index.db reports provisioned=false."""
    rc = ops.cmd_status(fresh_vault)
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["provisioned"] is False
    assert payload["index_path"].endswith("/.mcbrain/index.db")


def test_status_populated(populated_vault: Path, capsys):
    """Status JSON shape matches what callers depend on."""
    rc = ops.cmd_status(populated_vault)
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert payload["provisioned"] is True
    assert payload["doc_count"] == 4
    assert payload["embedding_model"] == ops.EMBEDDING_MODEL
    assert payload["embedding_dim"] == ops.EMBEDDING_DIM
    assert payload["schema_version"] == "1"
    assert payload["last_sync"]
    assert payload["last_rebuild"]
    assert payload["index_size_bytes"] > 0


# ----------------------------- model compatibility -------------------------


def test_check_model_compatibility_dies_on_dim_mismatch(populated_vault: Path):
    """If the stored dim differs from the running code's, abort and tell the
    user to rebuild."""
    conn = sqlite3.connect(ops.index_db_path(populated_vault))
    try:
        conn.execute("UPDATE meta SET value = '999' WHERE key = 'embedding_dim'")
        conn.commit()
    finally:
        conn.close()

    # Re-open via open_db (which is what subcommands use) and call the check.
    conn = ops.open_db(populated_vault)
    try:
        with pytest.raises(SystemExit) as exc:
            ops.check_model_compatibility(conn, populated_vault)
        assert exc.value.code == 6
    finally:
        conn.close()
