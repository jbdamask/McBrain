---
name: mcbrain
description: Operating skill for McBrain — the user's personal LLM-maintained knowledge base. Use this skill any time the user wants to ingest a source into McBrain, query McBrain, lint McBrain, file a synthesis page, or otherwise work with their personal knowledge base. Triggers on phrases like "ingest this", "ingest into mcbrain", "save to mcbrain", "add to mcbrain", "query mcbrain", "lint mcbrain", "what does mcbrain say about X", "ask my brain", "file this in mcbrain", "find insights from McBrain [name]", or any reference to the user's wiki, second brain, or knowledge base. Use it even if the user just says "my brain" or "the wiki" without naming McBrain — it's the only knowledge base they maintain.
---

# McBrain — Operating Skill

The user maintains one or more personal knowledge bases called **McBrain**. All vaults are tracked in a registry file (`~/.mcbrain/registry.json`) and served by a single `mcbrain` MCP server. This skill governs day-to-day operations against any vault.

## Step 1: Identify which vault the user wants

The user may have multiple McBrain vaults (e.g., "McBrain AI Science", "McBrain Finance", "McBrain Clinical Guidelines"). Each vault is registered under a name following the pattern `mcbrain-<topic>` (e.g., `mcbrain-ai-science`, `mcbrain-finance`). A single default vault may just be named `mcbrain`.

Get the list of registered vaults:
- **Claude Desktop / Cowork**: call the `mcbrain` MCP's `list_vaults` tool.
- **Claude Code**: read `~/.mcbrain/registry.json` directly — it's plain JSON of the shape `{"vaults": [{"name", "path", "created"}]}`. (`list_vaults` works here too if the `mcbrain` MCP is loaded.)

From the user's request, determine which vault they mean by fuzzy-matching their mention against the registered names:
- If they name a vault explicitly ("McBrain Finance", "my finance brain"), match it to the registered name — lowercasing and hyphenating gives `mcbrain-finance`.
- If they say "McBrain" with no qualifier and only one vault is registered, use it.
- If the mention is ambiguous or matches nothing cleanly, ask: *"Which McBrain vault — [list the registered vault names]?"*

Use that vault's registry entry — its name and absolute path — for all subsequent operations in this session.

## Step 2: Read CLAUDE.md

Before doing anything else, read the vault's schema file:

- **Claude Desktop**: `read_file(vault, "CLAUDE.md")` via the `mcbrain` MCP.
- **Claude Code**: native Read at `<vault path from registry>/CLAUDE.md`.
- **Cowork**: read `CLAUDE.md` from the granted vault folder.

Vault paths come from the registry entry — they are absolute paths on the user's machine.

CLAUDE.md is the source of truth for:
- The vault's directory layout
- Page conventions (YAML frontmatter, `[[wikilinks]]`)
- The backup strategy configured during setup (GitHub, Google Drive, or none)
- The canonical procedures for **ingest**, **query**, and **lint** operations

Follow what CLAUDE.md says — this skill is the trigger and the router, but CLAUDE.md is the spec.

If `CLAUDE.md` is missing or unreadable, stop and tell the user — something is wrong with the vault registration or setup.

## Why this two-layer design

McBrain is a living document. Its conventions evolve as the user refines them, and CLAUDE.md is checked into the vault so those conventions travel with the knowledge base. Hardcoding the schema into this skill would mean two places to keep in sync. Instead: this skill catches the user's intent, routes to the right vault, and defers to CLAUDE.md for the spec.

## Deferrals to CLAUDE.md

The following procedures are specified in the vault's CLAUDE.md and must be followed from there — do not rely on a cached version in this skill:

- **What-lives-where rule** — see CLAUDE.md's `## What lives where` section. CLAUDE.md is for operating instructions and plumbing (schema, procedures, registered companion systems); `wiki/` is for compiled knowledge derived from `raw/` sources. Don't write plumbing into `wiki/` and don't write derived knowledge into CLAUDE.md.
- **Raw-sources-first rule** — see CLAUDE.md's immutable rule forbidding wiki pages built from search results without a backing file in `raw/`.
- **Source ingestion paths** — how Obsidian Web Clipper, Claude in Chrome, and hand drops feed the ingest procedure.
- **Handling PDFs in `raw/papers/`** — the upload → Cowork `pdf` skill → `.md` → figure prose workflow.
- **Handling images in sources** — text-first reading, filtering decorative images.

If CLAUDE.md and this skill ever disagree, CLAUDE.md wins.

## Ingest

Ingest reads files from the vault's `raw/` directory, runs the procedure in CLAUDE.md's `## Operations → Ingest from raw/` section, and updates the wiki. There is one mode. When the user says "ingest" with no source argument, scan `raw/` for files that aren't yet referenced as a source in any `wiki/*.md` page, list them, and process per CLAUDE.md.

## Backup and version control

After reading CLAUDE.md, check the `## Backup` section for the `Strategy:` value. The strategy determines whether to do any git operations at all.

**Strategy: `git`**

The vault is a git repository. What Claude does about git depends on the surface:

- **Claude Code**: Claude **may run git directly** against the vault. After meaningful operations (ingest, lint, batch synth), commit and push, telling the user what was committed (user-visible messages — never silent git).
- **Cowork / Claude Desktop**: still **present** the commit/push commands in a copy-paste block — do not execute them. The sandbox cannot run processes on the host, so the user runs the block in their own terminal. Example:

```
cd <vault_path> && git add -A && git commit -m "<message>" && git push origin main
```

Mirror the log entry in the commit message: `ingest: <title>`, `lint: <summary>`, `synth: <topic>`.

CLAUDE.md's `## Backup → How Claude handles git for this vault` section is the source of truth; follow it. (Older vaults' CLAUDE.md may still cite a `.git/index.lock` race from the retired per-vault filesystem MCPs — that rationale no longer applies.)

**Strategy: `google-drive`**

No git operations. Drive for Desktop syncs the vault automatically — nothing extra is needed after writing files. Do not offer to commit.

**Strategy: `none`**

No git operations, no backup steps. Just write files. Do not offer to commit or mention backup.

## Always update index and log

Every operation (ingest, lint, filing a synthesis page) must update `wiki/index.md` and append to `wiki/log.md`. Don't skip this — the index is how future sessions discover what's in the vault, and the log is how the user audits what Claude did.

## Tone

The user values directness. Discuss the source briefly — key takeaways, tensions, things worth a dedicated page — before writing wiki pages, but don't pad it. After writing, list which pages were created or updated so the user can spot-check.
