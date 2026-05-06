# Plan: Add option for local research tracker (issue #16)

## Context

McBrain currently relies on Notion for tracking research tasks via two skills:

- `notion-research-db` — creates a Notion DB with the tracker schema (Task, Status, Priority, dates, Notes), then registers it in the vault's `CLAUDE.md` under `## Notion companion databases`.
- `notion-research-runner` — drains "To do" rows, spawns research subagents, and writes findings back to each Notion page. Output is then ingested into the wiki via the `mcbrain-engine` MCP's `ingest_from_notion` tool.

This works well for users who have Notion **and** admin rights to create Notion Connections + integration tokens. For everyone else it's friction. Issue #16 asks for a second option: store research tasks as a local JSONL file inside the vault, with the same schema, and add two parallel skills (`local-research-db`, `local-research-runner`) that mirror the Notion ones. The chosen backend is registered in the vault's `CLAUDE.md` so downstream skills know which one to use. The Notion path remains the default; the new option is additive.

## Recommended Approach

1. **One flat JSONL file per vault.** A vault has exactly one research-tracker file: `<vault>/raw/research_tasks/tasks.jsonl`. One JSON object per line, append-only with in-place atomic rewrites for status changes. Topic is a field on each record (`topic`, `topic_slug`), not a folder. Nothing else (no `README.md`, no `findings/`, no per-topic dirs) lives in `raw/research_tasks/`.
2. **Schema parity with Notion, plus two file-pointer fields.** Each task object has `id`, `topic`, `topic_slug`, `task_name`, `status` (`To do` | `In progress` | `Done`), `priority` (`High` | `Medium` | `Low`), `created_date`, `last_updated_date` (ISO-8601), `notes` (string), `notes_path` (relative path to the markdown file under `raw/notes/`, set by the runner once findings are written), and `sources_count` (integer, optional, set when findings are written). The full findings markdown body and Sources section live in the markdown file at `notes_path`, **not** inline in the JSONL row. This keeps the JSONL small and makes the file fast to re-read on every runner invocation.
3. **CLAUDE.md is the source of truth for the chosen backend.** Add a new top-level section `## Research tracker` containing a `Backend:` line whose value is `notion`, `local`, or `none`, plus per-backend metadata (Notion DB list as today, or local file path for local). Both `mcbrain-setup` (creation time) and the user (later, by re-running the setup skill or by hand) can write this section. Downstream skills (the `mcbrain` skill, the new local skills) read it on every invocation.
4. **Add two new skills bundled with the plugin.** `local-research-db` initializes the single `tasks.jsonl` file (or registers an additional topic on an already-initialized vault) and updates `CLAUDE.md`. `local-research-runner` drains up to 5 `To do` rows, claims them via the atomic protocol, spawns research subagents, writes one markdown file per task to `raw/notes/`, and updates each row with `notes_path` plus `status: Done`. They live next to `notion-research-db` and `notion-research-runner` under `plugins/mcbrain/skills/` so they ship with the plugin and need no per-user install.
5. **Hook `mcbrain-setup` into the choice.** Step 8 today is "Notion companion database (optional)". Generalize it: ask `Backend?` first (`local` / `notion` / `none`), then run the existing 8a–8g flow only on the Notion branch and a new lightweight 8L flow on the local branch (touch `tasks.jsonl`, write the CLAUDE.md section, no token, no MCP calls). Default-recommend `local` because it has zero dependencies; users who want Notion can pick it.
6. **No new ingest mode.** Findings written to `raw/notes/*.md` are picked up by the **existing standard ingest** flow without any routing changes. The `mcbrain` skill stays at two ingest modes (Notion-bridged and standard); nothing new there. The runner sets `status: Done` itself once the markdown file is written, so there's no "close the loop later" step in ingest.
7. **Backwards compatibility.** Vaults that already exist with `## Notion companion databases` keep working: the new `## Research tracker` section is *added* (not replacing the existing Notion-DB list section, which the engine still references). When the new mcbrain skill reads CLAUDE.md and finds no `## Research tracker` section, it falls back to "look for Notion companion databases", preserving today's behavior.

