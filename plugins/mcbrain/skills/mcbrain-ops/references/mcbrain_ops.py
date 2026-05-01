#!/usr/bin/env python3
"""mcbrain-ops — hybrid lexical + semantic query engine for a McBrain vault.

Single-file dispatcher with six subcommands: query, index sync, index rebuild,
index status, migrate, uninstall. Per-vault venv at <vault>/.mcbrain/venv/,
per-vault SQLite index at <vault>/.mcbrain/index.db, shared FastEmbed model
cache at ~/.cache/fastembed/ (FastEmbed default).

Heavy dependencies (fastembed, sqlite-vec) are imported lazily inside the
subcommand handlers so this file can also bootstrap a vault from system python
(migrate creates the venv and pip-installs the deps) and so --help / uninstall
work without them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384
SCHEMA_VERSION = "1"
RRF_K = 60
LEXICAL_LIMIT = 50
SEMANTIC_LIMIT = 50


# ----------------------------- Path helpers ---------------------------------


def is_windows() -> bool:
    return platform.system() == "Windows"


def resolve_vault(arg: str | None) -> Path:
    candidate = arg or os.environ.get("MCBRAIN_VAULT")
    if not candidate:
        die(
            "vault path not provided. Pass --vault <path> or set MCBRAIN_VAULT.",
            code=2,
        )
    p = Path(candidate).expanduser().resolve()
    if not p.is_dir():
        die(f"vault path is not a directory: {p}", code=2)
    return p


def mcbrain_dir(vault: Path) -> Path:
    return vault / ".mcbrain"


def venv_dir(vault: Path) -> Path:
    return mcbrain_dir(vault) / "venv"


def venv_python(vault: Path) -> Path:
    if is_windows():
        return venv_dir(vault) / "Scripts" / "python.exe"
    return venv_dir(vault) / "bin" / "python"


def venv_pip(vault: Path) -> Path:
    if is_windows():
        return venv_dir(vault) / "Scripts" / "pip.exe"
    return venv_dir(vault) / "bin" / "pip"


def bin_dir(vault: Path) -> Path:
    return mcbrain_dir(vault) / "bin"


def index_db_path(vault: Path) -> Path:
    return mcbrain_dir(vault) / "index.db"


def wiki_dir(vault: Path) -> Path:
    return vault / "wiki"


def claude_md_path(vault: Path) -> Path:
    return vault / "CLAUDE.md"


def script_source_dir() -> Path:
    """Where this script lives — used by migrate to copy reference files."""
    return Path(__file__).resolve().parent


# ------------------------------ Diagnostics ---------------------------------


def die(msg: str, code: int = 1) -> None:
    print(f"mcbrain-ops: error: {msg}", file=sys.stderr)
    sys.exit(code)


def warn(msg: str) -> None:
    print(f"mcbrain-ops: warning: {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


# --------------------------------- DB ---------------------------------------


def open_db(vault: Path) -> sqlite3.Connection:
    db_path = index_db_path(vault)
    if not db_path.exists():
        die(
            f"no index at {db_path}. Run `mcbrain-ops migrate --vault {vault}` "
            "to provision this vault.",
            code=3,
        )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path, schema_sql_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    with open(schema_sql_path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    set_meta(conn, "embedding_model", EMBEDDING_MODEL)
    set_meta(conn, "embedding_dim", str(EMBEDDING_DIM))
    set_meta(conn, "schema_version", SCHEMA_VERSION)
    conn.commit()
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


# ----------------------------- Embedder cache -------------------------------


_embedder = None


def get_embedder():
    """Lazy singleton for the FastEmbed model. Cached process-wide."""
    global _embedder
    if _embedder is None:
        try:
            from fastembed import TextEmbedding  # type: ignore
        except ImportError:
            die(
                "fastembed not installed in this Python. Run "
                "`mcbrain-ops migrate --vault <vault>` to provision the venv.",
                code=4,
            )
        _embedder = TextEmbedding(model_name=EMBEDDING_MODEL)
    return _embedder


def _np():
    try:
        import numpy as np  # type: ignore
    except ImportError:
        die(
            "numpy not installed in this Python. Run "
            "`mcbrain-ops migrate --vault <vault>` to provision the venv.",
            code=4,
        )
    return np


def embed_one(text: str) -> "np.ndarray":  # noqa: F821
    """Embed a single string. Returns a float32 numpy array of length EMBEDDING_DIM."""
    np = _np()
    emb = list(get_embedder().embed([text]))[0]
    return np.asarray(emb, dtype=np.float32)


def embed_many(texts: list[str]) -> list["np.ndarray"]:  # noqa: F821
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


def cmd_sync(vault: Path) -> int:
    schema_path = bin_dir(vault) / "schema.sql"
    if not index_db_path(vault).exists():
        if not schema_path.exists():
            die(
                f"index missing and schema.sql not found at {schema_path}. "
                f"Run `mcbrain-ops migrate --vault {vault}` first.",
                code=3,
            )
        init_db(index_db_path(vault), schema_path)

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
            for row in conn.execute("SELECT path, content_hash FROM chunks").fetchall()
        }

        added = [k for k in wanted if k not in existing]
        changed = [k for k in wanted if k in existing and existing[k] != wanted[k][1]]
        removed = [k for k in existing if k not in wanted]

        if added:
            info(f"sync: embedding {len(added)} new pages")
            for key in added:
                upsert_chunk(conn, vault, key, wanted[key][0], wanted[key][1])
        if changed:
            info(f"sync: re-embedding {len(changed)} changed pages")
            for key in changed:
                upsert_chunk(conn, vault, key, wanted[key][0], wanted[key][1])
        if removed:
            info(f"sync: removing {len(removed)} deleted pages")
            for key in removed:
                delete_chunk(conn, key)

        set_meta(conn, "last_sync", iso_now())
        conn.commit()
        if not (added or changed or removed):
            info("sync: no changes")
        info(
            f"sync: done — added={len(added)} changed={len(changed)} "
            f"removed={len(removed)} total={len(wanted)}"
        )
        return 0
    finally:
        conn.close()


def upsert_chunk(
    conn: sqlite3.Connection,
    vault: Path,
    key: str,
    path: Path,
    content_hash: str,
) -> None:
    text = read_text(path)
    mtime = path.stat().st_mtime
    embedding = embed_one(text)
    blob = embedding_to_blob(embedding)

    existing = conn.execute("SELECT id FROM chunks WHERE path = ?", (key,)).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO chunks(path, content_hash, text, mtime, embedding) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, content_hash, text, mtime, blob),
        )
    else:
        conn.execute(
            "UPDATE chunks SET content_hash = ?, text = ?, mtime = ?, embedding = ? "
            "WHERE id = ?",
            (content_hash, text, mtime, blob, existing["id"]),
        )


def delete_chunk(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM chunks WHERE path = ?", (key,))


# ----------------------------- index rebuild --------------------------------


def cmd_rebuild(vault: Path) -> int:
    db_path = index_db_path(vault)
    schema_path = bin_dir(vault) / "schema.sql"
    if not schema_path.exists():
        die(
            f"schema.sql not found at {schema_path}. Run "
            f"`mcbrain-ops migrate --vault {vault}` first.",
            code=3,
        )
    if db_path.exists():
        db_path.unlink()
    init_db(db_path, schema_path)

    conn = open_db(vault)
    try:
        files = discover_wiki_files(vault)
        if not files:
            info("rebuild: no wiki files yet — empty index initialized")
            set_meta(conn, "last_rebuild", iso_now())
            set_meta(conn, "last_sync", iso_now())
            conn.commit()
            return 0

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

        set_meta(conn, "last_rebuild", iso_now())
        set_meta(conn, "last_sync", iso_now())
        conn.commit()
        info(f"rebuild: done — {len(files)} pages indexed")
        return 0
    finally:
        conn.close()


def check_model_compatibility(conn: sqlite3.Connection, vault: Path) -> None:
    stored_model = get_meta(conn, "embedding_model")
    stored_dim = get_meta(conn, "embedding_dim")
    if stored_model != EMBEDDING_MODEL or stored_dim != str(EMBEDDING_DIM):
        die(
            f"index was built with model={stored_model} dim={stored_dim} but "
            f"current code expects {EMBEDDING_MODEL} dim={EMBEDDING_DIM}. "
            f"Run `mcbrain-ops index rebuild --vault {vault}`.",
            code=6,
        )


# ----------------------------- index status ---------------------------------


def cmd_status(vault: Path) -> int:
    db_path = index_db_path(vault)
    out: dict = {
        "vault": str(vault),
        "index_path": str(db_path),
        "provisioned": db_path.exists(),
    }
    if not db_path.exists():
        print(json.dumps(out, indent=2))
        return 0

    out["index_size_bytes"] = db_path.stat().st_size
    conn = open_db(vault)
    try:
        out["embedding_model"] = get_meta(conn, "embedding_model")
        out["embedding_dim"] = int(get_meta(conn, "embedding_dim") or 0)
        out["schema_version"] = get_meta(conn, "schema_version")
        out["last_sync"] = get_meta(conn, "last_sync")
        out["last_rebuild"] = get_meta(conn, "last_rebuild")
        out["doc_count"] = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        conn.close()

    print(json.dumps(out, indent=2))
    return 0


# ----------------------------- lexical search -------------------------------


def lexical_search(vault: Path, text: str) -> list[tuple[str, int]]:
    """Return [(rel_path, hit_count), ...] sorted by hit_count desc.

    Uses ripgrep when available; falls back to grep on POSIX or a Python
    walker when neither is found. The actual hit count is what we use for
    a within-modality rank — the absolute value isn't fed into RRF, just
    the rank ordering.
    """
    wiki = wiki_dir(vault)
    if not wiki.is_dir():
        return []

    if shutil.which("rg"):
        return _rg_search(wiki, text)
    if shutil.which("grep") and not is_windows():
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
    """Return [(rel_path, similarity), ...] sorted by similarity desc (higher = better).

    Brute-force cosine similarity over the entire chunks table. Fast in numpy
    even at low-thousands of pages.
    """
    np = _np()
    rows = conn.execute("SELECT path, embedding FROM chunks").fetchall()
    if not rows:
        return []

    paths = [row["path"] for row in rows]
    matrix = np.stack([blob_to_embedding(row["embedding"]) for row in rows])

    query_vec = embed_one(text)

    # Cosine similarity: dot(a, b) / (||a|| * ||b||).
    # FastEmbed bge-small-en-v1.5 returns normalized vectors, so dot is enough,
    # but normalize defensively in case the model or version changes.
    matrix_norms = np.linalg.norm(matrix, axis=1)
    query_norm = np.linalg.norm(query_vec)
    safe_denom = np.where(matrix_norms == 0, 1.0, matrix_norms) * (
        query_norm if query_norm != 0 else 1.0
    )
    sims = (matrix @ query_vec) / safe_denom

    order = np.argsort(-sims)[:SEMANTIC_LIMIT]
    return [(paths[i], float(sims[i])) for i in order]


# ----------------------------- query / RRF ----------------------------------


def rrf_fuse(rank_lists: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion. Each rank_list is an ordered list of paths
    (rank 0 = best). Returns {path: fused_score}."""
    scores: dict[str, float] = {}
    for ranks in rank_lists:
        for rank, path in enumerate(ranks):
            scores[path] = scores.get(path, 0.0) + 1.0 / (k + rank + 1)
    return scores


