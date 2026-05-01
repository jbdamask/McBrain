"""Static / smoke tests — the floor of the Testing Trophy.

These run without fastembed or numpy and catch the cheapest possible class
of regression: the module won't import, or argparse is broken.
"""
from __future__ import annotations

import subprocess
import sys

import mcbrain_ops


def test_module_imports():
    """The script imports without triggering heavy deps."""
    assert hasattr(mcbrain_ops, "main")
    assert hasattr(mcbrain_ops, "build_parser")


def test_help_lists_all_subcommands(script_dir):
    """The top-level --help mentions every subcommand."""
    result = subprocess.run(
        [sys.executable, str(script_dir / "mcbrain_ops.py"), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for cmd in ("query", "index", "migrate", "uninstall"):
        assert cmd in out, f"{cmd} missing from --help"


def test_index_subcommands_listed(script_dir):
    result = subprocess.run(
        [sys.executable, str(script_dir / "mcbrain_ops.py"), "index", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    for op in ("sync", "rebuild", "status"):
        assert op in result.stdout, f"{op} missing from index --help"


def test_constants_match_schema():
    """Constants in the script must agree with what's encoded in the schema."""
    assert mcbrain_ops.EMBEDDING_DIM == 384
    assert mcbrain_ops.EMBEDDING_MODEL == "BAAI/bge-small-en-v1.5"
    assert mcbrain_ops.SCHEMA_VERSION == "1"


def test_no_unbounded_text_select_in_query(script_dir):
    """Regression test for the lazy-text-fetch optimization (bead McBrain-rtp).

    The query subcommand must never SELECT chunks.text without a WHERE
    clause — that's the bug we explicitly fixed. Grep the source.
    """
    src = (script_dir / "mcbrain_ops.py").read_text(encoding="utf-8")
    assert "SELECT path, text FROM chunks WHERE" in src, (
        "expected the lazy-fetch SELECT (with WHERE) to be present"
    )
    # Look for any SELECT that pulls text without a WHERE clause. Naive but
    # catches the regression we care about.
    assert "SELECT path, text FROM chunks\"" not in src
    assert "SELECT path, text FROM chunks " not in src.replace(
        "SELECT path, text FROM chunks WHERE", ""
    )
