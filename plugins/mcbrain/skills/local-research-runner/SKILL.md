---
name: local-research-runner
description: Pull up to 5 "To do" tasks from a local JSONL research tracker (raw/research_tasks/tasks.jsonl), flip them to "In progress" via an atomic write, spawn a planning-then-executing research subagent for each in parallel, write the findings as markdown files to raw/notes/, and flip the rows to "Done". Use when the user says things like "run my local research queue", "drain the local tracker", "process the To do tasks in my vault's tracker", "kick off the local research backlog", or otherwise asks to execute queued research tasks from the vault's local JSONL tracker.
---

# Local Research Runner

Drain the "To do" queue of a local JSONL research tracker (the kind initialized by the `local-research-db` skill) by researching each task with a subagent and writing findings as markdown files into the vault's `raw/notes/` directory. The standard McBrain ingest flow then picks those files up — there is no special "local-bridged" ingest mode.

This skill is the local-backend twin of `notion-research-runner`. CLAUDE.md's `## Research tracker → Backend` line decides which one applies. Use this skill when `Backend: local`; use `notion-research-runner` when `Backend: notion`.

## Prerequisites

- The vault must be a McBrain vault — exposed as a filesystem MCP server named `mcbrain-<topic>` (or `mcbrain` for a single default vault).
- The vault's `CLAUDE.md` must have `## Research tracker → Backend: local`. (Legacy fallback: a vault that has `## Notion companion databases` but no `## Research tracker` section is treated as `Backend: notion` and is **not** handled by this skill — point the user at `notion-research-runner` instead.)
- The vault must have `raw/research_tasks/tasks.jsonl` (created by `local-research-db`).
- Python 3 must be available on the user's host (for the atomic-write helper). No third-party packages, no extra CLI tools (no `flock(1)`, no `lockfile`, no `filelock`). Standard library is enough on macOS, Linux, and Windows alike.
- The `Agent` tool must be available (for spawning research subagents). `WebSearch` / `WebFetch` are used **by the subagents**, not by this skill directly.

## Inputs to Collect

1. **Which topic to drain.** Read `## Research tracker → Topics:` from CLAUDE.md.
   - One topic registered → use it.
   - Multiple topics → if the user named one, use that; otherwise ask which.
   - Zero topics → tell the user no topics are registered and stop.
2. **Batch size.** Default 5. If the user asked for a different cap, honor it. Never exceed 5 in one run without explicit confirmation — parallel research burns tokens fast.

That's the entire input set. There is no Notion connector to match, no database URL to paste.

## The Atomic Write Protocol — read this once

Every modification of `tasks.jsonl` (claim, status flip, `notes_path` write) goes through the protocol documented at `references/atomic_write_protocol.md`. Read it before writing any code that touches `tasks.jsonl`. The summary:

- Acquire an exclusive lock by `os.open`-ing `tasks.jsonl.lock` with `O_CREAT | O_EXCL | O_WRONLY`. The file's existence is the lock; its contents are unimportant. Acquire timeout: 5 seconds.
- Modify the data: read the whole `tasks.jsonl` into memory, mutate, serialize to a temp file in the same directory, `os.fsync`, then `os.replace` the temp over the real file. (`os.replace` — not `os.rename` — because Windows.)
- Release the lock by `os.unlink`-ing the lock file.
- Wrap acquire/release in `try/finally`.

This works on macOS, Linux, and Windows out of the box — Python stdlib only.

**Why a lock at all:** even though today the parent runner does all writes (subagents return findings; only the runner mutates JSONL), nothing prevents two runner sessions running in two terminals against the same vault — or a future architecture where parallel agents claim and update rows directly. Without the lock, two readers can claim the same row and silently produce duplicate research. The lock is cheap (sub-second cycles) and forward-compatible.

## Workflow

Execute these phases in order. Do not skip steps.

### Phase 1 — Fetch candidates

Acquire the lock, read `tasks.jsonl` into memory, then with the lock still held proceed straight into Phase 2 (one acquire/release covers Phases 1+2).

