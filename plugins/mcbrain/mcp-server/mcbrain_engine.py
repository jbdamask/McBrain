"""mcbrain-engine — hybrid lexical + semantic query engine.

This module hosts the pure-Python implementations lifted from PR #4's
mcbrain_ops.py. The MCP wire layer (FastMCP @tool decorators) is added in a
separate file so the impls below stay testable as plain Python functions.

Conventions:
- Implementation entry points are *_impl functions returning structured dicts
  (no exit codes, no JSON printing). Errors raise EngineError with a clear msg.
- info()/warn() write to stderr for human-readable progress, independent of
  the structured return.
- Heavy deps (fastembed, numpy) load lazily inside the helpers that need them.
- Schema lives next to this module at runtime (<runtime>/schema.sql), not
  per-vault — that was the PR #4 layout this rewrite replaces.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384
SCHEMA_VERSION = "1"
RRF_K = 60
LEXICAL_LIMIT = 50
SEMANTIC_LIMIT = 50


class EngineError(Exception):
    """Raised by impl functions on user-correctable failures."""


# ----------------------------- Path helpers ---------------------------------


def runtime_dir() -> Path:
    """Where this module lives — the schema.sql sibling lives here too."""
    return Path(__file__).resolve().parent


def schema_path() -> Path:
    return runtime_dir() / "schema.sql"


def mcbrain_dir(vault: Path) -> Path:
    return vault / ".mcbrain"


def index_db_path(vault: Path) -> Path:
    return mcbrain_dir(vault) / "index.db"


def wiki_dir(vault: Path) -> Path:
    return vault / "wiki"


def claude_md_path(vault: Path) -> Path:
    return vault / "CLAUDE.md"


# ------------------------------ Diagnostics ---------------------------------


def warn(msg: str) -> None:
    print(f"mcbrain-engine: warning: {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


# --------------------------------- DB ---------------------------------------


def open_db(vault: Path) -> sqlite3.Connection:
    db_path = index_db_path(vault)
    if not db_path.exists():
        raise EngineError(
            f"no index at {db_path}. Run the migrate tool against this vault first."
        )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    sql = schema_path().read_text(encoding="utf-8")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(sql)
        set_meta(conn, "embedding_model", EMBEDDING_MODEL)
        set_meta(conn, "embedding_dim", str(EMBEDDING_DIM))
        set_meta(conn, "schema_version", SCHEMA_VERSION)
        conn.commit()
    finally:
        conn.close()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def check_model_compatibility(conn: sqlite3.Connection, vault: Path) -> None:
    stored_model = get_meta(conn, "embedding_model")
    stored_dim = get_meta(conn, "embedding_dim")
    if stored_model != EMBEDDING_MODEL or stored_dim != str(EMBEDDING_DIM):
        raise EngineError(
            f"index was built with model={stored_model} dim={stored_dim} but "
            f"engine expects {EMBEDDING_MODEL} dim={EMBEDDING_DIM}. "
            f"Run index_rebuild against vault {vault}."
        )


# ----------------------------- Embedder cache -------------------------------


_embedder = None


def get_embedder():
    """Lazy singleton for the FastEmbed model. Cached process-wide."""
    global _embedder
    if _embedder is None:
        try:
            from fastembed import TextEmbedding  # type: ignore
        except ImportError as e:
            raise EngineError(
                "fastembed not installed in this Python. The runtime venv is "
                "incomplete — re-run mcbrain-setup to reinstall."
            ) from e
        _embedder = TextEmbedding(model_name=EMBEDDING_MODEL)
    return _embedder


def _np():
    try:
        import numpy as np  # type: ignore
    except ImportError as e:
        raise EngineError(
            "numpy not installed in this Python. The runtime venv is "
            "incomplete — re-run mcbrain-setup to reinstall."
        ) from e
    return np


def embed_one(text: str):
    np = _np()
    emb = list(get_embedder().embed([text]))[0]
    return np.asarray(emb, dtype=np.float32)


def embed_many(texts: list[str]):
    np = _np()
    return [np.asarray(v, dtype=np.float32) for v in get_embedder().embed(texts)]


def embedding_to_blob(vec) -> bytes:
    np = _np()
    return np.ascontiguousarray(vec, dtype=np.float32).tobytes()


def blob_to_embedding(blob: bytes):
    np = _np()
    return np.frombuffer(blob, dtype=np.float32)


# --------------------------- Wiki discovery ---------------------------------


def discover_wiki_files(vault: Path) -> list[Path]:
    wiki = wiki_dir(vault)
    if not wiki.is_dir():
        return []
    return sorted(p for p in wiki.rglob("*.md") if p.is_file())


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def relative_wiki_path(vault: Path, path: Path) -> str:
    """Vault-relative path with forward slashes (cross-platform stable key)."""
    return path.resolve().relative_to(vault.resolve()).as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ----------------------------- index sync -----------------------------------


def index_sync_impl(vault: Path) -> dict:
    if not index_db_path(vault).exists():
        init_db(index_db_path(vault))

    conn = open_db(vault)
    try:
        check_model_compatibility(conn, vault)

        files = discover_wiki_files(vault)
        wanted: dict[str, tuple[Path, str]] = {}
        for p in files:
            key = relative_wiki_path(vault, p)
            wanted[key] = (p, hash_file(p))

        existing = {
            row["path"]: row["content_hash"]
            for row in conn.execute(
                "SELECT path, content_hash FROM chunks"
            ).fetchall()
        }

        added = [k for k in wanted if k not in existing]
        changed = [
            k for k in wanted if k in existing and existing[k] != wanted[k][1]
        ]
        removed = [k for k in existing if k not in wanted]

        if added:
            info(f"sync: embedding {len(added)} new pages")
            for key in added:
                upsert_chunk(conn, key, wanted[key][0], wanted[key][1])
        if changed:
            info(f"sync: re-embedding {len(changed)} changed pages")
            for key in changed:
                upsert_chunk(conn, key, wanted[key][0], wanted[key][1])
        if removed:
            info(f"sync: removing {len(removed)} deleted pages")
            for key in removed:
                delete_chunk(conn, key)

        last_sync = iso_now()
        set_meta(conn, "last_sync", last_sync)
        conn.commit()
        return {
            "added": len(added),
            "changed": len(changed),
            "removed": len(removed),
            "total": len(wanted),
            "last_sync": last_sync,
        }
    finally:
        conn.close()


def upsert_chunk(
    conn: sqlite3.Connection,
    key: str,
    path: Path,
    content_hash: str,
) -> None:
    text = read_text(path)
    mtime = path.stat().st_mtime
    embedding = embed_one(text)
    blob = embedding_to_blob(embedding)

    existing = conn.execute(
        "SELECT id FROM chunks WHERE path = ?", (key,)
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO chunks(path, content_hash, text, mtime, embedding) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, content_hash, text, mtime, blob),
        )
    else:
        conn.execute(
            "UPDATE chunks SET content_hash = ?, text = ?, mtime = ?, "
            "embedding = ? WHERE id = ?",
            (content_hash, text, mtime, blob, existing["id"]),
        )


def delete_chunk(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM chunks WHERE path = ?", (key,))


# ----------------------------- index rebuild --------------------------------


def index_rebuild_impl(vault: Path) -> dict:
    db_path = index_db_path(vault)
    if db_path.exists():
        db_path.unlink()
    init_db(db_path)

    conn = open_db(vault)
    try:
        files = discover_wiki_files(vault)
        last_rebuild = iso_now()

        if not files:
            info("rebuild: no wiki files yet — empty index initialized")
            set_meta(conn, "last_rebuild", last_rebuild)
            set_meta(conn, "last_sync", last_rebuild)
            conn.commit()
            return {"indexed": 0, "last_rebuild": last_rebuild}

        info(f"rebuild: embedding {len(files)} wiki pages")
        texts = [read_text(p) for p in files]
        embeddings = embed_many(texts)
        for path, text, embedding in zip(files, texts, embeddings):
            key = relative_wiki_path(vault, path)
            content_hash = hash_file(path)
            mtime = path.stat().st_mtime
            conn.execute(
                "INSERT INTO chunks(path, content_hash, text, mtime, embedding) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, content_hash, text, mtime, embedding_to_blob(embedding)),
            )

        set_meta(conn, "last_rebuild", last_rebuild)
        set_meta(conn, "last_sync", last_rebuild)
        conn.commit()
        info(f"rebuild: done — {len(files)} pages indexed")
        return {"indexed": len(files), "last_rebuild": last_rebuild}
    finally:
        conn.close()


# ----------------------------- index status ---------------------------------


def index_status_impl(vault: Path) -> dict:
    db_path = index_db_path(vault)
    out: dict = {
        "vault": str(vault),
        "index_path": str(db_path),
        "provisioned": db_path.exists(),
    }
    if not db_path.exists():
        return out

    out["index_size_bytes"] = db_path.stat().st_size
    conn = open_db(vault)
    try:
        out["embedding_model"] = get_meta(conn, "embedding_model")
        dim = get_meta(conn, "embedding_dim")
        out["embedding_dim"] = int(dim) if dim is not None else None
        out["schema_version"] = get_meta(conn, "schema_version")
        out["last_sync"] = get_meta(conn, "last_sync")
        out["last_rebuild"] = get_meta(conn, "last_rebuild")
        out["doc_count"] = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        conn.close()
    return out


# ----------------------------- lexical search -------------------------------


def lexical_search(vault: Path, text: str) -> list[tuple[str, int]]:
    """Return [(rel_path, hit_count), ...] sorted by hit_count desc."""
    wiki = wiki_dir(vault)
    if not wiki.is_dir():
        return []

    if shutil.which("rg"):
        return _rg_search(wiki, text)
    if shutil.which("grep") and sys.platform != "win32":
        return _grep_search(wiki, text)
    return _python_search(wiki, text)


def _rg_search(wiki: Path, text: str) -> list[tuple[str, int]]:
    cmd = [
        "rg",
        "--type", "md",
        "--count-matches",
        "--no-heading",
        "--ignore-case",
        "--",
        text,
        str(wiki),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        warn("rg timed out; falling back to no lexical results")
        return []
    if result.returncode not in (0, 1):
        warn(f"rg exited {result.returncode}; falling back to no lexical results")
        return []
    return _parse_count_lines(result.stdout, wiki)


def _grep_search(wiki: Path, text: str) -> list[tuple[str, int]]:
    cmd = [
        "grep",
        "-r",
        "-i",
        "-c",
        "--include=*.md",
        text,
        str(wiki),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        warn("grep timed out; falling back to no lexical results")
        return []
    return _parse_count_lines(result.stdout, wiki)


def _parse_count_lines(stdout: str, wiki: Path) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    vault = wiki.parent
    for line in stdout.splitlines():
        if not line:
            continue
        try:
            path_str, count_str = line.rsplit(":", 1)
            count = int(count_str)
        except ValueError:
            continue
        if count <= 0:
            continue
        try:
            rel = Path(path_str).resolve().relative_to(vault.resolve()).as_posix()
        except ValueError:
            continue
        out.append((rel, count))
    out.sort(key=lambda x: (-x[1], x[0]))
    return out[:LEXICAL_LIMIT]


def _python_search(wiki: Path, text: str) -> list[tuple[str, int]]:
    needle = text.lower()
    vault = wiki.parent
    results: list[tuple[str, int]] = []
    for path in wiki.rglob("*.md"):
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        count = content.count(needle)
        if count > 0:
            try:
                rel = path.resolve().relative_to(vault.resolve()).as_posix()
            except ValueError:
                continue
            results.append((rel, count))
    results.sort(key=lambda x: (-x[1], x[0]))
    return results[:LEXICAL_LIMIT]


# ----------------------------- semantic search ------------------------------


def semantic_search(conn: sqlite3.Connection, text: str) -> list[tuple[str, float]]:
    """Brute-force cosine similarity over the entire chunks table."""
    np = _np()
    rows = conn.execute("SELECT path, embedding FROM chunks").fetchall()
    if not rows:
        return []

    paths_list = [row["path"] for row in rows]
    matrix = np.stack([blob_to_embedding(row["embedding"]) for row in rows])

    query_vec = embed_one(text)

    matrix_norms = np.linalg.norm(matrix, axis=1)
    query_norm = np.linalg.norm(query_vec)
    safe_denom = np.where(matrix_norms == 0, 1.0, matrix_norms) * (
        query_norm if query_norm != 0 else 1.0
    )
    sims = (matrix @ query_vec) / safe_denom

    order = np.argsort(-sims)[:SEMANTIC_LIMIT]
    return [(paths_list[i], float(sims[i])) for i in order]


# ----------------------------- query / RRF ----------------------------------


def rrf_fuse(rank_lists: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion. Each rank_list is an ordered list of paths."""
    scores: dict[str, float] = {}
    for ranks in rank_lists:
        for rank, path in enumerate(ranks):
            scores[path] = scores.get(path, 0.0) + 1.0 / (k + rank + 1)
    return scores


