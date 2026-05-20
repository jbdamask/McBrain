---
name: local-research-db
description: Initialize a JSONL-based research-task tracker for a McBrain vault — a single JSONL file under raw/research_tasks/ that the local-research-runner skill drains. Use when the user says "set up a local research tracker", "add a research topic to my vault", "make a JSONL tracker for X", or asks for a research backlog stored inside the vault itself as plain files.
---

# Local Research DB

Initialize (or extend) a local research-task tracker for a McBrain vault. The tracker is a single JSONL file at `<vault>/raw/research_tasks/tasks.jsonl` — one JSON object per line, all topics in one file, topic-as-a-field (not a folder). The companion skill `local-research-runner` drains "To do" rows from this file, runs research subagents, writes findings to `raw/notes/`, and flips rows to "Done".

## When to Use

- User says "set up a local research tracker", "register a research topic in my vault", "spin up a JSONL backlog for X".
- User has run `mcbrain-setup` and chose `Local` as the research-tracker backend, and now wants to add an additional topic.

## Prerequisites

- The vault must be a McBrain vault — exposed as a filesystem MCP server named `mcbrain-<topic>` (or `mcbrain` for a single default vault). The skill writes through that MCP.
- Python 3 must be available on the user's host (used by `local-research-runner`, not by this skill — but worth surfacing so the user knows the runner will work).
- No external installations, no extra MCP connectors, no tokens.

## Inputs to Collect

1. **Research topic** — free-form string (e.g. "CRISPR base editing", "AI evals literature"). Used to derive the topic slug and to label tracker rows. If the user did not supply one, ask.
2. **Associated McBrain vault** — see *Identifying the McBrain vault* below. Resolve this **before** writing anything.

That's the entire input set. There is no parent page, no database name, no schema choice.

## Identifying the McBrain vault

McBrain vaults are exposed as MCP filesystem servers named `mcbrain-<topic>` (or just `mcbrain` for a single default vault). To pick the right one:

1. **Enumerate connected MCPs** and collect every server whose name starts with `mcbrain` or `mcbrain-`.
2. **Zero matches.** No McBrain is connected. Tell the user the tracker can't be set up because there is no vault to write into. Stop.
3. **One match.** Use it. Mention the choice in your confirmation message ("I'll register this tracker with `mcbrain-ai-science`.") so the user can correct you if it's wrong.
4. **Multiple matches.** Try to infer from the research topic by simple substring/keyword overlap with the vault names — e.g. topic "AI evals literature" → `mcbrain-ai-science` is a much better match than `mcbrain-finance`. Only auto-pick when one candidate is an obvious winner; otherwise ask:

   > *"Which McBrain should this tracker belong to — [list the connected `mcbrain-*` MCPs]?"*

   Always confirm an inferred choice with the user before proceeding.

Record the chosen MCP server name; you'll use it in every write below. Operate against that MCP's root — never use absolute filesystem paths.

## Row schema (`tasks.jsonl`)

Each line of `tasks.jsonl` is a single JSON object with these fields:

| Field               | Type    | Description |
|---------------------|---------|-------------|
| `id`                | string  | Stable per-task identifier. Short ULID preferred (e.g. `01HFXR3Q2K`); a 12-char random hex string is acceptable if the host has no ULID library. Generated when the row is created; never changes. |
| `topic`             | string  | Free-form research topic this task belongs to (e.g. "AI evals literature"). |
| `topic_slug`        | string  | Lowercase-hyphenated slug derived from `topic` (e.g. `ai-evals-literature`). The runner uses this to filter rows and to name the output markdown file. |
| `task_name`         | string  | The research question / task title. |
| `status`            | enum    | `"To do"` \| `"In progress"` \| `"Done"`. New rows start at `"To do"`. |
| `priority`          | enum    | `"High"` \| `"Medium"` \| `"Low"`. Determines runner queue ordering. |
| `created_date`      | string  | ISO-8601 UTC timestamp at row creation. |
| `last_updated_date` | string  | ISO-8601 UTC timestamp; bumped on every modification. |
| `notes`             | string  | Free-form context for the runner / subagent (optional, may be `""`). |
| `notes_path`        | string  | Set by the runner once findings are written. Relative path under the vault root, e.g. `raw/notes/research-ai-evals-literature-01HFXR3Q2K.md`. Empty/absent until findings exist. |
| `sources_count`     | integer | Set by the runner once findings are written. Best-effort count of items in the markdown's `## Sources` section. May be unset on parse failure. |

