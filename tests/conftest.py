"""Shared pytest fixtures for the mcbrain-ops test suite.

Design notes:

- The script under test (`mcbrain_ops.py`) lives at
  `plugins/mcbrain/skills/mcbrain-ops/references/`. We add that directory to
  sys.path so tests can `import mcbrain_ops` directly without installing it
  as a package.
- `mcbrain_ops` does lazy imports of fastembed/numpy inside subcommand
  handlers, so `import mcbrain_ops` succeeds even in a Python without those
  deps. Unit tests that exercise pure-Python logic don't need them.
- Integration tests that exercise indexing/search DO need fastembed + numpy
  in the test runner's Python. The session-scoped `shared_embedder` fixture
  loads the FastEmbed model exactly once per test session — the model load
  is the dominant cost (~500 ms), so amortizing it matters.
- Fastembed's model weights are cached at `~/.cache/fastembed/` (the library
  default). Re-running the suite is fast after the first run.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = (
    REPO_ROOT / "plugins" / "mcbrain" / "skills" / "mcbrain-ops" / "references"
)
FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Make `import mcbrain_ops` work in every test module.
sys.path.insert(0, str(SCRIPT_DIR))


@pytest.fixture(scope="session")
def script_dir() -> Path:
    """Directory containing mcbrain_ops.py and its sibling reference files."""
    return SCRIPT_DIR


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Directory holding test input markdown files."""
    return FIXTURES_DIR


@pytest.fixture
def fresh_vault(tmp_path: Path) -> Path:
    """A vault with sample wiki content + a legacy CLAUDE.md, NO .mcbrain/.

    Use this for tests that need a clean slate (e.g., migrate testing,
    no-index fallback testing).
    """
    vault = tmp_path / "vault"
    (vault / "wiki").mkdir(parents=True)
    for md in (FIXTURES_DIR / "wiki").glob("*.md"):
        shutil.copy(md, vault / "wiki" / md.name)
    (vault / "CLAUDE.md").write_text(
        (FIXTURES_DIR / "claude_md_legacy.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return vault


@pytest.fixture
def vault_with_schema(fresh_vault: Path) -> Path:
    """fresh_vault + .mcbrain/bin/schema.sql in place but no DB yet.

    Use this for tests that exercise index init / rebuild / sync directly
    without going through the full migrate (which would create a venv and
    pip-install — too slow for unit-grade tests).
    """
    bin_dir = fresh_vault / ".mcbrain" / "bin"
    bin_dir.mkdir(parents=True)
    shutil.copy(SCRIPT_DIR / "schema.sql", bin_dir / "schema.sql")
    return fresh_vault


@pytest.fixture(scope="session")
def shared_embedder():
    """Session-scoped FastEmbed instance.

    The first call to mcbrain_ops.get_embedder() triggers the model download
    (if not already cached) and instantiation. Subsequent calls during the
    same session reuse the cached singleton inside the module.
    """
    pytest.importorskip("fastembed")
    pytest.importorskip("numpy")
    import mcbrain_ops

    return mcbrain_ops.get_embedder()


@pytest.fixture
def populated_vault(vault_with_schema: Path, shared_embedder) -> Path:
    """vault_with_schema with a fresh index built from the wiki content."""
    import mcbrain_ops

    mcbrain_ops.cmd_rebuild(vault_with_schema)
    return vault_with_schema