def make_excerpt(text: str, max_chars: int = 240) -> str:
    """Skip frontmatter, headings, blank lines; return first substantive line."""
    lines = text.strip().splitlines()
    in_frontmatter = False
    if lines and lines[0].strip() == "---":
        in_frontmatter = True
        lines = lines[1:]

    body = ""
    for line in lines:
        stripped = line.strip()
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
            continue
        if not stripped or stripped.startswith("#"):
            continue
        body = stripped
        break

    if not body:
        body = " ".join(text.split())
    return (body[:max_chars] + "…") if len(body) > max_chars else body


def query_impl(vault: Path, text: str, k: int = 8) -> dict:
    if not text.strip():
        raise EngineError("query text was empty")

    db_path = index_db_path(vault)
    semantic_ranks: list[str] = []
    excerpts: dict[str, str] = {}

    conn: sqlite3.Connection | None = None
    try:
        if db_path.exists():
            conn = open_db(vault)
            check_model_compatibility(conn, vault)
            sem = semantic_search(conn, text)
            semantic_ranks = [path for path, _ in sem]
        else:
            warn(
                "no index — running lexical-only. "
                f"Run migrate against vault {vault} to enable semantic search."
            )

        lex = lexical_search(vault, text)
        lexical_ranks = [path for path, _ in lex]

        rank_inputs = [r for r in (lexical_ranks, semantic_ranks) if r]
        fused = rrf_fuse(rank_inputs)
        ordered = sorted(fused.items(), key=lambda x: (-x[1], x[0]))[:k]
        top_paths = [path for path, _ in ordered]

        if conn is not None and top_paths:
            placeholders = ",".join("?" * len(top_paths))
            for row in conn.execute(
                f"SELECT path, text FROM chunks WHERE path IN ({placeholders})",
                top_paths,
            ).fetchall():
                excerpts[row["path"]] = row["text"]
    finally:
        if conn is not None:
            conn.close()

    results = []
    for path, score in ordered:
        text_for_excerpt = excerpts.get(path)
        if text_for_excerpt is None:
            full = vault / path
            if full.is_file():
                try:
                    text_for_excerpt = read_text(full)
                except OSError:
                    text_for_excerpt = ""
            else:
                text_for_excerpt = ""
        results.append(
            {
                "path": path,
                "score": round(score, 6),
                "excerpt": make_excerpt(text_for_excerpt),
            }
        )

    return {
        "query": text,
        "k": k,
        "mode": "lexical+semantic" if semantic_ranks else "lexical",
        "results": results,
    }


