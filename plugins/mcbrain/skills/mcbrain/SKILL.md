---
name: mcbrain
description: Operating skill for McBrain — the user's personal LLM-maintained knowledge base. Use this skill any time the user wants to ingest a source into McBrain, query McBrain, lint McBrain, file a synthesis page, or otherwise work with their personal knowledge base. Triggers on phrases like "ingest this", "ingest into mcbrain", "save to mcbrain", "add to mcbrain", "query mcbrain", "lint mcbrain", "what does mcbrain say about X", "ask my brain", "file this in mcbrain", "find insights from McBrain [name]", or any reference to the user's wiki, second brain, or knowledge base. Use it even if the user just says "my brain" or "the wiki" without naming McBrain — it's the only knowledge base they maintain.
---

# McBrain — Operating Skill

The user maintains one or more personal knowledge bases called **McBrain**. Each vault has its own MCP filesystem server. This skill governs day-to-day operations against any vault.

## Step 1: Identify which vault the user wants

The user may have multiple McBrain vaults (e.g., "McBrain AI Science", "McBrain Finance", "McBrain Clinical Guidelines"). Each vault has a corresponding MCP server whose name follows the pattern `mcbrain-<topic>` (e.g., `mcbrain-ai-science`, `mcbrain-finance`). A single default vault may just be named `mcbrain`.

From the user's request, determine which vault they mean:
- If they name a vault explicitly ("McBrain Finance", "my finance brain"), map it to the MCP name by lowercasing and hyphenating: `mcbrain-finance`.
- If they say "McBrain" with no qualifier and only one vault exists, use `mcbrain`.
- If ambiguous and multiple vaults are connected, ask: *"Which McBrain vault — [list the connected mcbrain-* MCPs]?"*

Use that MCP for all subsequent operations in this session.

## Step 2: Read CLAUDE.md

Before doing anything else, read the vault's schema file via the identified MCP:

```
CLAUDE.md
```

Read this from the vault root — do **not** hardcode an absolute path. The MCP root is the vault.

CLAUDE.md is the source of truth for:
- The vault's directory layout
- Page conventions (YAML frontmatter, `[[wikilinks]]`)
- The backup strategy configured during setup (GitHub, Google Drive, or none)
- The canonical procedures for **ingest**, **query**, and **lint** operations

Follow what CLAUDE.md says — this skill is the trigger and the router, but CLAUDE.md is the spec.

If `CLAUDE.md` is missing or unreadable, stop and tell the user — something is wrong with the MCP setup.

## Why this two-layer design

McBrain is a living document. Its conventions evolve as the user refines them, and CLAUDE.md is checked into the vault so those conventions travel with the knowledge base. Hardcoding the schema into this skill would mean two places to keep in sync. Instead: this skill catches the user's intent, routes to the right vault, and defers to CLAUDE.md for the spec.

## Deferrals to CLAUDE.md

The following procedures are specified in the vault's CLAUDE.md and must be followed from there — do not rely on a cached version in this skill:

- **What-lives-where rule** — see CLAUDE.md's `## What lives where` section. CLAUDE.md is for operating instructions and plumbing (schema, procedures, registered companion systems); `wiki/` is for compiled knowledge derived from `raw/` sources. Don't write plumbing into `wiki/` and don't write derived knowledge into CLAUDE.md.
- **Raw-sources-first rule** — see CLAUDE.md's immutable rule forbidding wiki pages built from search results without a backing file in `raw/`.
- **Source ingestion paths** — how Obsidian Web Clipper, Claude in Chrome, hand drops, and the Notion research tracker feed the ingest procedure.
- **Handling PDFs in `raw/papers/`** — the upload → Cowork `pdf` skill → `.md` → figure prose workflow.
- **Handling images in sources** — text-first reading, filtering decorative images.

If CLAUDE.md and this skill ever disagree, CLAUDE.md wins.

## Routing "ingest" to the right mode

The word **ingest** in McBrain has two valid meanings, and Claude must pick the right one before acting. Picking wrong is the most common bug — if the user is talking about completed Notion research and Claude treats it as a generic ingest, it ends up re-running the research subagents. Do not do that.

Decide between these two modes after reading CLAUDE.md, **before** doing any work:

**Mode A — Notion-bridged ingest.** Use when any of these are true:
- The current conversation has involved the `notion-research-runner` or `notion-research-db` skill, or research tasks were just completed in a Notion tracker registered to this vault.
- The user references "the Notion pages", "the research", "those tasks", "the tracker", or names a research tracker explicitly.
- The user says something like "ingest the research" / "ingest from Notion" / "pull the Notion findings into the wiki".

In this mode, the source pages live in Notion, not on disk. The first step is to copy them into `raw/` (typically `raw/notes/`), then run the standard ingest on the resulting files. **Hard rule: never spawn research subagents and never call `notion-research-runner` from this mode — the research is already done.** Follow the procedure documented in CLAUDE.md under `## Operations → Ingest from Notion research tracker`.

**Mode B — Standard ingest.** Use when there is no Notion context and the user says "ingest" with no source argument, or names a specific file already in `raw/`. Scan `raw/` for files that aren't yet referenced as a source in any `wiki/*.md` page, list them, and process per CLAUDE.md's `## Operations → Ingest from raw/` section.

**Stating the choice.** Always tell the user which mode you picked and why, in one line, before you start work — e.g. *"Treating this as a Notion-bridged ingest because we just ran the research runner against the CRISPR tracker."* If both signals are present (Notion context **and** new files in `raw/`), or neither (no Notion context **and** `raw/` has nothing unprocessed), ask the user which they meant rather than guessing.

## Backup and version control

After reading CLAUDE.md, check the `## Backup` section for the `Strategy:` value. The strategy determines whether to do any git operations at all.

**Strategy: `git`**

The vault is a git repository. After meaningful operations (ingest, lint, batch synth), offer to commit and push:

```
cd <vault_path> && git add -A && git commit -m "<message>" && git push origin main
```

Mirror the log entry in the commit message: `ingest: <title>`, `lint: <summary>`, `synth: <topic>`. The CLAUDE.md `## Backup` section has the exact remote URL and push command.

**Strategy: `google-drive`**

No git operations. Drive for Desktop syncs the vault automatically — nothing extra is needed after writing files. Do not offer to commit.

**Strategy: `none`**

No git operations, no backup steps. Just write files. Do not offer to commit or mention backup.

## Always update index and log

Every operation (ingest, lint, filing a synthesis page) must update `wiki/index.md` and append to `wiki/log.md`. Don't skip this — the index is how future sessions discover what's in the vault, and the log is how the user audits what Claude did.

## Tone

The user values directness. Discuss the source briefly — key takeaways, tensions, things worth a dedicated page — before writing wiki pages, but don't pad it. After writing, list which pages were created or updated so the user can spot-check.