## Storage Layout

This option adds a new `research_tasks/` subdirectory under the existing `raw/` directory. The vault's overall folder structure is otherwise unchanged. `research_tasks/` contains exactly one file:

```
raw/research_tasks/tasks.jsonl
```

That is the entire footprint of this feature inside the vault. 

Findings markdown files written by the runner go into the **existing** `raw/notes/` directory, where they are picked up by the standard ingest flow alongside Web Clipper and hand-drop notes.

### Naming convention for the `raw/notes/` markdown files

```
research-<topic-slug>-<task-id>.md
```

- `<topic-slug>` is the same lowercase-hyphenated slug stored on the JSONL row (e.g. `gut-microbiome`).
- `<task-id>` is a short ULID (or 8-char random hex if ULID is too heavy to add as a dep) generated when the row is created. Stable for the life of the task.
- The leading `research-` prefix groups all runner-produced notes together when a user is browsing `raw/notes/` in Obsidian, and disambiguates them from Web Clipper / hand-drop notes that share the directory.

Example: `research-gut-microbiome-01HFXR3Q2K.md`

### Markdown file format

```markdown
---
source: local-research-tracker
topic: <Research Topic>
topic_slug: <topic-slug>
task_id: <id>
task_name: <Task Name>
research_date: <ISO-8601 last_updated_date when findings were written>
captured: <ISO-8601 today>
---

# <Task Name>

## Summary
...

## Detailed Results
...

## Key Findings
...

## Open Questions
...

## Sources
1. ...
2. ...
```

The frontmatter is what the standard ingest flow uses to attribute the note. The five `##` sections are the runner's verbatim subagent output (matching the Notion runner's "do not summarize, truncate, paraphrase, or drop anything" rule).

## Atomic Update Strategy

**The rule:** Every modification of `tasks.jsonl` (claim, status flip, `notes_path` write) goes through this exact protocol. No exceptions.

**Zero-dependency constraint:** Uses only Python 3 standard library (`os`, `json`, `tempfile`, `time`, `datetime`). No third-party packages, no external CLI tools (no `flock(1)`, no `lockfile`, no `filelock`). Works out of the box on any machine that already runs Claude Code's Python skills.

**Cross-platform constraint:** McBrain runs on macOS, Linux, and Windows. The strategy uses only Python primitives that behave identically on all three: `os.open` with `O_CREAT | O_EXCL`, `os.replace`, `os.fsync`, `tempfile.mkstemp`. No `fcntl`, no POSIX-only signals, no shell tools.

### Chosen approach: exclusive-create lock file + write-temp-then-`os.replace`

Two stdlib primitives, both fully cross-platform:

1. **Exclusive-create lock file** at `<vault>/raw/research_tasks/tasks.jsonl.lock`, opened with `os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)`. This call is atomic on POSIX *and* on Windows — exactly one caller wins the race; everyone else gets `FileExistsError`. Releasing the lock = `os.unlink(lockfile)`. Each lock cycle is sub-second (acquire → read → mutate → write → release), so a held lock is not a meaningful blocker for other runners.
2. **Write-temp-then-`os.replace`** on the same filesystem. `os.replace` is atomic on both POSIX (calls `rename(2)`) and Windows (calls `MoveFileExW` with `MOVEFILE_REPLACE_EXISTING`). It's the right primitive — `os.rename` would fail on Windows when the destination already exists. Any reader sees either the old file or the new file, never a partial one. Protects against mid-write process kills and naive readers that don't take the lock.

### Why both

- The lock alone wouldn't protect a reader that doesn't take the lock — a user running `cat tasks.jsonl` (or `type` on Windows) mid-write could see a half-written file.
- `os.replace` alone wouldn't prevent two runners from both reading, both modifying, and both writing — last-writer-wins, lost updates. The lock serializes read-modify-write.

### Why not other approaches