# ----------------------- CLAUDE.md patcher (lifted) -------------------------


QUERY_ENGINE_SECTION = f"""## Query engine

- mode: lexical+semantic (mcp)
- embedding_model: {EMBEDDING_MODEL}
- embedding_dim: {EMBEDDING_DIM}
- index_path: .mcbrain/index.db

The query engine is a global stdio MCP server (`mcbrain-engine`) shared by every
McBrain on this machine. It runs hybrid lexical (ripgrep / grep / pure-Python
fallback) plus semantic (FastEmbed, brute-force cosine via numpy over a SQLite
embedding column) search, fused via Reciprocal Rank Fusion. The per-vault index
lives at `.mcbrain/index.db`. Use the `mcbrain-engine` MCP's tools (`query`,
`index_sync`, `index_rebuild`, `index_status`, `migrate`, `uninstall`,
`list_vaults`) to interact with it.
"""


NEW_QUERY_OPERATION = """### Query
When asked a question against the wiki:
1. Call the `mcbrain-engine` MCP's `query` tool with `vault=<this vault's name>` (or `vault_path`) and `text=<question>`. It returns a ranked list of wiki page paths + scores as JSON.
2. Read the top 5–8 pages from that list.
3. Synthesize an answer with `[[wikilinks]]` citations.
4. Offer to file the answer as a new wiki page if it's worth keeping.

`wiki/index.md` is no longer the retrieval mechanism — it stays for human browsing and lint only.

Answers don't have to be prose. Pick the format that fits the question:
- **Markdown page** — the default; file-able back into the wiki as a new synthesis page
- **Comparison table** — for "how does X differ from Y?" questions
- **Marp slide deck** (`.md` with Marp frontmatter) — for presentations or sequential walkthroughs
- **Chart** (matplotlib via `python3`, or a rendered image saved to `raw/assets/`) — for questions about trends, distributions, or quantitative comparisons
- **Canvas / diagram** — for questions about relationships, flows, or architecture

If the output form is reusable knowledge (not just a one-off), offer to file it as a wiki page so the exploration compounds.
"""


