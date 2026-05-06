# Atomic Write Protocol for `tasks.jsonl`

This protocol governs every modification of `<vault>/raw/research_tasks/tasks.jsonl` made by the `local-research-runner` skill (or any other skill that writes to that file). Follow it verbatim. Skipping it risks lost claims and corrupted JSONL when more than one runner — or future parallel-agent architecture — touches the file.

## Constraints

- **Zero-dependency.** Uses only Python 3 standard library (`os`, `json`, `tempfile`, `time`, `datetime`). No third-party packages, no external CLI tools (no `flock(1)`, no `lockfile`, no `filelock`).
- **Cross-platform.** Works identically on macOS, Linux, and Windows. Uses only Python primitives that are platform-agnostic: `os.open` with `O_CREAT | O_EXCL`, `os.replace`, `os.fsync`, `tempfile.mkstemp`. No `fcntl`, no POSIX-only signals, no shell tools.
- **Sub-second cycles.** Each acquire → read → mutate → write → release cycle must complete in milliseconds. The lock is never held across long operations (e.g. spawning research subagents) — see the runner SKILL.md for how this is enforced at the phase level.

## Approach: exclusive-create lock file + write-temp-then-`os.replace`

Two stdlib primitives, both fully cross-platform:

1. **Exclusive-create lock file** at `<vault>/raw/research_tasks/tasks.jsonl.lock`, opened with `os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)`. This call is atomic on POSIX *and* on Windows — exactly one caller wins the race; everyone else gets `FileExistsError`. The file's existence *is* the lock; its contents are unimportant. Releasing the lock = `os.unlink(lockfile)`.
2. **Write-temp-then-`os.replace`** on the same filesystem. `os.replace` is atomic on both POSIX (calls `rename(2)`) and Windows (calls `MoveFileExW` with `MOVEFILE_REPLACE_EXISTING`). It's the right primitive — `os.rename` would fail on Windows when the destination already exists. Any reader sees either the old file or the new file, never a partial one.

### Why both

- The lock alone would not protect a reader that does not take the lock — a user running `cat tasks.jsonl` (or `type` on Windows) mid-write could see a half-written file.
- `os.replace` alone would not prevent two runners from both reading, both modifying, and both writing — last-writer-wins, lost updates. The lock serializes read-modify-write.

### Why not other approaches

- **`fcntl.flock` / `flock(1)` CLI.** `fcntl` is POSIX-only — not available on Windows Python. `flock(1)` the CLI is GNU/Linux-only. Both are non-starters for a cross-platform skill.
- **`msvcrt.locking` / `LockFileEx`.** Windows-only counterpart to `fcntl`. Forces platform-branched code for no real benefit over the exclusive-create pattern, which works everywhere.
- **TTL-based stale-lock reclaim.** Not needed given how short a real cycle is (sub-second). A TTL adds a knob (how long? what if a slow disk?) and an extra failure mode (reclaiming a still-live but slow lock). Simpler answer: short acquire timeout + clear error message + manual cleanup on the rare crash, see *Stuck-lock recovery* below.
- **PID liveness probing (`os.kill(pid, 0)`).** Reliable on POSIX, but Windows semantics differ. Skipping liveness probes entirely is simpler.
- **In-place edit with `O_APPEND`.** Works for pure appends, but status flips and findings updates are read-modify-rewrite, not append.
- **SQLite with WAL.** Solid, but breaks the "just a JSONL the user can `cat`" property that's the whole point of the local backend.
- **Third-party `filelock` / `portalocker` packages.** Add a pip install. Not worth the dependency for a single sidecar file.

## Concrete protocol

The runner imports a single small helper (defined inside the skill, no external deps) that wraps this loop. Pseudocode — implementations must match it exactly:

```
ACQUIRE_TIMEOUT_SECONDS = 5     # short — real cycles are sub-second

ACQUIRE_LOCK(lockfile, timeout=ACQUIRE_TIMEOUT_SECONDS, poll=0.1s):
  deadline = now() + timeout
  while now() < deadline:
    try:
      fd = os.open(lockfile, O_CREAT | O_EXCL | O_WRONLY, 0o644)
      os.close(fd)                # empty file is fine; existence == lock held
      return
    except FileExistsError:
      sleep(poll)
  raise LockTimeout(
    "Could not acquire <vault>/raw/research_tasks/tasks.jsonl.lock "
    "after 5s. Another runner is likely active. If you are sure no "
    "other runner is running, delete the .lock file manually and retry."
  )

READ:
  read entire tasks.jsonl into memory as a list of dict rows
  (if the file does not exist, treat as empty list)

MODIFY:
  apply changes in memory (claim N rows, set notes_path, etc.)
  bump last_updated_date on every modified row

WRITE:
  fd, tmp_path = tempfile.mkstemp(
      prefix='tasks.jsonl.', suffix='.tmp',
      dir=<vault>/raw/research_tasks)
  serialize all rows as JSONL into fd
  os.fsync(fd); os.close(fd)
  os.replace(tmp_path, tasks.jsonl)       # atomic on POSIX and Windows

RELEASE_LOCK:
  try: os.unlink(lockfile)
  except FileNotFoundError: pass
```

Wrap acquire/release in `try/finally` so the lock file is removed on every exit path, including subagent failures and exceptions.

## Stuck-lock recovery

If a runner crashes (force-killed, power loss) between creating the lock file and unlinking it, the lock is left orphaned on disk. There is **no automatic reclaim** — the next runner's `ACQUIRE_LOCK` will time out after 5 seconds and surface a clear error message instructing the user to delete `tasks.jsonl.lock` manually after confirming no other runner is active. This is deliberate: lock cycles are sub-second, so a held lock that lasts more than 5 seconds is a strong signal that something is wrong, not that something is just slow.

## What this protects vs. does NOT protect

- ✅ Two runners (or two parallel agents in a future architecture) claiming tasks at the same time — serialized by the exclusive-create lock, no double-claim. Works identically on macOS, Linux, and Windows.
- ✅ Runner killed mid-write — `tasks.jsonl` is untouched (only the orphaned temp file might exist; safe to leave or delete on next run).
- ✅ Runner killed mid-lock — next runner times out after 5 s and gives the user a clear "delete the .lock file" instruction. Manual one-step recovery; no automatic reclaim.
- ✅ Naive reader (`cat`/`type tasks.jsonl`) during a write — sees old or new file via `os.replace` atomicity.
- ⚠️ Network-mounted filesystems where `rename`/`O_EXCL` semantics aren't honored (rare on a single-user laptop vault). On a vault stored in a Google Drive / iCloud / OneDrive sync folder, the local-FS operation is still atomic — the cloud sync layer replicates the new file as-is.
- ⚠️ Windows file-share modes: in rare cases another process holding `tasks.jsonl` open without `FILE_SHARE_DELETE` could block `os.replace`. In practice Notepad / VS Code / `type` use share modes that allow replacement. Document as a known edge case; recommend closing editors that have the file open before writing.
- ❌ Two runners on different machines pointing at the same vault path. Out of scope — the runner SKILL's prerequisites say "single machine".
