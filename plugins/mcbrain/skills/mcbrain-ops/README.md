# McBrain Ops

Indexing and query engine that backs McBrain.

Provisioned automatically by `mcbrain-setup` when a fresh vault is created. Invoked automatically by the `mcbrain` skill after wiki edits (to keep the index current) and at query time (to find the most relevant pages for a question).

Hybrid search: lexical (ripgrep / grep fallback) + semantic (FastEmbed `BAAI/bge-small-en-v1.5` + sqlite-vec), fused via Reciprocal Rank Fusion. Per-vault index, shared model cache. Cross-platform: macOS, Linux, Windows.

This is the only place Python lives in McBrain. Everything else is Markdown.

## Operations

| Subcommand | Purpose |
|---|---|
| `query "<text>"` | Hybrid lexical + semantic search; returns top-K wiki pages as JSON |
| `index sync` | Incremental update — re-embed only changed/added/deleted wiki files |
| `index rebuild` | Wipe and re-embed everything from scratch |
| `index status` | Print doc count, last sync, model, index size |
| `migrate` | Provision `.mcbrain/` on a vault that doesn't have one yet (idempotent) |
| `uninstall` | Remove `.mcbrain/`; leave wiki/raw untouched |

See `SKILL.md` for the full contract and `references/mcbrain_ops.py` for the implementation.