INDEX_SYNC_STEP_TEXT = (
    "Call the `mcbrain-engine` MCP's `index_sync` tool against this vault so "
    "the new wiki page(s) are searchable immediately."
)


GENERAL_SYNC_RULE = (
    "**Always sync the search index after any wiki write.** Whenever you "
    "create, modify, or delete a file under `wiki/` — whether through a "
    "formal ingest, a lint pass that updates `log.md`, an ad-hoc page "
    "update, or a synthesis filing — call the `mcbrain-engine` MCP's "
    "`index_sync` tool against this vault afterward so subsequent queries "
    "see the change. Cheap (sub-second on no-op). The specific operation "
    "procedures below all end with this step explicitly; this paragraph is "
    "the catch-all for anything not enumerated."
)


GENERAL_SYNC_RULE_MARKER = "Always sync the search index after any wiki write"


def patch_claude_md(vault: Path) -> bool:
    """Idempotently update CLAUDE.md to advertise the MCP-mode query engine.

    Returns True if any change was written, False if the file was already up
    to date. Detects PR #4-style markers (`mode: lexical+semantic` without the
    `(mcp)` suffix) and replaces the whole `## Query engine` section so an
    upgrade migration also lands here.
    """
    cmd = claude_md_path(vault)
    if not cmd.is_file():
        warn(f"no CLAUDE.md at {cmd} — skipping CLAUDE.md patch")
        return False
    original = cmd.read_text(encoding="utf-8")
    text = original

    text = _ensure_query_engine_section(text)
    text = _replace_query_section(text)
    text = _ensure_index_sync_tail(text)
    text = _ensure_general_sync_rule(text)

    if text == original:
        return False
    cmd.write_text(text, encoding="utf-8")
    return True