def make_excerpt(text: str, max_chars: int = 240) -> str:
    snippet = text.strip().splitlines()
    body = ""
    for line in snippet:
        line = line.strip()
        if not line or line.startswith("---") or line.startswith("#"):
            continue
        body = line
        break
    if not body:
        body = " ".join(text.split())
    return (body[:max_chars] + "…") if len(body) > max_chars else body


def cmd_query(vault: Path, text: str, k: int) -> int:
    if not text.strip():
        die("query text was empty", code=2)

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
                f"Run `mcbrain-ops migrate --vault {vault}` to enable semantic search."
            )

        lex = lexical_search(vault, text)
        lexical_ranks = [path for path, _ in lex]

        rank_inputs = [r for r in (lexical_ranks, semantic_ranks) if r]
        fused = rrf_fuse(rank_inputs)
        ordered = sorted(fused.items(), key=lambda x: (-x[1], x[0]))[:k]
        top_paths = [path for path, _ in ordered]

        # Lazy text fetch: only SELECT text for the K paths that survived RRF,
        # not every chunk in the index. Keeps per-query memory bounded by k×
        # average page size instead of N× average page size.
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

    print(
        json.dumps(
            {
                "query": text,
                "k": k,
                "mode": "lexical+semantic" if semantic_ranks else "lexical",
                "results": results,
            },
            indent=2,
        )
    )
    return 0


