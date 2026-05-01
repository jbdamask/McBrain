# gitignore snippet for McBrain query engine

This file is consumed by `mcbrain-setup` Step 3 (when the user picked git as their backup strategy) and by `mcbrain-ops migrate` (when retrofitting an existing git-strategy vault). It documents which paths under `.mcbrain/` should be ignored by git and which should be tracked.

## Lines to append to `<vault>/.gitignore`

```
.mcbrain/venv/
.mcbrain/index.db
.mcbrain/index.db-*
__pycache__/
```

## What stays tracked

`.mcbrain/bin/` is **intentionally not ignored** — the indexer script (`mcbrain_ops.py`) and schema (`schema.sql`) must travel with the vault so a fresh clone can rebuild the venv and reproduce the index. Everything that's reproducible from the script + the wiki content (the venv itself, the index database, sqlite WAL/SHM files, Python bytecode) is ignored.

## Why each line

| Line | Reason |
|---|---|
| `.mcbrain/venv/` | Python venv. Platform-specific binaries (macOS x86_64 vs arm64 vs Linux vs Windows). Reproducible from `requirements.txt`. |
| `.mcbrain/index.db` | The SQLite index. Reproducible from the wiki via `mcbrain-ops index rebuild`. Also large for big vaults; bloats git history. |
| `.mcbrain/index.db-*` | sqlite-vec writes WAL (`*-wal`) and shared-memory (`*-shm`) sidecar files; these are transient. |
| `__pycache__/` | Python bytecode cache. Generated on demand. |