def _ensure_query_engine_section(text: str) -> str:
    """Insert or replace the `## Query engine` section.

    If the section is absent: append. If present with the legacy PR #4 marker
    (`mode: lexical+semantic` without `(mcp)`): replace that whole section
    with the new one. If the new marker is already there: leave alone.
    """
    new_marker = "mode: lexical+semantic (mcp)"
    if new_marker in text:
        return text
    header = "## Query engine"
    start = text.find(header)
    if start < 0:
        return text.rstrip() + "\n\n" + QUERY_ENGINE_SECTION
    end = _find_next_heading(text, start + len(header))
    return text[:start] + QUERY_ENGINE_SECTION + text[end:].lstrip("\n")


def _replace_query_section(text: str) -> str:
    """Replace the `### Query` block under `## Operations` with the MCP version.

    The block ends at the next heading at any level so we don't swallow nested
    subsections. Idempotent: returns text unchanged once the new instructions
    are already in place.
    """
    marker = "### Query"
    start = text.find(marker)
    if start < 0:
        return text
    end = _find_next_heading(text, start + len(marker))
    block = text[start:end]
    if "mcbrain-engine" in block and "`query` tool" in block:
        return text
    return (
        text[:start]
        + NEW_QUERY_OPERATION.rstrip()
        + "\n\n"
        + text[end:].lstrip("\n")
    )