# -------------------------------- migrate -----------------------------------


def cmd_migrate(vault: Path, no_rebuild: bool = False) -> int:
    info(f"migrate: target vault {vault}")
    mcb = mcbrain_dir(vault)
    bin_d = bin_dir(vault)
    bin_d.mkdir(parents=True, exist_ok=True)

    src_dir = script_source_dir()
    script_src = src_dir / "mcbrain_ops.py"
    schema_src = src_dir / "schema.sql"
    requirements_src = src_dir / "requirements.txt"
    for required in (script_src, schema_src, requirements_src):
        if not required.is_file():
            die(
                f"reference file missing: {required}. The migrate command must "
                "be run from the mcbrain-ops skill directory or from a copy of "
                "mcbrain_ops.py that has its references/ siblings present.",
                code=7,
            )

    script_dst = bin_d / "mcbrain_ops.py"
    schema_dst = bin_d / "schema.sql"
    if not script_dst.exists() or hash_file(script_src) != hash_file(script_dst):
        shutil.copy2(script_src, script_dst)
        info(f"migrate: copied mcbrain_ops.py -> {script_dst}")
    if not schema_dst.exists() or hash_file(schema_src) != hash_file(schema_dst):
        shutil.copy2(schema_src, schema_dst)
        info(f"migrate: copied schema.sql -> {schema_dst}")

    ensure_venv(vault)
    install_requirements(vault, requirements_src)

    patched = patch_claude_md(vault)
    if patched:
        info(f"migrate: patched {claude_md_path(vault)}")
    else:
        info(f"migrate: {claude_md_path(vault)} already up to date")

    if no_rebuild:
        info("migrate: skipping initial index rebuild (--no-rebuild)")
    else:
        info("migrate: running initial `index rebuild` in venv")
        result = run_in_venv(vault, ["index", "rebuild"])
        if result != 0:
            warn(f"initial rebuild exited {result} — run it manually to retry")
            return result

    info("migrate: done")
    return 0


