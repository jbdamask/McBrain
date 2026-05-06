"""Tests for the local research-tracker backend.

Coverage:

1. ``test_jsonl_schema_documented`` — both new SKILL.md files mention every
   required JSONL row field, so a contributor changing the schema can't drift
   the docs without the test catching it.
2. ``test_runner_claim_then_findings_round_trip`` — exercise the canonical
   protocol (acquire → read → mutate → write+replace → release) end-to-end
   against a fixture file and confirm the resulting JSONL matches expectations.
3. ``test_jsonl_atomic_concurrent_writers`` — the **mandatory** concurrency
   test. Five subprocesses each try to claim 3 rows from a 30-row fixture
   following the same protocol. Asserts no double-claims, no lost claims,
   valid JSONL afterwards, and that the lock file is gone after clean exit.
4. ``test_claude_md_template_has_research_tracker_section`` — the template
   ships the unified ``## Research tracker`` section with the right anchors.

The protocol is documented at
``plugins/mcbrain/skills/local-research-runner/references/atomic_write_protocol.md``
— this file's helpers must stay in sync with that doc.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "mcbrain" / "skills"
LOCAL_DB_SKILL = PLUGIN_ROOT / "local-research-db" / "SKILL.md"
LOCAL_RUNNER_SKILL = PLUGIN_ROOT / "local-research-runner" / "SKILL.md"
CLAUDE_MD_TEMPLATE = (
    PLUGIN_ROOT / "mcbrain-setup" / "references" / "claude-md-template.md"
)

JSONL_REQUIRED_FIELDS = [
    "id",
    "topic",
    "topic_slug",
    "task_name",
    "status",
    "priority",
    "created_date",
    "last_updated_date",
    "notes",
    "notes_path",
    "sources_count",
]


# ---------------------------------------------------------------------------
# Reference protocol implementation (shared by tests 2 and 3)
#
# Mirrors atomic_write_protocol.md. The runner SKILL tells the agent to
# implement this at runtime; we implement it here so tests can verify the
# protocol's invariants directly.
# ---------------------------------------------------------------------------

ACQUIRE_TIMEOUT_SECONDS = 5.0
ACQUIRE_POLL_SECONDS = 0.05


class LockTimeout(RuntimeError):
    pass


def _lock_path(tasks_path: Path) -> Path:
    return tasks_path.with_suffix(tasks_path.suffix + ".lock")


def acquire_lock(
    tasks_path: Path,
    timeout: float = ACQUIRE_TIMEOUT_SECONDS,
    poll: float = ACQUIRE_POLL_SECONDS,
) -> None:
    """Block until exclusive ownership of ``tasks_path``'s lock file.

    Implements the protocol: ``os.open`` with ``O_CREAT | O_EXCL | O_WRONLY``
    is atomic on POSIX *and* Windows. Caller must pair every successful
    ``acquire_lock`` with ``release_lock`` in a ``try/finally``.
    """
    lockfile = _lock_path(tasks_path)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            fd = os.open(
                str(lockfile),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
            os.close(fd)
            return
        except FileExistsError:
            time.sleep(poll)
    raise LockTimeout(
        f"Could not acquire {lockfile} after {timeout}s. "
        "If you are sure no other writer is active, delete the .lock file "
        "manually and retry."
    )


def release_lock(tasks_path: Path) -> None:
    try:
        os.unlink(_lock_path(tasks_path))
    except FileNotFoundError:
        pass


def read_jsonl(tasks_path: Path) -> list[dict]:
    if not tasks_path.exists():
        return []
    rows: list[dict] = []
    for line in tasks_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def write_jsonl_atomic(tasks_path: Path, rows: list[dict]) -> None:
    """Serialize ``rows`` to a temp file then ``os.replace`` over ``tasks_path``.

    ``os.replace`` is atomic on POSIX (``rename(2)``) and Windows
    (``MoveFileExW`` with ``MOVEFILE_REPLACE_EXISTING``).
    """
    parent = tasks_path.parent
    fd, tmp_str = tempfile.mkstemp(
        prefix=tasks_path.name + ".",
        suffix=".tmp",
        dir=str(parent),
    )
    tmp_path = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp_path), str(tasks_path))
    except Exception:
        # Clean up temp file on any failure; leave tasks_path untouched.
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Test 1 — schema is documented in both new SKILL.md files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "skill_path,label",
    [
        (LOCAL_DB_SKILL, "local-research-db"),
        (LOCAL_RUNNER_SKILL, "local-research-runner"),
    ],
)
def test_jsonl_schema_documented(skill_path: Path, label: str) -> None:
    assert skill_path.exists(), f"{label} SKILL.md missing at {skill_path}"
    text = skill_path.read_text(encoding="utf-8")
    missing = [field for field in JSONL_REQUIRED_FIELDS if field not in text]
    assert not missing, (
        f"{label} SKILL.md does not mention required JSONL fields: {missing}. "
        "Schema is drifting from docs. Update the SKILL or the test."
    )


# ---------------------------------------------------------------------------
# Test 2 — single-process Phase 2 + Phase 4 round trip
# ---------------------------------------------------------------------------


def _make_fixture_rows(n: int, topic_slug: str = "demo") -> list[dict]:
    """Generate ``n`` synthetic ``To do`` rows for the same topic."""
    base_iso = "2026-01-01T00:00:00+00:00"
    return [
        {
            "id": f"task-{i:03d}",
            "topic": "Demo Topic",
            "topic_slug": topic_slug,
            "task_name": f"Task {i}",
            "status": "To do",
            "priority": ["High", "Medium", "Low"][i % 3],
            "created_date": base_iso,
            "last_updated_date": base_iso,
            "notes": "",
            "notes_path": "",
            "sources_count": None,
        }
        for i in range(n)
    ]


def test_runner_claim_then_findings_round_trip(tmp_path: Path) -> None:
    tasks_path = tmp_path / "tasks.jsonl"
    fixture = _make_fixture_rows(10)
    tasks_path.write_text(
        "\n".join(json.dumps(r) for r in fixture) + "\n", encoding="utf-8"
    )
    original_timestamps = {r["id"]: r["last_updated_date"] for r in fixture}

    # Phase 2 — claim 3 rows under one lock cycle.
    acquire_lock(tasks_path)
    try:
        rows = read_jsonl(tasks_path)
        assert len(rows) == 10
        to_claim = [r for r in rows if r["status"] == "To do"][:3]
        assert len(to_claim) == 3
        claimed_ids = {r["id"] for r in to_claim}
        for r in rows:
            if r["id"] in claimed_ids:
                r["status"] = "In progress"
                r["last_updated_date"] = now_iso()
        write_jsonl_atomic(tasks_path, rows)
    finally:
        release_lock(tasks_path)

    # No lock file should remain after clean release.
    assert not _lock_path(tasks_path).exists()

    after_claim = read_jsonl(tasks_path)
    in_progress = [r for r in after_claim if r["status"] == "In progress"]
    assert len(in_progress) == 3
    assert {r["id"] for r in in_progress} == claimed_ids
    # last_updated_date was bumped on claimed rows but not others.
    for r in after_claim:
        if r["id"] in claimed_ids:
            assert r["last_updated_date"] != original_timestamps[r["id"]]
        else:
            assert r["last_updated_date"] == original_timestamps[r["id"]]

    # Total row count preserved.
    assert len(after_claim) == 10

    # Phase 4 — write findings and flip to Done, one task per lock cycle.
    for cid in sorted(claimed_ids):
        notes_path = f"raw/notes/research-demo-{cid}.md"
        acquire_lock(tasks_path)
        try:
            rows = read_jsonl(tasks_path)
            for r in rows:
                if r["id"] == cid:
                    r["notes_path"] = notes_path
                    r["sources_count"] = 4
                    r["status"] = "Done"
                    r["last_updated_date"] = now_iso()
                    break
            write_jsonl_atomic(tasks_path, rows)
        finally:
            release_lock(tasks_path)
        assert not _lock_path(tasks_path).exists()

    # Final state: 3 Done rows with notes_path set, 7 To do rows untouched.
    final = read_jsonl(tasks_path)
    assert len(final) == 10
    done = [r for r in final if r["status"] == "Done"]
    todo = [r for r in final if r["status"] == "To do"]
    assert len(done) == 3
    assert len(todo) == 7
    for r in done:
        assert r["id"] in claimed_ids
        assert r["notes_path"].startswith("raw/notes/research-demo-")
        assert r["sources_count"] == 4
    for r in todo:
        assert r["notes_path"] == ""


# ---------------------------------------------------------------------------
# Test 3 — MANDATORY concurrency test
# ---------------------------------------------------------------------------


# Worker script run by each subprocess. Self-contained so the subprocess
# does not need access to the test file's helpers — keeps the test simple
# and matches what the runner SKILL would generate at runtime.
_WORKER_SCRIPT = textwrap.dedent('''\
    """One subprocess in test_jsonl_atomic_concurrent_writers.

    Tries to claim ``WANTED`` rows from ``TASKS_PATH``. Re-runs the
    acquire/read/mutate/write/release cycle until either WANTED rows have
    been claimed or no more "To do" rows remain.

    Implements the protocol from atomic_write_protocol.md inline.
    """
    import json
    import os
    import sys
    import tempfile
    import time
    from datetime import datetime, timezone
    from pathlib import Path

    TASKS_PATH = Path(sys.argv[1])
    WANTED = int(sys.argv[2])
    WORKER_ID = sys.argv[3]
    LOCK_PATH = TASKS_PATH.with_suffix(TASKS_PATH.suffix + ".lock")

    def acquire():
        deadline = time.monotonic() + 30.0  # generous so contention does
                                             # not flake the test
        while time.monotonic() < deadline:
            try:
                fd = os.open(
                    str(LOCK_PATH),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644,
                )
                os.close(fd)
                return
            except FileExistsError:
                time.sleep(0.01)
        raise SystemExit(f"worker {WORKER_ID}: lock acquire timeout")

    def release():
        try:
            os.unlink(str(LOCK_PATH))
        except FileNotFoundError:
            pass

    def read_rows():
        if not TASKS_PATH.exists():
            return []
        out = []
        for line in TASKS_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def write_atomic(rows):
        fd, tmp = tempfile.mkstemp(
            prefix=TASKS_PATH.name + ".",
            suffix=".tmp",
            dir=str(TASKS_PATH.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, str(TASKS_PATH))
        except Exception:
            try:
                Path(tmp).unlink()
            except FileNotFoundError:
                pass
            raise

    claimed_ids = []
    while len(claimed_ids) < WANTED:
        acquire()
        try:
            rows = read_rows()
            todos = [r for r in rows if r["status"] == "To do"]
            if not todos:
                break
            take = min(WANTED - len(claimed_ids), len(todos))
            picks = todos[:take]
            pick_ids = {r["id"] for r in picks}
            now_iso = datetime.now(timezone.utc).isoformat()
            for r in rows:
                if r["id"] in pick_ids:
                    r["status"] = "In progress"
                    r["claimed_by"] = WORKER_ID
                    r["last_updated_date"] = now_iso
            write_atomic(rows)
            claimed_ids.extend(sorted(pick_ids))
        finally:
            release()

    print(json.dumps({"worker": WORKER_ID, "claimed": claimed_ids}))
''')


def test_jsonl_atomic_concurrent_writers(tmp_path: Path) -> None:
    """Five subprocesses, 30 To-do rows, each wants 3 — no double-claims."""
    n_workers = 5
    rows_per_worker = 3
    fixture_size = 30
    tasks_path = tmp_path / "tasks.jsonl"
    fixture = _make_fixture_rows(fixture_size)
    tasks_path.write_text(
        "\n".join(json.dumps(r) for r in fixture) + "\n", encoding="utf-8"
    )
    worker_path = tmp_path / "worker.py"
    worker_path.write_text(_WORKER_SCRIPT, encoding="utf-8")

    procs = [
        subprocess.Popen(
            [
                sys.executable,
                str(worker_path),
                str(tasks_path),
                str(rows_per_worker),
                f"w{i}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(n_workers)
    ]
    outs = []
    for p in procs:
        stdout, stderr = p.communicate(timeout=60)
        assert p.returncode == 0, (
            f"worker exited {p.returncode}; stderr=\n{stderr}\n"
            f"stdout=\n{stdout}"
        )
        outs.append(json.loads(stdout.strip().splitlines()[-1]))

    # Each worker reports its claimed IDs. Across all workers, no ID should
    # appear more than once (assertion 1).
    all_claimed = []
    for o in outs:
        all_claimed.extend(o["claimed"])
    assert len(all_claimed) == n_workers * rows_per_worker, (
        f"Expected {n_workers * rows_per_worker} total claims, got "
        f"{len(all_claimed)}: {all_claimed}"
    )
    assert len(set(all_claimed)) == len(all_claimed), (
        f"Double-claim detected: {sorted(all_claimed)}"
    )

    # File must still be valid JSONL with the original row count
    # (assertions 2 + 3).
    final = []
    for line in tasks_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            final.append(json.loads(line))
    assert len(final) == fixture_size

    # Every claimed row was bumped past the fixture's original timestamp
    # (assertion 4).
    original_ts = fixture[0]["last_updated_date"]
    in_progress = [r for r in final if r["status"] == "In progress"]
    assert len(in_progress) == n_workers * rows_per_worker
    for r in in_progress:
        assert r["last_updated_date"] > original_ts, (
            f"Row {r['id']} was claimed but its timestamp was not bumped: "
            f"{r['last_updated_date']!r} vs original {original_ts!r}"
        )

    # Lock file must NOT exist after every worker has cleanly released
    # (assertion 5).
    assert not _lock_path(tasks_path).exists(), (
        "Lock file left behind after all workers exited cleanly — "
        "release_lock is broken or a worker crashed."
    )


# ---------------------------------------------------------------------------
# Test 4 — CLAUDE.md template carries the unified Research tracker section
# ---------------------------------------------------------------------------


def test_claude_md_template_has_research_tracker_section() -> None:
    text = CLAUDE_MD_TEMPLATE.read_text(encoding="utf-8")
    assert "## Research tracker" in text, (
        "claude-md-template.md is missing the unified ## Research tracker "
        "section. Did the migration drop it?"
    )
    # The section's contract: a Backend: line and (when local) a File: line.
    assert "Backend:" in text, (
        "## Research tracker section is missing the Backend: line."
    )
    assert "File: raw/research_tasks/tasks.jsonl" in text, (
        "## Research tracker section is missing the local backend's "
        "File: anchor."
    )
    # Legacy section name should still be grep-matchable for back-compat
    # readers that don't yet know about ## Research tracker.
    assert "## Notion companion databases" in text, (
        "Legacy ## Notion companion databases reference is gone — older "
        "vaults that haven't migrated will lose their fallback path. "
        "Keep at least a back-compat reference."
    )