def _ensure_general_sync_rule(text: str) -> str:
    """Insert the catch-all 'always sync after any wiki write' rule under `## Operations`."""
    if GENERAL_SYNC_RULE_MARKER in text and "`index_sync` tool" in text:
        return text
    if GENERAL_SYNC_RULE_MARKER in text:
        # PR #4 wording is present — replace it with the MCP-flavored version.
        start = text.find(GENERAL_SYNC_RULE_MARKER)
        # Walk back to the start of the bold marker line.
        para_start = text.rfind("\n\n", 0, start)
        para_start = 0 if para_start < 0 else para_start + 2
        para_end = text.find("\n\n", start)
        if para_end < 0:
            para_end = len(text)
        return text[:para_start] + GENERAL_SYNC_RULE + text[para_end:]
    header = "## Operations"
    start = text.find(header)
    if start < 0:
        return text
    line_end = text.find("\n", start)
    if line_end < 0:
        return text
    insertion_point = line_end + 1
    if text[insertion_point : insertion_point + 1] == "\n":
        insertion_point += 1
    return (
        text[:insertion_point]
        + "\n"
        + GENERAL_SYNC_RULE
        + "\n\n"
        + text[insertion_point:].lstrip("\n")
    )


def _find_next_heading(text: str, search_from: int) -> int:
    i = search_from
    while True:
        nl = text.find("\n", i)
        if nl < 0:
            return len(text)
        if text[nl + 1 : nl + 2] == "#":
            return nl + 1
        i = nl + 1


def _ensure_index_sync_tail(text: str) -> str:
    """Append an `index_sync` step to `Ingest from raw/` if not already present."""
    import re

    header = "#### Ingest from raw/"
    start = text.find(header)
    if start < 0:
        return text
    end = _find_next_heading(text, start + len(header))
    block = text[start:end]
    if "mcbrain-engine" in block and "`index_sync` tool" in block:
        return text
    # Strip any legacy PR #4-style step that mentioned `mcbrain-ops`.
    block = re.sub(
        r"^\s*\d+\.\s+Invoke the `mcbrain-ops`.*\n?",
        "",
        block,
        flags=re.MULTILINE,
    )
    block = re.sub(
        r"^\s*-\s+Invoke the `mcbrain-ops`.*\n?",
        "",
        block,
        flags=re.MULTILINE,
    )

    numbered = re.findall(r"^\s*(\d+)\.\s", block, flags=re.MULTILINE)
    next_num = (max(int(n) for n in numbered) + 1) if numbered else None
    if next_num is not None:
        new_step = f"{next_num}. {INDEX_SYNC_STEP_TEXT}\n"
    else:
        new_step = f"- {INDEX_SYNC_STEP_TEXT}\n"

    appended = block.rstrip() + "\n" + new_step + "\n"
    return text[:start] + appended + text[end:]


# --------------------------------- helpers ----------------------------------


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------- Vault resolution -------------------------------