def ensure_venv(vault: Path) -> None:
    vpy = venv_python(vault)
    if vpy.is_file():
        return
    venv_dir(vault).parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("python3"):
        py = "python3"
    elif shutil.which("python"):
        py = "python"
    else:
        die(
            "no python3 found on PATH. Install Python 3.10+ from python.org "
            "(macOS / Windows) or via your distro package manager (Linux), "
            "then re-run migrate.",
            code=8,
        )
    info(f"migrate: creating venv at {venv_dir(vault)}")
    result = subprocess.run(
        [py, "-m", "venv", str(venv_dir(vault))],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        die(
            f"failed to create venv: {result.stderr.strip() or result.stdout.strip()}",
            code=8,
        )
    if not vpy.is_file():
        die(
            f"venv created but interpreter missing at {vpy}. The Python install "
            "may be incomplete — try reinstalling Python.",
            code=8,
        )


def install_requirements(vault: Path, requirements: Path) -> None:
    pip = venv_pip(vault)
    if not pip.is_file():
        die(f"venv pip missing at {pip}", code=8)
    info("migrate: installing pinned requirements (this can take a minute)")
    result = subprocess.run(
        [str(pip), "install", "--quiet", "-r", str(requirements)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        die(
            f"pip install failed:\n{result.stdout}\n{result.stderr}",
            code=8,
        )


def run_in_venv(vault: Path, subargs: list[str]) -> int:
    """Re-invoke the installed mcbrain_ops.py inside the venv."""
    vpy = venv_python(vault)
    script = bin_dir(vault) / "mcbrain_ops.py"
    if not vpy.is_file() or not script.is_file():
        die(
            f"venv or installed script missing — cannot run subcommand {subargs}.",
            code=8,
        )
    cmd = [str(vpy), str(script), *subargs, "--vault", str(vault)]
    return subprocess.call(cmd)


# ----------------------- CLAUDE.md patcher (migrate) -------------------------


QUERY_ENGINE_SECTION = f"""## Query engine

- mode: lexical+semantic
- embedding_model: {EMBEDDING_MODEL}
- embedding_dim: {EMBEDDING_DIM}
- index_path: .mcbrain/index.db

The query engine is hybrid: lexical (ripgrep / grep / pure-Python fallback)
plus semantic (FastEmbed for embeddings, brute-force cosine via numpy over a
SQLite-stored embedding column). Results from the two modalities are fused via
Reciprocal Rank Fusion. The per-vault index lives at `.mcbrain/index.db` and
is provisioned and maintained by the `mcbrain-ops` skill. Re-index with
`mcbrain-ops index rebuild` if the schema or model ever changes.
"""


NEW_QUERY_OPERATION = """### Query
When asked a question against the wiki:
1. Invoke the `mcbrain-ops` skill (`query "<text>"`). It returns a ranked list of wiki page paths + scores as JSON.
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
    "Invoke the `mcbrain-ops` skill (`index sync`) so the new wiki page(s) "
    "are searchable immediately."
)


def patch_claude_md(vault: Path) -> bool:
    """Idempotently update CLAUDE.md to advertise the query engine.

    Returns True if any change was written, False if the file was already
    up to date.
    """
    cmd = claude_md_path(vault)
    if not cmd.is_file():
        warn(f"no CLAUDE.md at {cmd} — skipping CLAUDE.md patch")
        return False
    original = cmd.read_text(encoding="utf-8")
    text = original

    if "## Query engine" not in text:
        text = text.rstrip() + "\n\n" + QUERY_ENGINE_SECTION

    text = _replace_query_section(text)
    text = _ensure_index_sync_tail(text)

    if text == original:
        return False
    cmd.write_text(text, encoding="utf-8")
    return True


def _replace_query_section(text: str) -> str:
    """Replace the legacy `### Query` block under `## Operations` with the new one.

    The block ends at the next heading at any level (H1–H6), so the replacement
    doesn't accidentally swallow nested `#### ...` subsections that happen to
    follow it.
    """
    marker = "### Query"
    start = text.find(marker)
    if start < 0:
        return text
    end = _find_next_heading(text, start + len(marker))
    if "mcbrain-ops" in text[start:end]:
        return text
    return (
        text[:start]
        + NEW_QUERY_OPERATION.rstrip()
        + "\n\n"
        + text[end:].lstrip("\n")
    )


def _find_next_heading(text: str, search_from: int) -> int:
    """Return the index of the next markdown heading line, or len(text)."""
    i = search_from
    while True:
        nl = text.find("\n", i)
        if nl < 0:
            return len(text)
        if text[nl + 1 : nl + 2] == "#":
            return nl + 1
        i = nl + 1


def _ensure_index_sync_tail(text: str) -> str:
    """Append an `index sync` step to the `Ingest from raw/` numbered list,
    if not already present. Increments the highest numbered step in the block
    so it slots in cleanly regardless of the source list length."""
    import re

    header = "#### Ingest from raw/"
    start = text.find(header)
    if start < 0:
        return text
    end = _find_next_heading(text, start + len(header))
    block = text[start:end]
    if "mcbrain-ops" in block and "index sync" in block:
        return text

    numbered = re.findall(r"^\s*(\d+)\.\s", block, flags=re.MULTILINE)
    next_num = (max(int(n) for n in numbered) + 1) if numbered else None
    if next_num is not None:
        new_step = f"{next_num}. {INDEX_SYNC_STEP_TEXT}\n"
    else:
        new_step = f"- {INDEX_SYNC_STEP_TEXT}\n"

    appended = block.rstrip() + "\n" + new_step + "\n"
    return text[:start] + appended + text[end:]


# -------------------------------- uninstall ---------------------------------


def cmd_uninstall(vault: Path, force: bool) -> int:
    target = mcbrain_dir(vault)
    if not target.exists():
        info(f"uninstall: nothing to do — {target} doesn't exist")
        return 0
    if not force:
        info(
            f"uninstall: would remove {target} (venv, scripts, index). "
            "Re-run with --force to actually delete."
        )
        return 0
    shutil.rmtree(target)
    info(f"uninstall: removed {target}")
    info(
        "note: the FastEmbed model cache at ~/.cache/fastembed/ is shared and "
        "intentionally left in place; remove it manually if no other vaults "
        "use it."
    )
    return 0


# --------------------------------- helpers ----------------------------------


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# -------------------------------- argparse ----------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcbrain-ops",
        description="Hybrid lexical + semantic query engine for a McBrain vault.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--vault",
        default=None,
        help="Path to the McBrain vault. Defaults to MCBRAIN_VAULT env var.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_query = sub.add_parser(
        "query", help="Hybrid lexical + semantic search", parents=[common]
    )
    p_query.add_argument("text", help="The query text")
    p_query.add_argument("--k", type=int, default=8, help="Number of results (default 8)")

    p_index = sub.add_parser("index", help="Index management")
    isub = p_index.add_subparsers(dest="op", required=True)
    isub.add_parser("sync", help="Incremental sync (changed files only)", parents=[common])
    isub.add_parser("rebuild", help="Wipe and re-embed everything", parents=[common])
    isub.add_parser("status", help="Print index status as JSON", parents=[common])

    p_mig = sub.add_parser(
        "migrate",
        help="Provision .mcbrain/, install deps, build initial index, patch CLAUDE.md",
        parents=[common],
    )
    p_mig.add_argument(
        "--no-rebuild",
        action="store_true",
        help="Skip initial index rebuild (useful for tests)",
    )

    p_un = sub.add_parser("uninstall", help="Remove .mcbrain/", parents=[common])
    p_un.add_argument(
        "--force",
        action="store_true",
        help="Actually delete (otherwise dry-run)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    vault = resolve_vault(args.vault)

    if args.cmd == "query":
        return cmd_query(vault, args.text, args.k)
    if args.cmd == "index":
        if args.op == "sync":
            return cmd_sync(vault)
        if args.op == "rebuild":
            return cmd_rebuild(vault)
        if args.op == "status":
            return cmd_status(vault)
    if args.cmd == "migrate":
        return cmd_migrate(vault, no_rebuild=args.no_rebuild)
    if args.cmd == "uninstall":
        return cmd_uninstall(vault, force=args.force)
    die(f"unknown command: {args.cmd}", code=2)
    return 2


if __name__ == "__main__":
    sys.exit(main())