1. **Acquire the lock** following the atomic protocol.
2. **Read** `<vault>/raw/research_tasks/tasks.jsonl` into a list of dict rows. If the file does not exist, treat as empty and tell the user the queue is empty (then release the lock and stop).
3. **Filter** rows where `topic_slug == <selected>` AND `status == "To do"`.
4. **Sort** by `priority` (`High` → `Medium` → `Low`), then by `created_date` ascending (older first).
5. Take at most the batch size (default 5).

If zero rows match the filter, release the lock and tell the user the queue is empty (for that topic).

### Phase 2 — Claim the tasks (lock still held)

The lock from Phase 1 is still held when entering this phase.

1. For each selected row, set `status = "In progress"` and bump `last_updated_date = <now ISO-8601 UTC>`.
2. Serialize the **full** row list (claimed and unclaimed) as JSONL to a temp file in `raw/research_tasks/`, fsync, `os.replace` over `tasks.jsonl`. Preserve unknown fields on every row — the user may have hand-edited the file.
3. Release the lock (unlink `tasks.jsonl.lock`).

Report to the user: the N tasks claimed, with titles and priorities. Tell them you are about to spawn subagents.

**Crash-recovery note for the user:** if this skill (or the host) crashes after the lock file is created but before it's unlinked, the next runner will time out after 5 seconds with a clear error message asking them to delete `<vault>/raw/research_tasks/tasks.jsonl.lock` manually after confirming no other runner is active. There is no automatic stale-lock reclaim. See *Troubleshooting* below.

### Phase 3 — Plan + research (parallel subagents, no lock held)

The lock is not held during this phase — it would defeat the point of doing research in parallel.

Spawn one `general-purpose` subagent per claimed task, **in a single message with multiple `Agent` tool calls** so they run in parallel. Use the prompt template at `references/research_subagent_prompt.md`. Fill in:

- `RESEARCH_TOPIC` — the topic name (not the slug, not the database title).
- `TASK_NAME` — the row's `task_name`.
- `PRIORITY` — the row's `priority`.
- `NOTES_OR_"(none)"` — the row's `notes` field, or the literal string `(none)` if empty.

The template instructs each subagent to plan, then execute with web search/fetch, then return a fixed-format Markdown document with **five** sections in order: `## Summary`, `## Detailed Results`, `## Key Findings`, `## Open Questions`, `## Sources`.

Do not paraphrase the template — load it from the reference file and substitute fields.

### Phase 4 — Write findings to `raw/notes/` and flip rows to Done

For each subagent that returns successfully, do these two writes **per task** (not batched across tasks). Subagents finish at different times — you do **not** want a slow subagent to delay recording a fast one. Each task gets its own lock cycle on the JSONL file.

For each completed subagent:

1. **Write the markdown file first** at `<vault>/raw/notes/research-<topic-slug>-<task-id>.md`. This write does **not** need the JSONL lock — it's a different file. Compose:

   ```markdown
   ---
   source: local-research-tracker
   topic: <Research Topic>
   topic_slug: <topic-slug>
   task_id: <id>
   task_name: <Task Name>
   research_date: <YYYY-MM-DD — today, looked up, not guessed>
   captured: <YYYY-MM-DD — today>
   ---

   # <Task Name>

   <... the verbatim subagent output, all five sections in order, no truncation, no paraphrase ...>
   ```

   The full subagent output goes in **verbatim**. Do not summarize, truncate, paraphrase, or drop sections. If `## Open Questions` would be empty, the subagent prompt requires the literal text `None.` so render that. The five-section invariant is what `tests/test_local_research.py` checks.

2. **Atomically update the JSONL row.** Acquire the lock, find the row by `id`, set:

   - `notes_path = "raw/notes/research-<topic-slug>-<task-id>.md"`
   - `sources_count = <count of items in the markdown's ## Sources section, best-effort>` (on parse failure leave the field unset rather than guessing)
   - `last_updated_date = <now ISO-8601 UTC>`
   - `status = "Done"`

   Serialize all rows, `os.fsync`, `os.replace`, release the lock.

**Sanity check before flipping a row to Done:** confirm the markdown file exists on disk and contains all five expected `##` headings. If it does not, treat the subagent return as malformed (see *Failure Modes* below) — leave the row at `In progress` and surface the issue.

