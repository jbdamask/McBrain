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

## Step 2.5: Migration check (query engine)

Before doing any procedural work, verify the vault is on the current MCP-based query engine. This is the **only** orchestration concern this skill owns for the query engine — all the actual query and indexing procedures live in CLAUDE.md (patched by the `mcbrain-engine` MCP's `migrate` tool to delegate into that MCP).

Read CLAUDE.md's `## Query engine` section and pick exactly one of three states:

1. **No `## Query engine` section** → the vault predates the engine entirely. Offer migration; on accept, call the `mcbrain-engine` MCP's `migrate` tool with `vault_path=<vault>` (and optionally `vault_name=<MCP name>`).
2. **`mode: lexical+semantic`** (the legacy PR #4 marker, **no** `(mcp)` suffix) → the vault was migrated to the per-vault Python layout. Offer "upgrade to the MCP-based engine"; on accept, call the `mcbrain-engine` MCP's `migrate` tool — it preserves `index.db` when the embedding model+dim still match and removes the legacy `bin/`/`venv/` directories.
3. **`mode: lexical+semantic (mcp)`** → already on the new architecture. No action — proceed with the user's original request.

Migration prompt (states 1 and 2):

> "This vault is on an older query-engine layout. Want me to upgrade it now? I'll call the `mcbrain-engine` MCP's `migrate` tool, which registers this vault, patches its `CLAUDE.md`, and (if a legacy `.mcbrain/{bin,venv}/` exists) collapses it after preserving the index when compatible. Takes seconds — wiki pages aren't touched. **If the `mcbrain-engine` MCP isn't registered in this harness, I'll surface that and you'll need to re-run `mcbrain-setup` Step 5.6.**"

After migration completes, **re-read `CLAUDE.md`** (it has been patched) and proceed with the user's original request using the now-updated procedure.

If the user declines migration, continue in lexical-only fallback mode: the `mcbrain-engine` MCP's `query` tool falls back to ripgrep when no index is provisioned, but with reduced recall on paraphrased questions. Let the user know that's what's happening and that they can run migration any time.

**Architectural principle (do not violate):** procedures (how to query, when to sync the index, how to ingest) live in CLAUDE.md. This skill is a router — it catches intent, routes to the right vault, runs the migration check, then defers to CLAUDE.md. Do not add query routing logic, post-edit `index_sync` orchestration, or any other procedural step to this skill — they belong in CLAUDE.md and the `mcbrain-engine` MCP respectively.

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

The vault is a git repository, but **Claude must not run git commands against it.** The filesystem MCP holds open handles that race with git and leave stale `.git/index.lock` files when commits are issued through Claude — the user then has to clear the lock manually before any further git work works. CLAUDE.md's `## Backup → How Claude handles git for this vault` section is the source of truth on this; follow it.

After meaningful operations (ingest, lint, batch synth), **present** the commit/push commands to the user in a copy-paste block — do not execute them. Example:

```
cd <vault_path> && git add -A && git commit -m "<message>" && git push origin main
```

Mirror the log entry in the commit message: `ingest: <title>`, `lint: <summary>`, `synth: <topic>`. The user runs the block in their own terminal.

**Strategy: `google-drive`**

No git operations. Drive for Desktop syncs the vault automatically — nothing extra is needed after writing files. Do not offer to commit.

**Strategy: `none`**

No git operations, no backup steps. Just write files. Do not offer to commit or mention backup.

## Always update index and log

Every operation (ingest, lint, filing a synthesis page) must update `wiki/index.md` and append to `wiki/log.md`. Don't skip this — the index is how future sessions discover what's in the vault, and the log is how the user audits what Claude did.

## Tone

The user values directness. Discuss the source briefly — key takeaways, tensions, things worth a dedicated page — before writing wiki pages, but don't pad it. After writing, list which pages were created or updated so the user can spot-check.
