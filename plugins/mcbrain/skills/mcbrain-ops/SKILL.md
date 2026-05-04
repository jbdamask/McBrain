---
name: mcbrain-ops
description: indexing/maintenance lifecycle for the McBrain query engine. Calls mcbrain-engine MCP tools — index_sync, index_rebuild, index_status, migrate, uninstall — to manage a vault's search index. The query tool is called from the mcbrain skill via CLAUDE.md, not from here. Use when the user asks to "rebuild the McBrain index", "check McBrain index status", "sync the McBrain index", "migrate this vault", or "uninstall the McBrain query engine".
---

# McBrain Operations

Thin maintenance frontend for the `mcbrain-engine` MCP. Routes the user's
intent to one of five MCP tools. **Does not call Python directly** — all the
actual logic lives in the engine MCP, registered once per machine by
`mcbrain-setup` (its Step 5.6).

## Architecture in one paragraph

PR #4 lived on per-vault venvs and a per-vault `.mcbrain/bin/mcbrain_ops.py`.
McBrain v2 collapses that: a single `mcbrain-engine` MCP server runs per
machine and serves every McBrain vault by name (or path). This skill no longer
owns Python — its job is now to surface the right MCP tool call given the
user's intent.

## Vault resolution

Every tool here takes a `vault` argument that accepts a registry name
(`mcbrain-ai-science`) or an absolute vault path. Resolve the user's intent in
this order:

1. **Registry first.** Call the `mcbrain-engine` MCP's `list_vaults` tool to
   see every registered vault on this machine.
2. **"This vault" disambiguation.** If the user says "this vault" mid-session,
   intersect `list_vaults` with the active filesystem MCP's
   `list_allowed_directories` — the FS MCP only exposes the vault it's
   pointed at, so the intersection is the user's "this".
3. **Ask if ambiguous.** If multiple vaults match and the user didn't name
   one, ask which.

Pass the canonical name (or the absolute path as fallback) to the tool.

## Tool calls

The `mcbrain-engine` MCP exposes eight maintenance tools handled here. The
ninth tool, `query`, is called from the `mcbrain` skill via CLAUDE.md —
**not from this skill** — because it's part of the user's question-answering
flow, not maintenance.

### `index_sync(vault)`

Incremental index update. Walks `<vault>/wiki/`, embeds only the deltas
(added / modified / deleted). Idempotent and fast — sub-second on a no-op
call. CLAUDE.md's catch-all sync rule expects callers to invoke this after
every wiki write. Returns `{added, changed, removed, total, last_sync}`.

### `index_rebuild(vault)`

Wipes and re-embeds every wiki page. Use when the embedding model or schema
changes, or when corruption is suspected. Slow proportional to wiki size.
Safe to run; non-destructive to wiki content. Returns `{indexed, last_rebuild}`.

### `index_status(vault)`

Returns a JSON object with `doc_count`, `last_sync`, `last_rebuild`,
`embedding_model`, `embedding_dim`, `index_path`, `index_size_bytes`,
`schema_version`, `provisioned`. Used for human diagnostics and by the
`mcbrain` skill's migration check.

### `migrate(vault_path, vault_name?)`

Provisions a vault under MCP-mode management. Idempotent — re-running on a
fully migrated vault is a no-op. Steps:

1. Ensures `<vault>/.mcbrain/` exists.
2. If a legacy PR #4 `.mcbrain/{bin,venv}/` is present: reads `index.db`'s
   meta to check the embedding model+dim. If they match, the index is
   preserved and `bin/` + `venv/` are removed. If they don't, the index is
   wiped and rebuilt from scratch.
3. Writes/updates the vault entry in the platform-resolved vault registry.
4. Patches `<vault>/CLAUDE.md` to the MCP-flavored Query operation and the
   `## Query engine` section (mode marker `lexical+semantic (mcp)`).
5. Runs an initial `index_sync`.

Returns `{vault_name, vault_path, registry_updated, claude_md_patched,
legacy_layout_removed, rebuilt_for_mismatch, initial_sync}`. Surface the
JSON to the user — the mismatch and legacy-cleanup flags matter for trust.

### `uninstall(vault, force=false)`

Removes `<vault>/.mcbrain/` (the per-vault index) and the vault's registry
entry. Dry-run unless `force=true`. Never touches `wiki/` or `raw/`. The
shared FastEmbed cache (managed by FastEmbed itself) is intentionally left
in place so other vaults don't redownload the model.

### `enable_notion_for_vault(vault, database_id)`

Marks a registered vault as Notion-enabled and records the database id to
drain. Required before `ingest_from_notion` will run against the vault.
Idempotent — re-running with the same id is a no-op. Vault must already
be registered (`migrate` first). Returns `{vault_name, vault_path,
notion_enabled, notion_db_id}`.

The token itself is *not* set by this tool — it lives in a per-machine
file at `~/Library/Application Support/mcbrain/notion-token` (macOS) or
`%APPDATA%\mcbrain\notion-token` (Windows). `mcbrain-setup` Step 8f
captures it on first run; subsequent vaults reuse it.

### `disable_notion_for_vault(vault)`

Clears the registry's Notion config on a vault. The integration token
file and any already-imported markdown under `<vault>/raw/notes/` are
left in place — only the registry flag is reset. Use when the user
stops wanting Notion ingest for a specific vault but keeps it for others.

### `ingest_from_notion(vault, database_id?, page_ids?, filter?)`

**This is the high-leverage tool.** Calls the Notion REST API directly
from the user's host (not via Claude's Notion connector), converts each
page's blocks to markdown, and writes the files to `<vault>/raw/notes/`.
**Page bodies do not pass through the LLM context** — only summary
counts come back. That's a major token-cost and latency win for bulk
ingest of 10+ pages.

Refuses to run if the vault isn't Notion-enabled (call
`enable_notion_for_vault` first). Refuses if the integration token file
is missing.

Arguments:
- `vault` — registry name or absolute path (required)
- `database_id` — optional override; default uses the vault's registered
  `notion_db_id`
- `page_ids` — optional list of specific page IDs; if omitted, drains
  the database
- `filter` — optional Notion DB filter object

Idempotent: pages whose `last_edited_time` matches the local file's
frontmatter are skipped, so re-running is cheap and safe.

Returns `{vault_name, vault_path, database_id, imported_count,
imported_files, skipped_count, skipped_files, errors}`. Surface the
counts and any errors to the user. Each imported file lands at
`<vault>/raw/notes/<slug>-<short_id>.md` with frontmatter recording
`notion_id`, `notion_url`, `last_edited_time`, and `imported_at` so
re-runs can detect changes.

**After `ingest_from_notion` writes files, run `index_sync` to fold the
new raw notes into the vault's search index** — the catch-all
"sync after writes" rule applies.

## When the engine MCP isn't reachable

If a tool call fails because the `mcbrain-engine` MCP isn't registered in the
current harness (Claude Code vs Claude Desktop are separate), tell the user:

> "The `mcbrain-engine` MCP isn't registered in this harness. Re-run
> `mcbrain-setup` Step 5.6 to register it (idempotent across additional
> McBrains). The other harness will keep working — registration is per-app."

## What this skill does not do

- It does not invoke system package managers (no `brew`, `winget`, `apt`).
  The `mcbrain-setup` skill detects ripgrep and surfaces install instructions;
  the user installs missing tools themselves.
- It does not modify `wiki/` or `raw/`. Read-only against vault content.
- It does not call out to any cloud service. Everything runs locally.