**Important: this file may be hand-edited by the user.** They might add new "To do" rows directly with a text editor, change priorities, or write notes for the runner to consider. That is expected and supported. Do not rewrite the file in a way that strips fields the user added — keep round-tripping unknown fields untouched. (The runner's atomic-write helper preserves the full row dict when serializing.)

This skill itself does **not** create task rows. It only creates the file (if missing) and registers the topic in CLAUDE.md so the user (or the runner) can subsequently add rows. Adding tasks is a normal manual operation: append a JSONL line by hand, or use a future "add task" helper if one is built. Some users may prefer to ask Claude to draft the rows for them; that's fine — the skill is not the only path to populating the tracker.

## Workflow

Operate against the chosen `mcbrain-*` MCP. Use Read/Write/Edit on paths relative to the MCP root.

1. **Confirm the topic and vault** with the user (one short turn). State which vault you'll write to and what the topic slug will be.

2. **Derive the topic slug.** Lowercase the topic, replace runs of non-alphanumeric characters with single hyphens, strip leading/trailing hyphens. Example: `"CRISPR base editing"` → `crispr-base-editing`. If the slug collides with an already-registered topic in CLAUDE.md, append `-2`, `-3`, etc. until unique.

3. **Ensure the directory and file exist.**
   - Verify that `raw/research_tasks/` exists in the vault (create it if missing).
   - Verify that `raw/research_tasks/tasks.jsonl` exists (touch with empty content if missing — an empty JSONL file is valid).

4. **Register in `CLAUDE.md`.** Read `CLAUDE.md` from the vault root and update the `## Research tracker` section.

   - **If the section exists with `Backend: local`:** append the new topic to the existing `Topics:` list. Do not duplicate an entry that's already present (match on `Topic slug`).
   - **If the section exists with `Backend: none`:** rewrite `Backend: none` to `Backend: local` and populate the body with the `File:` line and a `Topics:` list containing this entry.
   - **If the section does not exist** (legacy vault): create it just before the `## Domain` section, with `Backend: local`, the `File:` line, and the new topic entry.

   The entry format is:

   ```markdown
   - **<Research Topic>**
     - Topic slug: <topic-slug>
     - Registered: <YYYY-MM-DD>   <!-- look up today's date; do not guess -->
     - Notes: companion local research tracker for this topic.
   ```

   And the surrounding section, when fully populated, looks like:

   ```markdown
   ## Research tracker

   Backend: local
   File: raw/research_tasks/tasks.jsonl
   Topics:
     - **AI evals literature**
       - Topic slug: ai-evals-literature
       - Registered: 2026-05-06
       - Notes: companion local research tracker for this topic.
     - **CRISPR base editing**
       - Topic slug: crispr-base-editing
       - Registered: 2026-05-06
       - Notes: companion local research tracker for this topic.
   ```

5. **Append to `wiki/log.md`** a one-line entry, matching whatever log format CLAUDE.md prescribes. Typical:

   ```
   - YYYY-MM-DD — registered local tracker topic: <topic> (slug: <topic-slug>)
   ```

6. **Backup.** Re-read CLAUDE.md's `## Backup` section. If `Strategy: git`, **present** the commit/push block to the user as a copy-paste fence — do not run git directly (the filesystem MCP races with git and can leave a stale `.git/index.lock`; see CLAUDE.md's `## Backup → How Claude handles git for this vault`). A good message:

   ```bash
   cd VAULT_PATH && \
     git add CLAUDE.md raw/research_tasks/tasks.jsonl wiki/log.md && \
     git commit -m "init: local research tracker (<topic>)" && \
     git push
   ```

   If `google-drive` or `none`, no git operations are needed at all.

7. **Tell the user what just happened.** Which vault, what files were touched, the topic slug, and a short note that they (or the runner) can now add `To do` rows by appending JSONL lines or asking Claude to do so. Do not pre-populate sample rows unless the user asks.

## Idempotency

Running this skill twice with the same topic and the same vault should be a no-op:

- The directory and file already exist → skip creation.
- The CLAUDE.md entry is already present (matched by `Topic slug` under the `## Research tracker → Topics:` list) → skip the append, tell the user the topic is already registered.
- Do not rewrite `tasks.jsonl` to "normalize" it — it may contain user data.

## Failure modes

- **Vault MCP unavailable.** Stop and tell the user the vault must be reachable to write CLAUDE.md.
- **CLAUDE.md is read-only or has an unexpected layout** (no `## Domain` anchor, no place to insert the section). Surface the conflict to the user, show the diff you wanted to apply, and ask them to apply it manually rather than guessing.
- **Slug collision.** If the slug already exists under a different topic name, ask the user whether the topics are actually the same (re-use existing slug) or different (add a numeric suffix to disambiguate).

## Non-Goals

- Do **not** create or populate task rows in `tasks.jsonl`. This skill's only writes to `tasks.jsonl` are creating an empty file if one does not exist.
- Do **not** modify rows in `tasks.jsonl` (status flips, findings updates) — that is the runner's job.
- Do **not** invoke any MCP tool other than the vault's filesystem MCP.
- Do **not** run git directly — always present a copy-paste fence.
