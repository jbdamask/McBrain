"""Patcher tests — CLAUDE.md text manipulation.

The CLAUDE.md patcher (`patch_claude_md` and helpers) is regex-heavy text
mangling. The tests here are deliberately exhaustive because this is exactly
the kind of code that breaks silently on inputs the implementer didn't
anticipate.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import mcbrain_ops as ops


# ------------------------- _find_next_heading -------------------------------


def test_find_next_heading_h2():
    text = "## Foo\n\nbody\n## Bar\n"
    # Search starting from after "## Foo"
    idx = ops._find_next_heading(text, len("## Foo"))
    assert text[idx : idx + 7] == "## Bar\n"


def test_find_next_heading_h4():
    text = "## Foo\n\nbody\n#### Bar\n"
    idx = ops._find_next_heading(text, len("## Foo"))
    assert text[idx : idx + 9] == "#### Bar\n"


def test_find_next_heading_at_eof():
    """Returns len(text) when no further heading exists."""
    text = "## Foo\n\nbody only, no further heading"
    idx = ops._find_next_heading(text, len("## Foo"))
    assert idx == len(text)


def test_find_next_heading_skips_in_paragraph():
    """A '#' that's not at the start of a line is not a heading."""
    text = "## Foo\n\nA paragraph mentioning #hashtag inline.\n## Bar\n"
    idx = ops._find_next_heading(text, len("## Foo"))
    assert text[idx : idx + 7] == "## Bar\n"


# ------------------------- patch_claude_md (legacy) -------------------------


def test_patch_legacy_template_adds_query_engine_section(fresh_vault: Path):
    """A vault without ## Query engine gets the section appended."""
    cmd_path = fresh_vault / "CLAUDE.md"
    assert "## Query engine" not in cmd_path.read_text(encoding="utf-8")

    changed = ops.patch_claude_md(fresh_vault)
    assert changed is True

    after = cmd_path.read_text(encoding="utf-8")
    assert "## Query engine" in after
    assert "embedding_model: BAAI/bge-small-en-v1.5" in after
    assert "embedding_dim: 384" in after
    assert "index_path: .mcbrain/index.db" in after


def test_patch_legacy_rewrites_query_operation(fresh_vault: Path):
    """The legacy ### Query block is replaced with the mcbrain-ops version."""
    ops.patch_claude_md(fresh_vault)
    after = (fresh_vault / "CLAUDE.md").read_text(encoding="utf-8")
    # Old retrieval mechanism is gone.
    assert "Read `wiki/index.md` to find relevant pages" not in after
    # New delegation is in.
    assert "Invoke the `mcbrain-ops` skill" in after
    assert 'query "<text>"' in after


def test_patch_preserves_lint_section(fresh_vault: Path):
    """Replacing ### Query must not consume the following ### Lint sibling."""
    ops.patch_claude_md(fresh_vault)
    after = (fresh_vault / "CLAUDE.md").read_text(encoding="utf-8")
    assert "### Lint" in after
    assert "Sample wiki pages" in after


def test_patch_handles_h4_after_query(tmp_path: Path, fixtures_dir: Path):
    """Regression: H4 (####) immediately following ### Query was being swallowed."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "CLAUDE.md").write_text(
        (fixtures_dir / "claude_md_h4_after_query.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    ops.patch_claude_md(vault)
    after = (vault / "CLAUDE.md").read_text(encoding="utf-8")
    assert "#### Notes about the Query operation" in after
    assert "This subsection must survive the patcher." in after


# ------------------------- patch_claude_md (idempotency) --------------------


def test_patch_is_idempotent(fresh_vault: Path):
    """Running the patcher twice is a no-op the second time."""
    first_changed = ops.patch_claude_md(fresh_vault)
    after_first = (fresh_vault / "CLAUDE.md").read_text(encoding="utf-8")

    second_changed = ops.patch_claude_md(fresh_vault)
    after_second = (fresh_vault / "CLAUDE.md").read_text(encoding="utf-8")

    assert first_changed is True
    assert second_changed is False
    assert after_first == after_second


def test_patch_skips_when_query_engine_present_and_query_already_rewritten(
    fresh_vault: Path,
):
    """If an upgrade has already happened, second call returns False without
    altering the file even by a whitespace."""
    ops.patch_claude_md(fresh_vault)
    snapshot = (fresh_vault / "CLAUDE.md").read_text(encoding="utf-8")

    changed = ops.patch_claude_md(fresh_vault)
    assert changed is False
    assert (fresh_vault / "CLAUDE.md").read_text(encoding="utf-8") == snapshot


# ------------------------- patch_claude_md (missing CLAUDE.md) --------------


def test_patch_skips_silently_when_no_claude_md(tmp_path: Path):
    """A vault with no CLAUDE.md returns False rather than crashing."""
    vault = tmp_path / "no_cmd"
    vault.mkdir()
    assert ops.patch_claude_md(vault) is False


# ------------------------- _ensure_index_sync_tail --------------------------


def test_index_sync_tail_autonumbers_after_max(fresh_vault: Path):
    """The new step is numbered max(existing) + 1."""
    ops.patch_claude_md(fresh_vault)
    after = (fresh_vault / "CLAUDE.md").read_text(encoding="utf-8")
    # The legacy fixture's Ingest from raw/ has steps 1..6, so the new step
    # must be `7. Invoke the `mcbrain-ops` skill (`index sync`)...`
    assert "7. Invoke the `mcbrain-ops` skill (`index sync`)" in after


def test_index_sync_tail_skipped_if_already_present(fresh_vault: Path):
    """Once the tail is in, a second pass leaves it alone."""
    ops.patch_claude_md(fresh_vault)
    after_first = (fresh_vault / "CLAUDE.md").read_text(encoding="utf-8")
    count_first = after_first.count("(`index sync`)")
    ops.patch_claude_md(fresh_vault)
    after_second = (fresh_vault / "CLAUDE.md").read_text(encoding="utf-8")
    count_second = after_second.count("(`index sync`)")
    assert count_first == count_second  # not duplicated


def test_index_sync_tail_uses_bullet_when_no_numbered_list():
    """If Ingest from raw/ doesn't have a numbered list, fall back to a bullet."""
    text = (
        "## Operations\n\n"
        "#### Ingest from raw/\n\n"
        "Just a paragraph, no numbered steps.\n\n"
        "### Lint\nfoo\n"
    )
    out = ops._ensure_index_sync_tail(text)
    # New line should be a hyphen-bullet, not a numbered step.
    assert "- Invoke the `mcbrain-ops` skill" in out
    assert "1. Invoke" not in out


def test_index_sync_tail_no_op_when_section_missing():
    """If there's no `#### Ingest from raw/` section at all, return text unchanged."""
    text = "## Backup\n\nStrategy: none.\n"
    assert ops._ensure_index_sync_tail(text) == text


# ------------------------- _replace_query_section ---------------------------


def test_replace_query_section_no_op_when_already_migrated():
    """Already contains 'mcbrain-ops' inside the Query block? Skip."""
    text = (
        "## Operations\n\n"
        "### Query\nInvoke the `mcbrain-ops` skill (`query`).\n\n"
        "### Lint\nfoo\n"
    )
    assert ops._replace_query_section(text) == text


def test_replace_query_section_no_op_when_no_query_block():
    text = "## Operations\n\n### Lint\nfoo\n"
    assert ops._replace_query_section(text) == text