### Phase 5 — Report

Return a compact summary to the user:

- For each task: title, one-line takeaway pulled from the markdown's `## Summary`, the row's `id`, and the path to the markdown file (so the user can `open` it in Obsidian).
- For each failure: the task title, the row's `id`, what went wrong, and what state the row is in (`In progress` typically).

End with a one-line note that the standard ingest flow will pick the new files up on the next ingest — there is no special "local-bridged" ingest mode to run.

## Failure Modes to Handle

- **Subagent malformed output** (missing one of the five sections, missing Sources, wrong headings). Ask it once to reformat; if it fails again, **do not** write the markdown file and **do not** flip status to Done. Leave the row at `In progress` so a future runner invocation (or a follow-up by the user) can detect and retry. Surface the malformed output to the user verbatim so it isn't lost.
- **Markdown file write fails** (filesystem MCP returns an error). Same outcome as malformed output: row stays at `In progress`, surface the failure to the user.
- **JSONL update fails after the markdown file was already written** (rare, e.g. lock timeout). The markdown file exists at `raw/notes/research-<topic-slug>-<task-id>.md`, but the row still says `In progress` with no `notes_path`. Tell the user this orphan exists — they can re-run the runner, which should detect orphaned `In progress` rows whose expected markdown file already exists and offer to reconcile (set `notes_path` and flip to Done without re-researching).
- **Lock timeout (`LockTimeout`).** Another runner is likely active, or a previous runner crashed leaving an orphaned lock file. Surface the exact error message from the helper, which already tells the user what to do (delete `tasks.jsonl.lock` manually after confirming no other runner is running).
- **Runner killed mid-claim (between acquiring the lock in Phase 1 and writing in Phase 2).** Rows were never rewritten — they stay at `To do`. The lock file may be orphaned; next runner will time out and surface the manual-delete message. No half-state in the data file.
- **Runner killed between writing the markdown and updating the JSONL (Phase 4 mid-step).** Same as the rare-orphan case above — re-run, reconcile.

## Reconciliation of orphaned `In progress` rows

At the start of every run (just after fetching candidates in Phase 1, before mutating anything), scan for rows where `status == "In progress"` AND `topic_slug == <selected>` AND a file exists at the expected `raw/notes/research-<topic-slug>-<task-id>.md` path. These are orphans from a previous crash — the research is done but the row was never closed.

For each orphan, ask the user whether to reconcile (flip the row to `Done`, set `notes_path` to the existing file, best-effort fill `sources_count`) or to leave it for inspection. Do reconciliation in its own per-row lock cycle, before claiming new candidates. This keeps the queue from filling up with permanently-stuck rows.

If `In progress` rows exist with **no** corresponding markdown file, they are likely truly mid-flight from a sibling runner — leave them alone.

## Troubleshooting

**"Could not acquire `tasks.jsonl.lock` after 5s. Another runner is likely active. If you are sure no other runner is running, delete the .lock file manually and retry."**

Two scenarios:

1. **Another runner really is active** (you ran this skill in two terminals, or a parallel-agent architecture is in flight). Wait for it to finish, then retry. The lock cycle is sub-second; the next acquire should succeed almost immediately.
2. **A previous runner crashed** (force-killed, power loss) and left an orphaned lock file. Confirm no runner is active, then delete `<vault>/raw/research_tasks/tasks.jsonl.lock` manually and re-run. There is no automatic stale-lock reclaim by design — a held lock that lasts more than 5 seconds is a strong signal of a real problem, not a slow disk.

## Non-Goals

- Do **not** create new rows in `tasks.jsonl`. The runner only updates existing rows.
- Do **not** modify any field other than `status`, `last_updated_date`, `notes_path`, and `sources_count` on a row. Other fields (notably `priority` and `notes`) are user-owned.
- Do **not** flip rows to `Done` without writing the markdown file first.
- Do **not** ingest into the wiki yourself — that's the standard ingest's job, run separately by the `mcbrain` skill against `raw/notes/`.
- Do **not** spawn more subagents than the batch cap in a single run.
- Do **not** hold the lock across Phase 3 (research). Lock cycles must be sub-second.
