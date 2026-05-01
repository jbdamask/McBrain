"""End-to-end test — full lifecycle exercised through the script's CLI.

Calls the script via `python -m` style subprocess invocation so we get
the same code path a real user (and the `mcbrain-setup` skill) would take.
We skip the part of `migrate` that creates a venv and pip-installs (too slow
and requires network); the test runner's Python is assumed to already have
fastembed + numpy. The migration logic that DOES get exercised: copying
reference files, patching CLAUDE.md, building the initial index.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def run_cli(script_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script_path), *args],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def cli_vault(fresh_vault: Path, script_dir: Path) -> Path:
    """A vault prepared like a real provisioned one: CLAUDE.md patched,
    .mcbrain/bin/ populated, index.db rebuilt. Skips the venv-creation and
    pip-install steps of migrate to keep the test fast — the test runner's
    Python is already serving as the 'venv' here."""
    bin_dir = fresh_vault / ".mcbrain" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(script_dir / "mcbrain_ops.py", bin_dir / "mcbrain_ops.py")
    shutil.copy(script_dir / "schema.sql", bin_dir / "schema.sql")

    # Patch CLAUDE.md and seed the index by calling the underlying functions
    # directly from the test runner — equivalent to what the venv-resident
    # script would do, just without the subprocess hop.
    import mcbrain_ops as ops

    ops.patch_claude_md(fresh_vault)
    ops.cmd_rebuild(fresh_vault)
    return fresh_vault


def test_e2e_status_query_uninstall(cli_vault: Path, script_dir: Path, shared_embedder):
    """Full lifecycle from the CLI: status → multiple queries → uninstall."""
    script = script_dir / "mcbrain_ops.py"

    # 1. Status — provisioned, doc count matches fixture wiki count.
    result = run_cli(script, "index", "status", "--vault", str(cli_vault))
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["provisioned"] is True
    assert payload["doc_count"] == 4

    # 2. Three queries that each have a distinct expected top-1.
    expectations = [
        ("heart attack", "wiki/myocardial-infarction.md"),  # lexical winner
        ("cardiac event", "wiki/myocardial-infarction.md"),  # pure semantic
        ("how plants make food", "wiki/photosynthesis.md"),  # paraphrase
    ]
    for query_text, expected_top in expectations:
        result = run_cli(
            script, "query", query_text, "--k", "3", "--vault", str(cli_vault)
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["mode"] == "lexical+semantic"
        assert payload["results"][0]["path"] == expected_top, (
            f"query {query_text!r}: expected top-1 {expected_top}, "
            f"got {payload['results'][0]['path']}"
        )

    # 3. Uninstall (dry-run first) reports what would be deleted but doesn't.
    mcbrain_dir = cli_vault / ".mcbrain"
    result = run_cli(script, "uninstall", "--vault", str(cli_vault))
    assert result.returncode == 0
    assert "would remove" in result.stderr
    assert mcbrain_dir.exists()  # dry-run: still here

    # 4. Uninstall --force actually removes .mcbrain/, leaves wiki/CLAUDE.md alone.
    wiki = cli_vault / "wiki"
    cmd = cli_vault / "CLAUDE.md"
    wiki_files_before = sorted(p.name for p in wiki.glob("*.md"))
    cmd_size_before = cmd.stat().st_size

    result = run_cli(script, "uninstall", "--force", "--vault", str(cli_vault))
    assert result.returncode == 0
    assert "removed" in result.stderr
    assert not mcbrain_dir.exists()
    assert sorted(p.name for p in wiki.glob("*.md")) == wiki_files_before
    assert cmd.stat().st_size == cmd_size_before


def test_e2e_query_after_sync_picks_up_new_file(cli_vault: Path, script_dir: Path):
    """Add a wiki file, run sync, query for it — the new file should rank."""
    script = script_dir / "mcbrain_ops.py"
    new_page = cli_vault / "wiki" / "quantum-entanglement.md"
    new_page.write_text(
        "# Quantum Entanglement\n\nTwo particles share a state — measuring one "
        "instantly determines the other regardless of distance.\n",
        encoding="utf-8",
    )

    result = run_cli(script, "index", "sync", "--vault", str(cli_vault))
    assert result.returncode == 0, result.stderr

    result = run_cli(
        script,
        "query",
        "spooky action at a distance",
        "--k",
        "1",
        "--vault",
        str(cli_vault),
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["results"][0]["path"] == "wiki/quantum-entanglement.md"


def test_e2e_missing_vault_arg_exits_with_clear_error(script_dir: Path):
    """No --vault and no MCBRAIN_VAULT → exit 2 with a clear message."""
    script = script_dir / "mcbrain_ops.py"
    result = subprocess.run(
        [sys.executable, str(script), "index", "status"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},  # strip MCBRAIN_VAULT
    )
    assert result.returncode == 2
    assert "vault" in result.stderr.lower()
