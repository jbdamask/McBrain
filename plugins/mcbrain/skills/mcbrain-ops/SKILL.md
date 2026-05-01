---
name: mcbrain-ops
description: Indexing and query engine that backs McBrain. Use this skill when the user asks to "rebuild the McBrain index", "check McBrain index status", "uninstall the McBrain query engine", "migrate a vault to the McBrain query engine", or otherwise wants to manage the per-vault search index. This skill is also invoked as a subroutine by the `mcbrain` and `mcbrain-setup` skills — `mcbrain-setup` calls it during initial provisioning, `mcbrain` calls it at query time and after every wiki edit. The six subcommands are: query, index sync, index rebuild, index status, migrate, uninstall.
---

# McBrain Operations

Manages the hybrid lexical + semantic search index for a McBrain vault. Every vault has its own per-vault Python venv at `<vault>/.mcbrain/venv/` and its own SQLite database at `<vault>/.mcbrain/index.db`. The embedding model itself (FastEmbed `BAAI/bge-small-en-v1.5`, 384 dims, ~30 MB ONNX) lives in FastEmbed's shared cache (`~/.cache/fastembed/`) so multiple vaults don't re-download it.

This skill is the **only** place Python lives in McBrain. Everything else is Markdown skills + reference templates.

## When to use this skill

Direct triggers (the user asks for one of these):
- "Rebuild the McBrain index" / "reindex McBrain" → `index rebuild`
- "Check the McBrain index status" / "is the index healthy?" → `index status`
- "Sync the McBrain index" / "the index is stale" → `index sync`
- "Migrate this vault to the new query engine" / "this vault doesn't have an index" → `migrate`
- "Uninstall the McBrain query engine" / "remove .mcbrain/ from this vault" → `uninstall`

Indirect triggers (other skills invoke this one):
- `mcbrain-setup` calls `migrate` during its Step 8.5 provisioning
- `mcbrain` calls `query` whenever the user asks the wiki a question (per CLAUDE.md's `## Operations → Query`)
- `mcbrain` calls `index sync` after every wiki write (per CLAUDE.md's ingest / update / delete procedures)
- `mcbrain` calls `migrate` when it detects a vault with `wiki/` but no `.mcbrain/`

## Subcommands

All subcommands accept `--vault <path>` (or `MCBRAIN_VAULT` env var) to target the vault. JSON output for `query` and `index status`; human-readable for the rest. Exit code 0 on success, non-zero on error.

### `query "<text>" [--k 8]`

Runs hybrid search (lexical + semantic, fused via Reciprocal Rank Fusion at k=60). Returns the top `--k` (default 8) wiki pages as a JSON list to stdout, each entry containing the file path, fused score, and a short excerpt.

```sh
python3 <vault>/.mcbrain/bin/mcbrain_ops.py query "what does the wiki say about scaling laws" --vault <vault> --k 8
```

The skill-level call from CLAUDE.md / the `mcbrain` skill should pass the user's question verbatim, then read the returned page paths.

### `index sync`

Incremental index update. Walks `<vault>/wiki/`, compares each file's content hash against the stored hash in the index, and re-embeds only the deltas (added / modified / deleted). Idempotent and fast — sub-second on a no-op call. Default after-edit path; called automatically by the CLAUDE.md ingest/update procedures.

### `index rebuild`

Wipes the index and re-embeds every wiki file from scratch. Used when the embedding model or schema changes, or when corruption is suspected. Slow proportional to wiki size (FastEmbed encodes thousands of chunks per minute on a CPU laptop). Safe to run; non-destructive to wiki content.

### `index status`

Prints a JSON object with: `doc_count`, `last_sync` (ISO timestamp), `embedding_model`, `embedding_dim`, `index_path`, `index_size_bytes`. Used by the migration check in the `mcbrain` skill and by humans diagnosing index issues.

### `migrate`

Provisions a vault that doesn't have a working query engine yet. Idempotent — re-running on a fully migrated vault is a no-op. Steps:

1. Create `<vault>/.mcbrain/{venv,bin}/` if missing.
2. Run `python3 -m venv .mcbrain/venv` if the venv isn't there.
3. Install pinned packages from `mcbrain-ops/references/requirements.txt` into the venv.
4. Copy `mcbrain_ops.py` and `schema.sql` from this skill's `references/` directory into `<vault>/.mcbrain/bin/`.
5. Patch the vault's `CLAUDE.md`: ensure a `## Query engine` section exists (with `mode`, `embedding_model`, `embedding_dim`, `index_path` keys); rewrite `## Operations → Query` to delegate to this skill; append `index sync` to ingest/update operations.
6. Run an initial `index rebuild` to seed the database.

This is the same logic `mcbrain-setup` Step 8.5 executes; the setup skill simply delegates here so there's one implementation.

### `uninstall`

Removes `<vault>/.mcbrain/` (venv, scripts, index). Does NOT touch `wiki/`, `raw/`, or any other vault content. Optionally cleans up the FastEmbed shared cache if it can detect that no other vaults reference it (best-effort — by default leaves the shared cache alone).

## Cross-platform paths

Per locked design decision #12 (cross-platform: macOS, Linux, Windows), every path the script constructs must use `pathlib.Path`, not string concatenation. The venv interpreter lives at:

- POSIX: `<vault>/.mcbrain/venv/bin/python`
- Windows: `<vault>/.mcbrain/venv/Scripts/python.exe`

The script must detect the host and pick the right path. Same applies to `pip` (`venv/bin/pip` vs `venv/Scripts/pip.exe`).

## What this skill does not do

- It does not invoke system package managers (no `brew`, `winget`, `apt`, `dnf`). Per locked decision #11, McBrain only detects and surfaces install instructions for system prerequisites; the user installs Python and ripgrep themselves.
- It does not modify `wiki/` or `raw/`. Read-only against vault content.
- It does not call out to any cloud service. Everything runs locally on the user's machine.

## Reference files

- `references/requirements.txt` — pinned `fastembed` and `sqlite-vec` versions
- `references/schema.sql` — SQLite schema (chunks + chunk_vec virtual table + meta)
- `references/mcbrain_ops.py` — the dispatcher script copied into each vault during provisioning
- `references/gitignore-snippet.md` — the gitignore lines that go into git-strategy vaults