- **`fcntl.flock` / `flock(1)` CLI.** `fcntl` is POSIX-only — not available on Windows Python. `flock(1)` the CLI is GNU/Linux-only. Both are non-starters for a cross-platform skill.
- **`msvcrt.locking` / `LockFileEx`.** Windows-only counterpart to `fcntl`. Forces platform-branched code for no real benefit over the exclusive-create pattern, which works everywhere.
- **TTL-based stale-lock reclaim.** Considered, but unnecessary given how short a real cycle is (sub-second). A TTL adds a knob (how long? what if a slow disk?) and an extra failure mode (reclaiming a still-live but slow lock). Simpler answer: short acquire timeout + clear error message + manual cleanup on the rare crash, see *Stuck-lock recovery* below.
- **PID liveness probing (`os.kill(pid, 0)`).** Reliable on POSIX, but Windows semantics differ and "is this PID alive?" without a third-party package gets messy. Skipping liveness probes entirely is simpler.
- **In-place edit with `O_APPEND`.** Works for pure appends, but status flips and findings updates are read-modify-rewrite, not append.
- **SQLite with WAL.** Solid, but breaks the "just a JSONL the user can `cat`" property that's the whole point of the local backend.
- **Third-party `filelock` / `portalocker` packages.** Add a pip install. Not worth the dependency for a single sidecar file.

### Concrete protocol