UNREGISTERED = "<unregistered>"


def resolve_vault(arg: str) -> tuple[str, Path]:
    """Resolve a registry name or absolute path to (canonical_name, abs_path).

    - If `arg` matches a registry key, returns (name, registry_path).
    - If `arg` is a path that canonicalizes to a registered vault, returns
      (registered_name, abs_path).
    - If `arg` is a path not in the registry, returns (UNREGISTERED, abs_path).
      Only valid for `migrate`; the other tools must check and raise.
    """
    if not arg:
        raise EngineError("vault argument was empty")

    # Lazy import so this module stays importable when registry.py is mid-edit.
    import registry

    by_name = registry.find_vault_by_name(arg)
    if by_name is not None:
        return arg, Path(by_name["path"]).resolve()

    candidate = Path(arg).expanduser()
    if not candidate.is_absolute():
        # Reject relative paths — ambiguous from a stdio server's CWD.
        raise EngineError(
            f"vault '{arg}' is not a registered name and not an absolute path"
        )
    abs_path = candidate.resolve()
    found = registry.find_vault_by_path(abs_path)
    if found is not None:
        return found[0], abs_path
    return UNREGISTERED, abs_path


def require_registered(name: str, vault: Path) -> None:
    if name == UNREGISTERED:
        raise EngineError(
            f"vault {vault} is not registered. Run the migrate tool with "
            "vault_path and (optionally) vault_name to register it first."
        )


# ------------------------------- migrate ------------------------------------


def migrate_impl(vault_path: Path, vault_name: str | None = None) -> dict:
    """Bring a vault under MCP-mode management.

    Idempotent: collapses legacy PR #4 .mcbrain/{bin,venv}/ when present,
    preserves index.db when the embedding model+dim still match, registers
    the vault, patches CLAUDE.md, and runs an initial index_sync.
    """
    import registry

    vault_path = Path(vault_path).expanduser().resolve()
    if not vault_path.is_dir():
        raise EngineError(f"vault path is not a directory: {vault_path}")

    name = vault_name or vault_path.name

    mcb = mcbrain_dir(vault_path)
    mcb.mkdir(parents=True, exist_ok=True)

    legacy_bin = mcb / "bin"
    legacy_venv = mcb / "venv"
    legacy_layout_removed = False
    rebuilt_for_mismatch = False
    db_path = index_db_path(vault_path)

    if legacy_bin.exists() or legacy_venv.exists():
        compatible = False
        if db_path.exists():
            try:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                try:
                    stored_model = get_meta(conn, "embedding_model")
                    stored_dim = get_meta(conn, "embedding_dim")
                finally:
                    conn.close()
                compatible = (
                    stored_model == EMBEDDING_MODEL
                    and stored_dim == str(EMBEDDING_DIM)
                )
            except sqlite3.Error as exc:
                warn(f"could not read index.db meta: {exc}")
                compatible = False

        if not compatible and db_path.exists():
            warn(
                "legacy index meta mismatch (or unreadable) — rebuilding from scratch"
            )
            db_path.unlink()
            rebuilt_for_mismatch = True

        if legacy_bin.exists():
            shutil.rmtree(legacy_bin)
        if legacy_venv.exists():
            shutil.rmtree(legacy_venv)
        legacy_layout_removed = True

    registry.put_vault(name, vault_path)
    claude_md_patched = patch_claude_md(vault_path)
    initial_sync = index_sync_impl(vault_path)

    return {
        "vault_name": name,
        "vault_path": str(vault_path),
        "registry_updated": True,
        "claude_md_patched": claude_md_patched,
        "legacy_layout_removed": legacy_layout_removed,
        "rebuilt_for_mismatch": rebuilt_for_mismatch,
        "initial_sync": initial_sync,
    }


# ------------------------------ uninstall -----------------------------------


