-- McBrain Ops — index schema
--
-- Single SQLite database at <vault>/.mcbrain/index.db, populated and queried by
-- mcbrain_ops.py. Embeddings are stored as raw float32 BLOBs and ranked at
-- query time by computing cosine similarity in numpy. This avoids the
-- sqlite-vec extension-loading requirement that breaks on Python builds
-- without `enable_load_extension` (notably pyenv on macOS and the macOS
-- system Python).
--
-- Per-page chunking (one row per wiki/*.md file) is the v1 strategy; if pages
-- routinely exceed ~1500 tokens the schema still works but recall on long pages
-- will suffer. A future change can split on H2 boundaries by appending more
-- rows per path; the path UNIQUE constraint would need to be relaxed at that
-- point.

CREATE TABLE IF NOT EXISTS chunks (
  id            INTEGER PRIMARY KEY,
  path          TEXT    NOT NULL UNIQUE,    -- vault-relative path, e.g. "wiki/scaling-laws.md"
  content_hash  TEXT    NOT NULL,           -- SHA-256 of file contents; drives incremental sync
  text          TEXT    NOT NULL,           -- full chunk text (the embedded content)
  mtime         REAL    NOT NULL,           -- file mtime at index time; informational, hash is authoritative
  embedding     BLOB    NOT NULL            -- float32[384] little-endian, packed by numpy.ndarray.tobytes()
);

CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);

-- Single-row meta table: holds the embedding model name, dimension, last sync,
-- and schema version. mcbrain_ops.py reads this on every invocation to detect
-- mismatches and trigger rebuilds when needed.
CREATE TABLE IF NOT EXISTS meta (
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL
);