The runner imports a single small helper (defined inside the skill, no external deps) that wraps this loop. Pseudocode:

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
  (if the file doesn't exist, treat as empty list)

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

The skill MUST wrap acquire/release in `try/finally` so the lock file is removed on every exit path, including subagent failures and exceptions.

### Stuck-lock recovery

If a runner crashes (force-killed, power loss) between creating the lock file and unlinking it, the lock is left orphaned on disk. There is **no automatic reclaim** — the next runner's `ACQUIRE_LOCK` will time out after 5 seconds and surface a clear error message instructing the user to delete `tasks.jsonl.lock` manually after confirming no other runner is active. This is deliberate: lock cycles are sub-second, so a held lock that lasts more than 5 seconds is a strong signal that something is wrong, not that something is just slow. Trading automatic recovery for one manual step is the right call given how rare the crash scenario is.

The runner SKILL.md must mention this recovery instruction in its troubleshooting section so users don't have to grep this plan to find it.

### What this protects vs. does NOT protect

- ✅ Two runners claiming tasks at the same time — serialized by the exclusive-create lock, no double-claim. Works identically on macOS, Linux, and Windows.
- ✅ Runner killed mid-write — `tasks.jsonl` is untouched (only the orphaned temp file might exist; safe to leave or delete on next run).
- ✅ Runner killed mid-lock — next runner times out after 5 s and gives the user a clear "delete the .lock file" instruction. Manual one-step recovery; no automatic reclaim.
- ✅ Naive reader (`cat`/`type tasks.jsonl`) during a write — sees old or new file via `os.replace` atomicity.
- ⚠️ Network-mounted filesystems where `rename`/`O_EXCL` semantics aren't honored (rare on a single-user laptop vault). On a vault stored in a Google Drive / iCloud / OneDrive sync folder, the local-FS operation is still atomic — the cloud sync layer replicates the new file as-is.
- ⚠️ Windows file-share modes: in rare cases another process holding `tasks.jsonl` open without `FILE_SHARE_DELETE` could block `os.replace`. In practice Notepad / VS Code / `type` use share modes that allow replacement. Document as a known edge case; recommend closing editors that have the file open before writing.
- ❌ Two runners on different machines pointing at the same vault path. Out of scope; the SKILL says "single machine" in its prerequisites.

The runner SKILL must include this protocol verbatim in its `references/atomic_write_protocol.md` so any contributor implementing/maintaining it follows the same flow.

## Changes

### `plugins/mcbrain/skills/local-research-db/` (NEW)

- `SKILL.md` — modeled on `notion-research-db/SKILL.md` but with these differences:
  - No Notion connector check, no parent-page selection.
  - **Inputs:** research topic (free-form string); associated McBrain vault (resolved via the same `mcbrain-*` MCP enumeration logic — copy the *Identifying the McBrain vault* section verbatim).
  - **Workflow:**
    1. Resolve vault, derive `<topic-slug>` (lowercase, hyphenated) from the topic.
    2. Ensure `<vault>/raw/research_tasks/` exists. Ensure `<vault>/raw/research_tasks/tasks.jsonl` exists (touch if missing — empty file is valid).
    3. **Register in `CLAUDE.md`** under a new `## Research tracker` section:

       ```markdown
       ## Research tracker

       Backend: local
       File: raw/research_tasks/tasks.jsonl

       Topics:
         - **<Research Topic>**
           - Topic slug: <topic-slug>
           - Registered: <YYYY-MM-DD>
           - Notes: companion local research tracker for this topic.
       ```

       If a `## Research tracker` section already exists with `Backend: local`, append the new topic under `Topics:` rather than overwriting. If `Backend:` is `notion`, surface the conflict to the user and ask whether to switch the backend or keep both registered (single-backend recommended; see *Mixed backends* note below).
    4. Append to `wiki/log.md`: `- YYYY-MM-DD — registered local tracker topic: <topic>`.
    5. **Backup.** Same git-vs-google-drive-vs-none copy-paste-fence pattern as `notion-research-db`. The commit fence is `git add CLAUDE.md raw/research_tasks/tasks.jsonl && git commit -m "init: local research tracker (<topic>)" && git push`.

  - **Why one file, not one file per topic.** Simpler concurrency story (one lock, one file), simpler discovery (no enumeration of subdirs to find all open tasks), and topics are easy to filter on read. Folder-per-topic was over-engineered for the actual workload.
  - **Schema documentation.** The SKILL.md MUST include a "Row schema" subsection that lists every field (`id`, `topic`, `topic_slug`, `task_name`, `status`, `priority`, `created_date`, `last_updated_date`, `notes`, `notes_path`, `sources_count`) with a one-line description of each. This is what `tests/test_local_research.py::test_jsonl_schema_documented` parses.

- No `references/` content needed — the schema is small enough to live inline.

### `plugins/mcbrain/skills/local-research-runner/` (NEW)

- `SKILL.md` — modeled on `notion-research-runner/SKILL.md`. Same Phase 1–5 shape. Differences:
  - **No Notion-connector capability matching at startup.**
  - **Inputs:** which topic to drain (resolve via `## Research tracker → Topics` in CLAUDE.md; ask if multiple topics are registered and the user didn't specify); batch size (default 5, hard cap 5).
  - **Phase 1 — Fetch candidates.** Acquire lock, read `raw/research_tasks/tasks.jsonl` line-by-line, filter rows where `topic_slug == <selected>` AND `status == "To do"`, sort by `priority` (High → Medium → Low) then `created_date` ascending, take the first N. (Lock held through Phase 2.)
  - **Phase 2 — Claim.** Following the atomic protocol (see *Atomic Update Strategy*):
    1. Lock is already held from Phase 1.
    2. For each claimed row, set `status = "In progress"` and `last_updated_date = <now ISO-8601>`.
    3. Write all rows (claimed and unclaimed) to a temp file in `raw/research_tasks/`, fsync, rename over the original.
    4. Release lock.
  - **Phase 3 — Plan + research.** Reuse the existing `notion-research-runner/references/research_subagent_prompt.md` template verbatim — it's backend-agnostic. Spawn one `general-purpose` subagent per claimed task in a single message (parallel). Subagents do NOT touch `tasks.jsonl`; they return findings markdown to the runner.
  - **Phase 4 — Write findings to `raw/notes/`, then update JSONL.** For each subagent return:
    1. **Write the markdown file first.** Compose the file at `<vault>/raw/notes/research-<topic-slug>-<task-id>.md` with the frontmatter block (see *Markdown file format* above) followed by the verbatim subagent output. This write does NOT need the JSONL lock — it's a different file.
    2. **Atomically update the JSONL row.** Acquire lock, read `tasks.jsonl`, find the row by `id`, set `notes_path = "raw/notes/research-<topic-slug>-<task-id>.md"`, set `sources_count = <parsed from Sources section>`, bump `last_updated_date`, set `status = "Done"`, write+rename, release lock.
    - **Important:** Each task gets its own lock cycle in Phase 4 — the runner does NOT batch all four updates into one rewrite, because subagents finish at different times and we don't want a slow subagent to block recording the fast ones. This is exactly the kind of concurrency the lock is designed for.
  - **Phase 5 — Report.** Each task title, one-line takeaway pulled from the file's `## Summary`, and the row's `id` plus the markdown path (so the user can `open` the file in Obsidian).
  - **Failure modes** (mirror the Notion runner's section):
    - Subagent malformed output → runner does NOT write the markdown file, does NOT flip status to Done; row stays at `In progress` so the next runner invocation can detect and retry.
    - Runner killed between writing the markdown file and updating the JSONL → markdown exists but row says `In progress` with empty `notes_path`. On next run, detect this (`In progress` rows where a file matching the naming convention exists) and offer to reconcile.
    - Runner killed mid-claim (Phase 2) → an orphaned lock file may remain; rows stay at `To do` (the JSONL was never rewritten); next runner surfaces a "delete the .lock file" message after a 5-second acquire timeout. No half-state in the data file.
  - **Atomic protocol reference.** SKILL.md must include or `references/`-link the atomic protocol from this plan, so an implementing contributor can't accidentally do a non-atomic update.

### `plugins/mcbrain/skills/local-research-runner/references/atomic_write_protocol.md` (NEW)

- Verbatim copy of the *Atomic Update Strategy* section above, formatted as a standalone reference. Both the SKILL.md body and any contributor adding new write paths consult it.

### `plugins/mcbrain/skills/local-research-runner/references/research_subagent_prompt.md` (NEW)

- Verbatim copy of the Notion runner's prompt template (backend-agnostic content already).

### `plugins/mcbrain/skills/mcbrain-setup/SKILL.md` — modify Step 8

Restructure Step 8 from "Notion companion database (optional)" to "Research tracker setup (optional)". The existing Notion sub-steps move under a `notion` branch.

- New **8a — Choose research-tracker backend.** Ask via `AskUserQuestion`:

  ```yaml
  questions:
    - question: "How do you want to track research tasks for this McBrain?"
      header: "Research tracker"
      multiSelect: false
      options:
        - label: "Local (Recommended — no Notion needed)"
          description: "Tracks tasks as a JSONL file in your vault under raw/research_tasks/. Zero dependencies, works offline."
        - label: "Notion"
          description: "Tracks tasks in a Notion database. Needs a Notion MCP connector and admin rights to create integrations."
        - label: "Skip for now"
          description: "Don't set one up. You can add one later by re-running mcbrain-setup or running local-research-db / notion-research-db."
  ```

  Store as `RESEARCH_TRACKER_BACKEND` ∈ {`local`, `notion`, `none`}.

- **8L — Local backend branch** (only if `local`). Create the default tracker for the vault (default topic = a sensible default like the vault domain, e.g. `mcbrain-finance` → topic `finance`):
  1. Ensure `<vault>/raw/research_tasks/tasks.jsonl` exists (touch if missing) using the Write tool against the granted vault mount.
  2. Append a `## Research tracker` section to `CLAUDE.md` with `Backend: local`, the `File:` line, and one entry under `Topics:` (template above).
  3. No Notion token, no MCP-engine call (the engine doesn't need to know about local trackers — they're just files in the vault, which the filesystem MCP already has access to).
  4. If git backup, present a single copy-paste commit fence: `git add CLAUDE.md raw/research_tasks/tasks.jsonl && git commit -m "init: local research tracker" && git push`.

- **8N — Notion backend branch** (only if `notion`). This is today's 8a–8g, renumbered. The existing token install (8f) and `enable_notion_for_vault` call (in 8.5) only happen on this branch. Skip them entirely on the local branch.

- Update the **Required intake** table at the top of the SKILL: rename row 8 to `RESEARCH_TRACKER_BACKEND ∈ {local, notion, none}`. Step 8.5's `enable_notion_for_vault` call becomes conditional on `RESEARCH_TRACKER_BACKEND == notion`.

### `plugins/mcbrain/skills/mcbrain-setup/references/claude-md-template.md` — modify

- **Replace** today's `## Notion companion databases` section with a unified `## Research tracker` section that supports both backends. Schema:

  ```markdown
  ## Research tracker

  Backend: <local | notion | none>

  <!-- If backend is local: -->
  File: raw/research_tasks/tasks.jsonl
  Topics:
    - **<Topic>**
      - Topic slug: <slug>
      - Registered: <YYYY-MM-DD>

  <!-- If backend is notion: -->
  Notion databases:
    - **<DB Name>**
      - URL: <URL>
      - Database ID: <hex>
      - Registered: <YYYY-MM-DD>

  <!-- If backend is none: empty body. -->
  ```

  Keep a one-paragraph explanation block above so a new reader (or Claude) knows what the section is for and which downstream skills consume it.

- **Update the existing "Ingest from Notion research tracker" sub-section** (in `## Operations → Ingest`) so the introductory paragraph references *Backend: notion* explicitly: "Use this when CLAUDE.md's `## Research tracker → Backend` is `notion`. For `local`, the runner writes findings directly to `raw/notes/` so the standard ingest flow handles them — no special procedure required."

- **Do NOT add an "Ingest from local research tracker" sub-section.** The local backend doesn't need one — the standard ingest already covers it. The CLAUDE.md template only documents the local layout (so a reader knows what `raw/research_tasks/tasks.jsonl` is) but does not route ingest through a special path.

### `plugins/mcbrain/skills/mcbrain/SKILL.md` — NO CHANGES

The mcbrain skill keeps its current two-mode ingest routing (Notion-bridged + standard). No third "local-bridged" mode is needed because the local runner writes its output as ordinary `raw/notes/*.md` files, which the standard ingest already handles.

### `plugins/mcbrain/.claude-plugin/plugin.json` — minor

- Bump `version` (e.g. `2.1.0` → `2.2.0` — minor, additive feature).
- Update `description` to mention "local or Notion-backed research trackers" (instead of just "Notion").
- Add `local-research` to `keywords`.

### `tests/` — add a static + concurrency test layer

- New `tests/test_local_research.py`. The two new skills are pure markdown so they don't import in pytest the way the engine does — but the JSONL schema and the atomic-rewrite logic are testable:
  - **`test_jsonl_schema_documented`** — read both new SKILL.md files, assert each lists every required field (`id`, `topic`, `topic_slug`, `task_name`, `status`, `priority`, `created_date`, `last_updated_date`, `notes`, `notes_path`, `sources_count`).
  - **`test_runner_claim_then_findings_round_trip`** — minimal Python helper (in the test itself, not in the skill) that simulates Phase 2 + Phase 4 against a fixture `tasks.jsonl`: load → claim 3 rows → verify atomic rewrite (temp file gone, rename worked, other rows untouched) → "complete" them by writing markdown files and updating rows → verify status/notes_path/last_updated_date are correct.
  - **`test_jsonl_atomic_concurrent_writers`** — **MANDATORY.** Spawn 5 subprocesses that simultaneously try to claim 3 rows each from a fixture `tasks.jsonl` containing 30 `To do` rows. Each subprocess uses the documented exclusive-create lock + temp-rename protocol. Assertions:
    1. Total claimed rows across all subprocesses = 15 (each row claimed exactly once — no double-claims, no lost claims).
    2. `tasks.jsonl` parses as valid JSONL after all subprocesses exit (no truncation, no interleaved bytes).
    3. Row count is unchanged (30 rows in, 30 rows out).
    4. Every row that ended up `In progress` has a `last_updated_date` newer than the fixture's original timestamps.
    5. The lock file (`tasks.jsonl.lock`) does NOT exist after all subprocesses exit cleanly (it is unlinked on lock release).
  - **`test_claude_md_template_has_research_tracker_section`** — assert the template now has `## Research tracker` with `Backend:` and `File:` lines, and that the legacy `## Notion companion databases` heading is *either* gone or clearly marked as the old name (so existing vaults still grep-matchable).
- No changes to engine tests — the engine doesn't change.

## Critical Files

- `plugins/mcbrain/skills/local-research-db/SKILL.md` — NEW.
- `plugins/mcbrain/skills/local-research-runner/SKILL.md` — NEW.
- `plugins/mcbrain/skills/local-research-runner/references/atomic_write_protocol.md` — NEW.
- `plugins/mcbrain/skills/local-research-runner/references/research_subagent_prompt.md` — NEW (verbatim copy of the Notion one).
- `plugins/mcbrain/skills/mcbrain-setup/SKILL.md` — MODIFIED (Step 8 restructure, Required-intake table update).
- `plugins/mcbrain/skills/mcbrain-setup/references/claude-md-template.md` — MODIFIED (new `## Research tracker` section; **no** new ingest sub-procedure).
- `plugins/mcbrain/.claude-plugin/plugin.json` — MODIFIED (version bump, description, keywords).
- `tests/test_local_research.py` — NEW (includes the mandatory concurrency test).

No engine MCP code changes. No new MCP tools. No changes to `notion.py`, `mcbrain_engine.py`, or `registry.py`. `plugins/mcbrain/skills/mcbrain/SKILL.md` is not modified — there is no third ingest mode.

## Dependencies & Ordering

1. Update `claude-md-template.md` first — defines the contract that every other change consumes.
2. Build the two new skills (`local-research-db`, `local-research-runner`) — they are independent of `mcbrain-setup` and can be developed and tested in isolation. The atomic protocol reference is part of building the runner.
3. Update `mcbrain-setup/SKILL.md` Step 8 — depends on the template change (it writes the new section).
4. Bump `plugin.json` version — last, after everything is in.
5. Add tests — can be in parallel with steps 2–3, but run them at the end. The concurrency test is a hard gate for landing.

## Risks & Open Questions

- **Mixed backends.** A user could conceivably want both — Notion for one topic and local for another. The `## Research tracker` section design above forces a single `Backend:` value, which is a deliberate simplification. **Recommendation: ship as single-backend, document the limitation, revisit if a user actually asks for mixed.**
- **Concurrency model — was the lock actually needed?** Multiple research-runner subagents may hit `tasks.jsonl` concurrently (today the parent does the writes, but a future architecture might let subagents write directly), and a user can also start two runner sessions in two terminals against the same vault. Adding the lock + rename now is cheap (Python stdlib only, ~40 lines) and forward-compatible; it's the right call.
- **Portability.** `os.open` with `O_CREAT|O_EXCL`, `os.replace`, `os.fsync`, and `tempfile.mkstemp` are all Python stdlib and behave identically on macOS, Linux, and Windows. The runner's SKILL.md prerequisites should still list "Python 3.x available locally" but does NOT need to restrict by OS.
- **`raw/notes/` namespace collisions.** Other ingest paths (Web Clipper, hand drops) also write into `raw/notes/`. The `research-<topic-slug>-<task-id>.md` prefix avoids collision with anything realistic, and the `<task-id>` suffix is unique. A user manually creating a file named `research-foo-bar.md` could in theory collide; document the convention so users avoid the prefix.
- **`sources_count` parsing fragility.** Counting items in the Sources section is best-effort. On parse failure, leave the field unset (or `null`); the markdown file is still complete. Don't fail the whole task on a parsing edge case.
- **Token-cost discipline.** With findings living in `raw/notes/*.md` and only a `notes_path` pointer in the JSONL, the JSONL stays tiny indefinitely. ~30 bytes of pointer per row × 1000 tasks = 30 KB. Non-issue.
- **Migration of existing Notion users.** Out of scope. Existing vaults keep their `## Notion companion databases` section working.
- **Naming.** `local-research-db` is slightly awkward (there's no DB; it's a JSONL file). Sticking with it for symmetry with `notion-research-db` so users with muscle memory pick it up immediately.
- **No engine tool changes.** With findings flowing through `raw/notes/`, the engine doesn't need a `local_ingest` tool, and we don't need to touch `mcbrain/SKILL.md` to add a routing mode. Engine surface area is genuinely zero.

## Verification

**Manual end-to-end** (the most informative check, given that the new artifacts are skills not Python):

1. **Setup with local backend.** Run `mcbrain-setup` from scratch in a fresh sandbox; pick `Local` at Step 8a. Expect: `<vault>/raw/research_tasks/tasks.jsonl` exists (empty), CLAUDE.md has `## Research tracker` with `Backend: local`, `File: raw/research_tasks/tasks.jsonl`, and one topic under `Topics:`. No Notion token prompt appears.
2. **Setup with Notion backend.** Re-run setup, pick `Notion`. Expect: today's Step 8 flow runs (DB creation/registration, token install, `enable_notion_for_vault`). CLAUDE.md has `## Research tracker` with `Backend: notion` plus the `Notion databases:` sublist.
3. **Setup with none.** Re-run setup, pick `Skip for now`. Expect: `## Research tracker → Backend: none` and no other artifacts created.
4. **Add a topic post-setup.** With a local-backend vault, run `local-research-db` for a new topic. Expect: `tasks.jsonl` is unchanged (still flat), the new entry appended under `Topics:` in CLAUDE.md.
5. **Run the local runner end-to-end.** Hand-write three rows in `tasks.jsonl` for the same topic with `status: To do` and varied priority. Run `local-research-runner`. Expect: rows flip to `In progress` immediately (Phase 2 lock + rename), three subagents run in parallel, three markdown files appear under `raw/notes/research-<slug>-<id>.md` with full frontmatter and four-section bodies, JSONL rows now show `status: Done` with `notes_path` populated.
6. **Standard ingest picks them up.** Run a standard ingest. Expect: the three new `raw/notes/research-*.md` files are ingested into the wiki via the existing flow with no special routing. **No `mcbrain/SKILL.md` changes were needed.**
7. **Concurrency stress (mandatory).** From two terminals, run `local-research-runner` simultaneously against the same vault with 10 `To do` rows queued. Expect: each runner claims its own disjoint set of rows (no double-claims), `tasks.jsonl` remains valid JSONL throughout, and the two runs complete without errors.
8. **Crash recovery.** Start a runner, force-kill it during Phase 3 (after claim, before findings write) — `kill -9 <pid>` on macOS/Linux, `taskkill /F /PID <pid>` on Windows. Expect: rows stay at `In progress`. If the kill happened mid-lock, an orphaned `tasks.jsonl.lock` may remain — the next runner times out after 5 s with a clear "delete the .lock file manually" message. After deleting the lock and re-running, the SKILL detects orphaned `In progress` rows and offers to retry just those.
9. **Backwards compatibility.** Open a vault that pre-dates this change (no `## Research tracker` section, only `## Notion companion databases`). Run `mcbrain` operations. Expect: the skill detects the legacy layout, falls back to `Backend == notion` behavior, doesn't error.

**Automated** (`pytest tests/test_local_research.py`):

- All four tests in the new file pass, including the mandatory `test_jsonl_atomic_concurrent_writers`.
- The existing engine test suite continues to pass unchanged — engine code wasn't touched.

**Idempotency checks:**

- Running `local-research-db` twice with the same topic is a no-op (jsonl already exists; CLAUDE.md entry detected as already-present).
- Running `local-research-runner` twice in a row drains a different batch the second time (Phase 2's claim is what makes this true).
- Running standard ingest twice doesn't re-create wiki pages for already-ingested `raw/notes/research-*.md` files (existing standard-ingest idempotency handles this).