def uninstall_impl(vault: Path, name: str, force: bool = False) -> dict:
    """Remove <vault>/.mcbrain/ and the vault's registry entry.

    Dry-run unless force=True. Never touches wiki/ or raw/. The shared
    FastEmbed cache at ~/.cache/fastembed/ is intentionally left alone since
    other vaults may rely on it.
    """
    import registry

    target = mcbrain_dir(vault)
    if not force:
        return {
            "removed_path": None,
            "removed_from_registry": False,
            "fastembed_cache_kept": True,
            "would_remove": str(target) if target.exists() else None,
            "would_unregister": name if name != UNREGISTERED else None,
            "dry_run": True,
        }

    removed_path = None
    if target.exists():
        shutil.rmtree(target)
        removed_path = str(target)

    removed_from_registry = False
    if name != UNREGISTERED:
        removed_from_registry = registry.remove_vault(name)

    return {
        "removed_path": removed_path,
        "removed_from_registry": removed_from_registry,
        "fastembed_cache_kept": True,
        "dry_run": False,
    }


# ----------------------------- list_vaults ----------------------------------


def list_vaults_impl() -> list[dict]:
    out: list[dict] = []
    for name, entry in sorted(read_registry_safe().items()):
        try:
            path = Path(entry["path"])
        except (KeyError, TypeError):
            continue
        provisioned = (path / ".mcbrain" / "index.db").is_file()
        out.append(
            {
                "name": name,
                "path": entry.get("path"),
                "registered": entry.get("registered"),
                "provisioned": provisioned,
            }
        )
    return out


def read_registry_safe() -> dict:
    import registry

    try:
        return registry.read_registry()["vaults"]
    except (FileNotFoundError, ValueError):
        return {}


# ---------------------------- MCP wire layer --------------------------------


# FastMCP is the official MCP SDK's high-level decorator API. It owns stdio
# transport, schema generation from type hints, and JSON-RPC plumbing — leaving
# our @mcp.tool functions as thin one-liners over the impls above.
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    FastMCP = None  # Allows the module to be imported in environments without the SDK

if FastMCP is not None:
    mcp = FastMCP("mcbrain-engine")

    @mcp.tool()
    def query(vault: str, text: str, k: int = 8) -> dict:
        """Hybrid lexical + semantic search across the named vault."""
        name, path = resolve_vault(vault)
        require_registered(name, path)
        return query_impl(path, text, k)

    @mcp.tool()
    def index_sync(vault: str) -> dict:
        """Incrementally sync the index — embed only added/changed pages."""
        name, path = resolve_vault(vault)
        require_registered(name, path)
        return index_sync_impl(path)

    @mcp.tool()
    def index_rebuild(vault: str) -> dict:
        """Wipe and re-embed the entire vault."""
        name, path = resolve_vault(vault)
        require_registered(name, path)
        return index_rebuild_impl(path)

    @mcp.tool()
    def index_status(vault: str) -> dict:
        """Return counts and timestamps for the vault's index."""
        name, path = resolve_vault(vault)
        require_registered(name, path)
        return index_status_impl(path)

    @mcp.tool()
    def migrate(vault_path: str, vault_name: str | None = None) -> dict:
        """Provision a vault under MCP-mode management (idempotent)."""
        return migrate_impl(Path(vault_path), vault_name)

    @mcp.tool()
    def uninstall(vault: str, force: bool = False) -> dict:
        """Remove a vault's .mcbrain/ directory and registry entry."""
        name, path = resolve_vault(vault)
        return uninstall_impl(path, name, force=force)

    @mcp.tool()
    def list_vaults() -> list[dict]:
        """Return every registered vault and its provisioned status."""
        return list_vaults_impl()

TOOL_NAMES = (
    "query",
    "index_sync",
    "index_rebuild",
    "index_status",
    "migrate",
    "uninstall",
    "list_vaults",
)


def main() -> None:
    if FastMCP is None:
        raise SystemExit(
            "mcp SDK not installed. The runtime venv is incomplete — re-run "
            "mcbrain-setup to reinstall."
        )
    mcp.run()


if __name__ == "__main__":
    main()
