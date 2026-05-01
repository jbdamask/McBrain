"""Unit tests — pure-Python logic, no DB, no embedder.

These run without fastembed or numpy and exercise small focused pieces:
RRF math, excerpt rendering, hashing, path manipulation, blob round-trip
(the blob round-trip lazily imports numpy via the helper, so it does need
numpy in the test runner).
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

import mcbrain_ops as ops


# ----------------------------- rrf_fuse -------------------------------------


def test_rrf_fuse_single_list_descending_by_rank():
    """Reciprocal rank fusion over one list = 1/(k+rank+1)."""
    fused = ops.rrf_fuse([["a", "b", "c"]], k=60)
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["b"] == pytest.approx(1 / 62)
    assert fused["c"] == pytest.approx(1 / 63)
    assert fused["a"] > fused["b"] > fused["c"]


def test_rrf_fuse_two_lists_overlap_boosts_score():
    """A doc that appears in both lists scores higher than one that's only in one."""
    fused = ops.rrf_fuse([["a", "b"], ["a", "c"]], k=60)
    assert fused["a"] == pytest.approx(2 / 61)
    assert fused["b"] == pytest.approx(1 / 62)
    assert fused["c"] == pytest.approx(1 / 62)
    assert fused["a"] > fused["b"]
    assert fused["a"] > fused["c"]


def test_rrf_fuse_disjoint_lists_no_overlap():
    fused = ops.rrf_fuse([["a"], ["b"]], k=60)
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["b"] == pytest.approx(1 / 61)


def test_rrf_fuse_empty():
    assert ops.rrf_fuse([]) == {}
    assert ops.rrf_fuse([[]]) == {}


def test_rrf_fuse_default_k_is_60():
    """Module constant should match what the function uses by default."""
    assert ops.RRF_K == 60
    fused = ops.rrf_fuse([["x"]])
    assert fused["x"] == pytest.approx(1 / 61)


# ----------------------------- make_excerpt --------------------------------


def test_make_excerpt_picks_first_body_line():
    text = (
        "---\n"
        "type: concept\n"
        "---\n"
        "\n"
        "# Heading\n"
        "\n"
        "This is the first body sentence.\n"
        "Second line that won't be used.\n"
    )
    assert ops.make_excerpt(text) == "This is the first body sentence."


def test_make_excerpt_truncates_long_lines():
    long_line = "A" * 500
    excerpt = ops.make_excerpt(long_line, max_chars=240)
    assert len(excerpt) <= 241  # 240 chars + the ellipsis
    assert excerpt.endswith("…")


def test_make_excerpt_falls_back_when_only_headings():
    """If every line is empty / frontmatter / heading, fall back to the whole text."""
    text = "---\n---\n# Heading\n## Sub\n"
    excerpt = ops.make_excerpt(text)
    assert excerpt  # nonempty
    assert "Heading" in excerpt or "Sub" in excerpt


def test_make_excerpt_empty_text():
    assert ops.make_excerpt("") == ""


# ----------------------------- hashing -------------------------------------


def test_hash_file_deterministic(tmp_path: Path):
    p = tmp_path / "x.md"
    p.write_text("hello world", encoding="utf-8")
    h1 = ops.hash_file(p)
    h2 = ops.hash_file(p)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_hash_file_changes_with_content(tmp_path: Path):
    p = tmp_path / "x.md"
    p.write_text("v1", encoding="utf-8")
    h1 = ops.hash_file(p)
    p.write_text("v2", encoding="utf-8")
    h2 = ops.hash_file(p)
    assert h1 != h2


# ----------------------------- path helpers --------------------------------


def test_relative_wiki_path_is_posix(tmp_path: Path):
    """Even on Windows, the stored key uses forward slashes."""
    vault = tmp_path / "v"
    page = vault / "wiki" / "sub" / "page.md"
    page.parent.mkdir(parents=True)
    page.write_text("x")
    rel = ops.relative_wiki_path(vault, page)
    assert rel == "wiki/sub/page.md"


def test_venv_python_picks_correct_path_per_platform(monkeypatch, tmp_path: Path):
    """POSIX → bin/python; Windows → Scripts/python.exe."""
    vault = tmp_path / "v"
    monkeypatch.setattr(ops, "is_windows", lambda: False)
    posix = ops.venv_python(vault)
    assert posix.parts[-2:] == ("bin", "python")

    monkeypatch.setattr(ops, "is_windows", lambda: True)
    win = ops.venv_python(vault)
    assert win.parts[-2:] == ("Scripts", "python.exe")


# ----------------------------- blob round-trip ------------------------------


def test_embedding_blob_round_trip():
    """numpy array → blob → numpy array preserves bytes exactly."""
    pytest.importorskip("numpy")
    import numpy as np

    arr = np.arange(384, dtype=np.float32)
    blob = ops.embedding_to_blob(arr)
    assert isinstance(blob, (bytes, bytearray))
    assert len(blob) == 384 * 4

    restored = ops.blob_to_embedding(blob)
    assert restored.dtype == np.float32
    assert restored.shape == (384,)
    np.testing.assert_array_equal(arr, restored)


def test_embedding_blob_handles_python_list():
    """embedding_to_blob accepts a list (i.e., not pre-shaped as float32)."""
    pytest.importorskip("numpy")
    import numpy as np

    blob = ops.embedding_to_blob([1.0, 2.0, 3.0])
    arr = ops.blob_to_embedding(blob)
    assert arr.tolist() == [1.0, 2.0, 3.0]
