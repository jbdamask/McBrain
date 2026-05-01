"""Search behavior: lexical cascade, semantic recall, hybrid query, lazy fetch.

The query subcommand is the user-facing center of the engine. These tests
verify that:
- The lexical cascade (rg → grep → pure-Python) is a fallback chain that
  always produces results.
- Semantic search recovers paraphrased queries that have zero lexical
  overlap with the matching wiki page.
- The hybrid query merges both modalities via RRF.
- The lazy-text-fetch optimization (bead McBrain-rtp) still holds —
  cmd_query must never SELECT all chunks' text upfront.
- The no-index lexical fallback path still works.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

import mcbrain_ops as ops


# ----------------------------- lexical cascade -----------------------------


def test_lexical_search_finds_term_in_python_fallback(
    fresh_vault: Path, monkeypatch
):
    """Force the pure-Python fallback by hiding rg and grep. Lexical results
    should still come back."""
    monkeypatch.setattr(ops.shutil, "which", lambda name: None)
    results = ops.lexical_search(fresh_vault, "heart attack")
    paths = [p for p, _ in results]
    assert "wiki/myocardial-infarction.md" in paths


def test_lexical_search_returns_empty_when_no_match(fresh_vault: Path):
    results = ops.lexical_search(fresh_vault, "zzzzzz_no_such_string_zzzzz")
    assert results == []


def test_lexical_search_no_wiki_dir_returns_empty(tmp_path: Path):
    """A vault without wiki/ should return [] cleanly, not raise."""
    vault = tmp_path / "v"
    vault.mkdir()
    assert ops.lexical_search(vault, "anything") == []


def test_lexical_search_ranks_by_hit_count(tmp_path: Path, monkeypatch):
    """A page with two hits ranks above a page with one hit."""
    monkeypatch.setattr(ops.shutil, "which", lambda name: None)
    vault = tmp_path / "v"
    (vault / "wiki").mkdir(parents=True)
    (vault / "wiki" / "many.md").write_text("apple apple apple", encoding="utf-8")
    (vault / "wiki" / "few.md").write_text("apple", encoding="utf-8")

    results = ops.lexical_search(vault, "apple")
    paths = [p for p, _ in results]
    assert paths.index("wiki/many.md") < paths.index("wiki/few.md")


# ----------------------------- semantic recall ------------------------------


def test_semantic_finds_paraphrase(populated_vault: Path):
    """'cardiac event' has zero lexical overlap with myocardial-infarction.md
    but should still be the top semantic hit."""
    conn = ops.open_db(populated_vault)
    try:
        results = ops.semantic_search(conn, "cardiac event")
    finally:
        conn.close()
    assert results, "semantic search returned nothing"
    top_path = results[0][0]
    assert top_path == "wiki/myocardial-infarction.md"


def test_semantic_finds_topic_via_synonyms(populated_vault: Path):
    """'how plants make food' should surface photosynthesis.md."""
    conn = ops.open_db(populated_vault)
    try:
        results = ops.semantic_search(conn, "how plants make food")
    finally:
        conn.close()
    assert results
    assert results[0][0] == "wiki/photosynthesis.md"


def test_semantic_search_returns_empty_when_no_chunks(
    vault_with_schema: Path, shared_embedder
):
    """An initialized but empty index returns no semantic results."""
    schema_sql = vault_with_schema / ".mcbrain" / "bin" / "schema.sql"
    ops.init_db(ops.index_db_path(vault_with_schema), schema_sql)

    conn = ops.open_db(vault_with_schema)
    try:
        results = ops.semantic_search(conn, "anything")
    finally:
        conn.close()
    assert results == []


# ----------------------------- query (hybrid) -------------------------------


def _capture_query(vault: Path, text: str, k: int, capsys) -> dict:
    rc = ops.cmd_query(vault, text, k)
    assert rc == 0, "cmd_query failed"
    return json.loads(capsys.readouterr().out)


def test_query_hybrid_top1_lexical_winner(populated_vault: Path, capsys):
    """A query whose words appear in the matching page wins."""
    payload = _capture_query(populated_vault, "heart attack", 4, capsys)
    assert payload["mode"] == "lexical+semantic"
    assert payload["results"][0]["path"] == "wiki/myocardial-infarction.md"


def test_query_hybrid_top1_semantic_winner(populated_vault: Path, capsys):
    """A paraphrase with no lexical overlap relies on the semantic side."""
    payload = _capture_query(populated_vault, "cardiac event", 4, capsys)
    assert payload["results"][0]["path"] == "wiki/myocardial-infarction.md"


def test_query_hybrid_results_have_excerpts(populated_vault: Path, capsys):
    payload = _capture_query(populated_vault, "neural network model size", 2, capsys)
    for r in payload["results"]:
        assert isinstance(r["excerpt"], str) and r["excerpt"]
        assert isinstance(r["score"], (int, float))
        assert r["score"] > 0


def test_query_respects_k_parameter(populated_vault: Path, capsys):
    payload = _capture_query(populated_vault, "bread fermentation", 1, capsys)
    assert len(payload["results"]) == 1


def test_query_empty_text_exits_nonzero(populated_vault: Path):
    with pytest.raises(SystemExit) as exc:
        ops.cmd_query(populated_vault, "   ", 4)
    assert exc.value.code == 2


# ----------------------------- no-index fallback ----------------------------


def test_query_falls_back_to_lexical_when_no_index(
    fresh_vault: Path, capsys, monkeypatch
):
    """A vault without .mcbrain/index.db should still answer queries via
    the pure-Python lexical search."""
    monkeypatch.setattr(ops.shutil, "which", lambda name: None)
    rc = ops.cmd_query(fresh_vault, "heart attack", 3)
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["mode"] == "lexical"
    paths = [r["path"] for r in payload["results"]]
    assert "wiki/myocardial-infarction.md" in paths


# --------------------- lazy text fetch (regression) -------------------------


def test_query_does_not_load_all_text_into_memory(
    populated_vault: Path, monkeypatch
):
    """Regression for bead McBrain-rtp.

    During cmd_query, no SQL statement may issue `SELECT ... text FROM chunks`
    without a WHERE clause. Text must only be fetched for the top-K paths.

    Implementation: replace open_db with a wrapper that installs sqlite3's
    set_trace_callback on each returned connection. The callback receives
    every SQL statement the connection executes (including via cursors).
    """
    seen_sql: list[str] = []
    real_open_db = ops.open_db

    def open_db_with_trace(vault):
        conn = real_open_db(vault)
        conn.set_trace_callback(seen_sql.append)
        return conn

    monkeypatch.setattr(ops, "open_db", open_db_with_trace)

    ops.cmd_query(populated_vault, "heart attack", 3)

    # No SELECT that pulls `text` without a WHERE filter.
    offending = [
        sql
        for sql in seen_sql
        if "FROM chunks" in sql
        and "text" in sql.lower()
        and "WHERE" not in sql.upper()
    ]
    assert offending == [], f"unbounded text SELECTs leaked: {offending}"

    # The lazy SELECT (with WHERE path IN (...)) MUST be issued so we know
    # the optimization actually ran rather than being silently absent.
    lazy_selects = [
        sql
        for sql in seen_sql
        if "SELECT path, text FROM chunks WHERE path IN" in sql
    ]
    assert lazy_selects, "expected lazy-fetch SELECT to run"
