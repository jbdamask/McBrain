---
name: notion-research-db
description: Create a Notion database for tracking tasks related to a specific research topic, and register it with the associated McBrain knowledge base. Use when the user wants to set up a Notion research tracker, create a Notion database for a project or topic, spin up a task database in Notion, or says things like "make me a Notion DB for researching X", "create a research tracker in Notion", or "new Notion database for my X project".
---

# Notion Research DB

Create a new Notion database scoped to a user-supplied research topic. The database has a fixed schema suited to tracking research tasks: Task name, Status, Priority, Created date, Last updated date, Notes.

This skill is designed to be used **alongside McBrain**. After the database is created, its name and URL are written back into the associated McBrain vault so the wiki knows where its companion research tracker lives. If no McBrain vault is connected, the skill still creates the database and just skips the write-back (with a note to the user).

## When to Use

- User wants a Notion database for a new research topic or project
- User asks to create a task tracker in Notion
- User mentions spinning up a research workspace in Notion

## Prerequisite: a Notion MCP connection

This skill needs **some** Notion MCP connector to be available — it does not matter which one (Anthropic's claude.ai Notion connector, Notion's official `@notionhq/notion-mcp-server`, a community server, etc.). The tool naming differs per connector.

At the start of the skill, scan the available tools for ones that look like Notion operations. Match by capability, not by exact name. The capabilities needed are:

- **Search Notion** — find pages/databases by query. Typical names: `*notion*search*`, `*search-pages*`, `API-post-search`.
- **Create a database** — typical names: `*create-database*`, `API-post-database`.

If no Notion-like tools are present, stop and tell the user: "I need a Notion MCP connector to be connected. Please enable one (the claude.ai Notion connector, Notion's official MCP server, or equivalent) and re-run."

Once matching tools are identified, use them throughout the workflow. If a tool call fails with a schema error, read the tool's actual schema and adapt the payload — different connectors wrap the Notion API differently (some take raw Notion API JSON, some take simplified parameters).

## Inputs to Collect

Before calling the tool, make sure you have:

1. **Research topic** — used to name the database (e.g. "CRISPR base editing", "AI evals literature"). If the user did not already provide one in the prompt, ask.
2. **Parent location** — where in Notion the database should live. Ask the user which page should be the parent, or offer to search. Use the identified Notion search tool with the topic or a user-supplied hint to find candidate parent pages, then confirm the choice before creating.
3. **Associated McBrain vault** — which McBrain knowledge base this tracker belongs to. See *Identifying the McBrain vault* below. Resolve this **before** creating the database so the post-create write-back doesn't get stranded.

Do not guess the parent page. If search returns nothing useful, ask the user to paste a Notion page URL or page ID.

## Identifying the McBrain vault

McBrain vaults are exposed as MCP filesystem servers named `mcbrain-<topic>` (or just `mcbrain` for a single default vault). To pick the right one:

1. **Enumerate connected MCPs** and collect every server whose name starts with `mcbrain` or `mcbrain-`.
2. **Zero matches.** No McBrain is connected. Tell the user the database will still be created but the name/URL won't be written back anywhere. Skip *Step 4 — Register with McBrain* at the end.
3. **One match.** Use it. Mention the choice in your confirmation message ("I'll register this tracker with `mcbrain-ai-science`.") so the user can correct you if it's wrong.
4. **Multiple matches.** Try to infer from the research topic by simple substring/keyword overlap with the vault names — e.g. topic "AI evals literature" → `mcbrain-ai-science` is a much better match than `mcbrain-finance`. Only auto-pick when one candidate is an obvious winner; otherwise ask:

   > *"Which McBrain should this tracker belong to — [list the connected `mcbrain-*` MCPs]?"*

   Always confirm an inferred choice with the user before proceeding ("I'm guessing this belongs to `mcbrain-ai-science` — confirm or pick a different one.").

Record the chosen MCP server name; you'll use it in Step 4.

## Database Schema

Create the database with exactly these properties:

| Property          | Type                | Notes                                       |
|-------------------|---------------------|---------------------------------------------|
| Task name         | `title`             | Required title property                     |
| Status            | `status`            | Default Notion status groups (To-do/In progress/Done) |
| Priority          | `select`            | Options: `High` (red), `Medium` (yellow), `Low` (blue) |
| Created date      | `created_time`      | Auto-populated by Notion                    |
| Last updated date | `last_edited_time`  | Auto-populated by Notion                    |
| Notes             | `rich_text`         | Free-form notes                             |

Name the database `<Research Topic> — Research Tracker` unless the user specifies a different title.

## Workflow

1. Confirm the research topic, parent page, and associated McBrain vault with the user (search Notion / enumerate MCPs as needed).
2. Call the connector's create-database tool with the parent page ID, the title, and the properties above. Capture the returned **database ID** and **database URL**.
3. Report back with the new database URL so the user can open it. Do not add sample rows unless the user asks.
4. **Register with McBrain** (skip if no McBrain MCP was identified above).

### Step 4 — Register with McBrain

The point of this step: future sessions of the `mcbrain` skill (and the `notion-research-runner` skill) should be able to discover the companion tracker by reading the vault, not by being told again. Write this once, here, at creation time.

Operate against the `mcbrain-<topic>` MCP chosen earlier. Do all paths relative to the MCP root (the vault root) — never use absolute filesystem paths.

1. **Read `CLAUDE.md`** at the vault root first. The canonical registration location is its `## Notion companion databases` section (added by `mcbrain-setup` Step 8 in vaults set up after that step shipped). Append a new entry there:

   ```markdown
   - **<Research Topic> — Research Tracker**
     - URL: <database URL>
     - Database ID: <database ID>
     - Registered: <YYYY-MM-DD>   <!-- look up today's date; do not guess -->
     - Notes: companion research tracker for this vault. Used by `notion-research-runner` and the Notion-bridged ingest mode.
   ```

   If CLAUDE.md does not yet have a `## Notion companion databases` section (legacy vault), create it just before the `## Domain` section using the same entry format.

2. **Legacy fallback.** If CLAUDE.md cannot be edited (read-only / unexpected layout) and the vault already has a `wiki/notion-databases.md` from an older setup, append to that file instead and tell the user you used the legacy path. Don't scatter files in vaults that have their own conventions — when in doubt, ask the user.

3. **Append to `wiki/log.md`** a one-line entry, matching whatever log format CLAUDE.md prescribes (typical: `- YYYY-MM-DD — registered Notion tracker: <Research Topic> — <URL>`).

4. **Update `wiki/index.md`** to mention the registration only if you wrote to `wiki/notion-databases.md` (the legacy path). When the registration lives in CLAUDE.md, no index update is needed — CLAUDE.md is read on every session.

5. **Backup.** Re-read CLAUDE.md's `## Backup` section. If `Strategy: git`, offer to commit and push with a message like `register: notion tracker <Research Topic>`. If `google-drive` or `none`, do not run git operations.

After registration, tell the user: which vault you wrote to, which file(s) you touched, and (if applicable) whether you committed.

## Notes on Property Types

- `title` must be present exactly once — it is `Task name` here.
- `created_time` and `last_edited_time` are Notion system properties; they take no options.
- If the connector's schema rejects `status`, fall back to a `select` property named `Status` with options `To do`, `In progress`, `Done` and tell the user you used select instead.
