# Plan: Add Query Engine (Issue #2)

## Context

GitHub issue [#2 "Add query engine"](https://github.com/jbdamask/McBrain/issues/2) asks for a search/query engine so McBrain query performance stays stable as the knowledge base grows. Today the query procedure (defined in each vault's `CLAUDE.md`, executed by Claude through that vault's filesystem MCP) is:

1. Read `wiki/index.md` to find relevant pages
2. Read those pages
3. Synthesize an answer with citations

This degrades as the wiki grows because (a) `index.md` itself grows and starts taking real context to load, and (b) "find relevant pages" by skimming a flat catalog is fragile — Claude either over-reads or misses pages whose titles don't lexically match the question.

McBrain ships as a Claude plugin marketplace whose only artifacts are Markdown skills + reference templates under `plugins/mcbrain/skills/`. There is no compiled code, no test runner, no typecheck. Each user's vault is a separate Markdown directory accessed via a per-vault filesystem MCP. Procedures (ingest / query / lint) are documented in the vault's `CLAUDE.md` and executed by Claude.

## Locked Design Decisions

The user has chosen the following architecture. The rest of this plan is written against these decisions; do not revisit them.

1. **Hybrid lexical + semantic, both on by default.** Every vault gets both. Semantic is *not* opt-in — it's automatic.
2. **Zero-touch setup.** The `mcbrain-setup` skill provisions everything: per-vault Python venv, packages, initial index. The user installs McBrain and "it just works."
3. **Per-vault venv, shared model cache.** Python venv lives at `<vault>/.mcbrain/venv/`. The FastEmbed ONNX model weights live in FastEmbed's default cache (`~/.cache/fastembed/`) — shared across vaults so additional vaults are instant to set up and don't re-download the ~30 MB model.
4. **Lexical engine: ripgrep** over `wiki/` (with a `grep -ri` fallback when `rg` isn't available). Stateless.
5. **Semantic engine: FastEmbed (CPU ONNX) + sqlite-vec.** Index lives at `<vault>/.mcbrain/index.db`. Model: `BAAI/bge-small-en-v1.5` (384-dim, ~30 MB, fast on CPU).
6. **Index scope: `wiki/` only.** `raw/` is intentionally excluded — it's source material that's already cited from wiki pages and would roughly double the index size.
7. **Hybrid ranking: Reciprocal Rank Fusion (RRF).** Standard, well-studied, no tuning knobs.
8. **New skill: `mcbrain-ops`.** Owns the indexing lifecycle (`query`, `index sync`, `index rebuild`, `index status`, `uninstall`). Called automatically by the `mcbrain` skill after any wiki edit, and at query time.
9. **Chunking: per-page initially.** Each `wiki/*.md` file is one chunk. If pages routinely exceed ~1500 tokens, revisit and split on `##` headings — but keep first-cut simple.
10. **Existing vaults upgrade in place.** When the user runs `mcbrain-ops` on a vault that has no `.mcbrain/` yet, it provisions the venv and builds the initial index. No vault is left behind.
11. **McBrain never installs system-wide tools.** The only things McBrain creates or modifies on the user's machine live inside `<vault>/.mcbrain/` (per-vault venv, per-vault sqlite database, per-vault scripts) and inside FastEmbed's existing user-level cache (`~/.cache/fastembed/`, owned by the FastEmbed library, not by McBrain itself). McBrain *detects* required system prerequisites (`python3`, `rg`) and *surfaces platform-appropriate instructions* for the user to install them; it never invokes a system package manager (`brew`, `winget`, `apt`, `dnf`, etc.) on the user's behalf. This applies everywhere in the plan: any "install" verb the engineer encodes must mean "into a per-vault venv via `pip`," never "system-wide."
12. **Cross-platform: macOS, Linux, Windows.** Implementation must work on all three. Detection logic should not assume a POSIX shell (Windows users may be in PowerShell, Git Bash, WSL, or cmd). Instructions surfaced to the user must be branched on detected OS.

## Recommended Approach

Three streams of work, in order. Each ends in a shippable state.

### Stream 1 — `mcbrain-ops` skill (the engine)

A new skill at `plugins/mcbrain/skills/mcbrain-ops/`. It owns all index operations and is the only place Python lives. Other skills delegate to it.

**Public operations (what Claude invokes via this skill):**

- `query "<text>" [--k 8]` — runs hybrid search, returns ranked list of `wiki/*.md` paths + scores + short excerpts as JSON to stdout.
- `index sync` — incremental: detects added / changed / deleted wiki files via content hash, re-embeds only the deltas. Default after-edit path. Idempotent and fast (sub-second on no-op).
- `index rebuild` — wipes the index and re-embeds everything. Used when the embedding model or schema changes, or on suspected corruption.
- `index status` — prints doc count, last sync time, model name + dimension, index file size.
- `migrate` — provisions `.mcbrain/` (venv + packages + initial index) on a vault that doesn't have it yet, AND patches the vault's `CLAUDE.md` to include the current query-engine procedures (`## Query engine` section, rewritten `## Operations → Query`, `index sync` hook in ingest/update procedures). Idempotent: re-running on a fully migrated vault is a no-op. Used by both the `mcbrain` skill (Stream 3 migration check) and as the underlying mechanism for `mcbrain-setup` Step 8.5.
- `uninstall` — removes `<vault>/.mcbrain/`. Optionally cleans the FastEmbed shared cache if no other vault references it (see risks).

**Internals:**

- One small Python entry-point (`mcbrain_ops.py`) inside `<vault>/.mcbrain/bin/`, dispatched by subcommand.
- Embedding via `fastembed.TextEmbedding(model_name="BAAI/bge-small-en-v1.5")`. Lets FastEmbed manage its own cache (no `cache_dir` override → shared behaviour for free).
- Storage via `sqlite-vec`: one table for chunks (`id`, `path`, `content_hash`, `text`, `mtime`), one virtual `vec0` table for embeddings keyed by chunk id.
- Lexical: shells out to `rg --type md --json` against `<vault>/wiki/`; on `rg` absence, falls back to `grep -ril --include='*.md'` and a Python BM25 pass over the candidate set.
- Hybrid fusion: RRF over the lexical-rank list and the cosine-similarity-rank list. `score(d) = Σ 1/(k+rank_i(d))` with `k=60`. Top-K returned.
- Single-process, synchronous. No daemon. Each call is a `python3 .mcbrain/bin/mcbrain_ops.py <subcommand>` invocation through `Bash`.

**Files this skill owns:**

- `plugins/mcbrain/skills/mcbrain-ops/SKILL.md` — describes when this skill triggers (mainly: invoked by other skills, but also directly when user asks to "rebuild the index", "check McBrain index status", etc.).
- `plugins/mcbrain/skills/mcbrain-ops/README.md` — marketplace-facing description.
- `plugins/mcbrain/skills/mcbrain-ops/references/requirements.txt` — `fastembed==<pinned>`, `sqlite-vec==<pinned>`. Versions resolved during implementation, not at plan time.
- `plugins/mcbrain/skills/mcbrain-ops/references/mcbrain_ops.py` — the dispatcher script. Single file, < 300 LoC. Subcommands: `query`, `index sync|rebuild|status`, `uninstall`.
- `plugins/mcbrain/skills/mcbrain-ops/references/schema.sql` — initial sqlite schema (chunks table + vec0 virtual table).
- `plugins/mcbrain/skills/mcbrain-ops/references/gitignore-snippet.md` — the lines added to `<vault>/.gitignore` for git-strategy vaults: `.mcbrain/venv/`, `.mcbrain/index.db`, `__pycache__/`. (`.mcbrain/bin/` *is* checked in so the script travels with the vault.)

### Stream 2 — `mcbrain-setup` provisioning (zero-touch install)

Update the existing setup skill so a fresh McBrain install ends with a working index.

**New setup steps (additions, not replacements):**

- **Step 5.5: Detect prerequisites (do not install).** McBrain never runs a system package manager itself (see Locked Decision 11). This step only *detects* what's available and *surfaces instructions* for the user to install missing pieces themselves.
  - **Detect host OS** first (`uname -s` on POSIX; `$env:OS` / `ver` on Windows). Don't assume the shell — the detection step must work whether the user is in zsh, bash, PowerShell, Git Bash, WSL, or cmd.
  - **Detect `python3`** by attempting to run it (`python3 --version`, falling back to `python --version` and parsing for `Python 3.x`). If absent: print a clear warning that semantic search will be unavailable until Python 3 is installed, and surface platform-appropriate guidance: macOS → suggest installing from python.org or via Homebrew if the user already has it; Linux → suggest the user's distro package manager (`apt install python3` / `dnf install python3` / etc.); Windows → suggest installing from python.org or via the Microsoft Store. **Do not block setup** — the vault still gets a working lexical-only path.
  - **Detect `rg`** (`rg --version`). If absent: surface the same kind of platform-appropriate guidance and continue. The `grep` fallback (POSIX) / `Select-String` fallback (Windows PowerShell) handles the lexical path either way. **Do not block.**
  - After surfacing instructions, prompt the user: "Install these now and let me know when ready, or proceed with the available subset?" If the user installs and confirms, re-run detection and continue. If they proceed without, record the limitation in the vault's `## Query engine` section so the user can fix it later.
- **Step 8.5: Provision `.mcbrain/`.** Create `<vault>/.mcbrain/{venv,bin}/`. Create venv (`python3 -m venv .mcbrain/venv`). `pip install -r` the requirements from `mcbrain-ops/references/requirements.txt`. Copy `mcbrain_ops.py` and `schema.sql` from the `mcbrain-ops` skill's `references/` into `<vault>/.mcbrain/bin/`. Run `python3 .mcbrain/bin/mcbrain_ops.py index rebuild` to seed the empty index (no-op if `wiki/` is empty, which it will be for a fresh vault).
- **Step 3 update (Git strategy):** `.gitignore` baked in by setup must now also include `.mcbrain/venv/`, `.mcbrain/index.db`, `__pycache__/`. (`bin/` stays in git so the script is reproducible.)

**`claude-md-template.md` rewrites:**

- `## Operations → Query` becomes: "Invoke the `mcbrain-ops` skill (`query "<text>"`). It returns a ranked list of wiki page paths + scores. Read the top 5–8 pages. Synthesize an answer with `[[wikilinks]]` citations. Offer to file the answer as a wiki page if it's worth keeping." `wiki/index.md` is no longer the retrieval mechanism — it stays for human browsing and `lint`.
- `## Operations → Ingest from raw/`: append a final step "Invoke `mcbrain-ops` (`index sync`) so the new wiki page is searchable immediately."
- `## Operations → Update existing wiki page`: same final step — `index sync`.
- New `## Query engine` section, sibling of `## Backup`: records `mode: lexical+semantic`, `embedding_model: BAAI/bge-small-en-v1.5`, `embedding_dim: 384`, `index_path: .mcbrain/index.db`. The `mcbrain-ops` skill writes/updates this section during provisioning.

### Stream 3 — Migration prompt in `mcbrain` (orchestration only)

**Architectural principle (do not violate):** procedures (how to query, when to sync the index, how to ingest) live in the vault's `CLAUDE.md`, which is generated from `claude-md-template.md`. The `mcbrain` skill is a router — it defers to CLAUDE.md for everything procedural. **Stream 2 already places the query procedure and the post-edit `index sync` hooks into `claude-md-template.md`.** This stream must not duplicate or relocate any of that into the skill.

The only thing the `mcbrain` skill is responsible for in this feature is a migration check: detecting vaults that pre-date the query engine and offering to upgrade them. That's a skill-level orchestration concern (the environment isn't ready yet) — distinct from any procedure CLAUDE.md describes.

**`mcbrain/SKILL.md` changes:**

- Add a "Migration check" subsection at the top of the skill's routing logic: when invoked against a vault, first verify the vault has `.mcbrain/` *and* that its `CLAUDE.md` contains a `## Query engine` section. If either is missing, the vault was set up before the query engine landed; offer the user a one-shot migration (delegate to `mcbrain-ops migrate`, which provisions `.mcbrain/` and updates `CLAUDE.md` to the current template). Once the user accepts and migration completes, defer to the (now-updated) `CLAUDE.md` as normal. Don't add anything else — query routing and post-edit syncing are CLAUDE.md procedures, not skill concerns.

**`mcbrain-ops` skill addition (consequence of the migration design):**

- Add `migrate` as a sixth subcommand on `mcbrain_ops.py`. It is the same logic as `mcbrain-setup` Step 8.5 (provision the venv, install packages, build initial index) plus a `CLAUDE.md` patcher that adds the `## Query engine` section, rewrites `## Operations → Query`, and appends the `index sync` step to existing ingest/update procedures. Idempotent: running it on an already-migrated vault is a no-op.

**Why this design:** the user said "every time the wiki is updated, the index gets updated." That happens because `claude-md-template.md` (Stream 2) bakes the `index sync` step into every operation that writes the wiki — Claude reads CLAUDE.md, follows the procedure, runs `index sync` automatically. There's no daemon, no filesystem watcher, and no skill-level orchestration of that hook. If a user edits the vault outside Claude (e.g., directly in Obsidian), the next query — which itself runs through the CLAUDE.md procedure that calls `mcbrain-ops query`, which can opportunistically prelude with `index sync` — reconciles drift silently via content hashing.

## Changes (file by file)

### New: `plugins/mcbrain/skills/mcbrain-ops/SKILL.md`

Full new skill file. Frontmatter: `name: mcbrain-ops`, `description` triggers on "rebuild McBrain index", "check McBrain index", "McBrain query engine", and (most importantly) describes that this skill is invoked by the other McBrain skills as a subroutine. Body documents the five operations and their CLI shapes.

### New: `plugins/mcbrain/skills/mcbrain-ops/README.md`

Marketplace-facing. Short. "Indexing and query engine that backs McBrain. Provisioned automatically by `mcbrain-setup`. Invoked automatically by `mcbrain` after wiki edits and at query time."

### New: `plugins/mcbrain/skills/mcbrain-ops/references/requirements.txt`

Pinned versions of `fastembed` and `sqlite-vec`. Resolved during implementation against then-latest stable releases.

### New: `plugins/mcbrain/skills/mcbrain-ops/references/mcbrain_ops.py`

The dispatcher. Single file, < 300 LoC, no dependencies beyond `fastembed`, `sqlite-vec`, and the standard library. Subcommands: `query`, `index sync`, `index rebuild`, `index status`, `uninstall`. Reads vault path from `--vault <path>` or `MCBRAIN_VAULT` env var. JSON output on stdout for `query` and `index status`; human-readable for the rest.

### New: `plugins/mcbrain/skills/mcbrain-ops/references/schema.sql`

```sql
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  content_hash TEXT NOT NULL,
  text TEXT NOT NULL,
  mtime REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vec USING vec0(
  embedding float[384]
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
```

### New: `plugins/mcbrain/skills/mcbrain-ops/references/gitignore-snippet.md`

Lines for `<vault>/.gitignore`: `.mcbrain/venv/`, `.mcbrain/index.db`, `.mcbrain/index.db-*` (sqlite WAL/SHM), `__pycache__/`.

### Modified: `plugins/mcbrain/skills/mcbrain-setup/SKILL.md`

- New Step 5.5 (prereq detection) — see Stream 2 above.
- New Step 8.5 (provision `.mcbrain/`) — see Stream 2 above.
- Updated Step 3 (Git strategy gitignore additions).
- Footer "Key operations to teach the user" expanded with the `mcbrain-ops` rebuild command for the rare case of needing a full reindex.

### Modified: `plugins/mcbrain/skills/mcbrain-setup/references/claude-md-template.md`

- Rewrite `## Operations → Query`.
- Append `index sync` step to `## Operations → Ingest from raw/` and to any "update wiki page" / "delete wiki page" procedures.
- New `## Query engine` section template (filled in during provisioning).
- `.gitignore` block updated for git strategy.

### Modified: `plugins/mcbrain/skills/mcbrain/SKILL.md`

- New "Migration check" subsection only. Detects pre-upgrade vaults (missing `.mcbrain/` or stale `CLAUDE.md`) and offers `mcbrain-ops migrate`. **No procedural changes here** — query routing and post-edit `index sync` are CLAUDE.md procedures (Stream 2), not skill concerns.

### Modified: `README.md` (repo root)

- New "Query engine" subsection under "Building Your Knowledgebase". Covers: hybrid lexical + semantic, automatic provisioning, where the index lives, how to rebuild manually if needed, what's in the shared cache and how to clear it.
- Update dependency footprint section: now lists `python3` and `rg` (recommended) alongside Node.

### Modified: `plugins/mcbrain/skills/mcbrain/README.md` and `plugins/mcbrain/skills/mcbrain-setup/README.md`

- One-line mention that the query engine is built in and provisioned automatically.

## Critical Files

- `plugins/mcbrain/skills/mcbrain-ops/references/mcbrain_ops.py` — *new*. The only piece of executable code McBrain ships. Every operation funnels through this script. Quality of this file determines the quality of the feature.
- `plugins/mcbrain/skills/mcbrain-ops/SKILL.md` — *new*. Triggers and contracts for the operations.
- `plugins/mcbrain/skills/mcbrain-setup/SKILL.md` — modified. Adds prereq detection and provisioning steps. Most user-visible change in the install flow.
- `plugins/mcbrain/skills/mcbrain-setup/references/claude-md-template.md` — modified. Defines the query procedure every vault's CLAUDE.md inherits.
- `plugins/mcbrain/skills/mcbrain/SKILL.md` — modified. Wires auto-sync and migration prompts into day-to-day usage.

## Dependencies & Ordering

1. **Build `mcbrain-ops` first.** Stream 1. Standalone. Can be tested by manually creating `.mcbrain/` in a real vault, copying the script in, and exercising every subcommand. Verifying this in isolation before touching the other skills means we catch all the embedding / sqlite-vec / chunking bugs before they're spread across the wider system.
2. **Wire `mcbrain-setup` to provision.** Stream 2. Depends on Stream 1 because it copies the script and requirements file from `mcbrain-ops/references/`. This is when fresh installs become end-to-end zero-touch.
3. **Wire `mcbrain` for auto-sync and query routing.** Stream 3. Depends on Stream 1. Can land in parallel with Stream 2 — they touch different files.
4. **Migration path for existing vaults.** Sub-feature of Stream 3. Same logic as Stream 2's Step 8.5, just triggered lazily.
5. **Documentation pass.** README + skill READMEs, last.

Single PR. The streams are interdependent enough that splitting them would create awkward "lexical-only" or "ops-without-callers" intermediate states. One coherent ship.

## Risks & Open Questions

- **Python availability.** This is the load-bearing assumption. If `python3` is missing the user can't get semantic. Mitigation: setup detects and instructs (`brew install python` / similar); falls back gracefully to lexical-only with a clear "install Python and re-run setup to enable semantic search" message. Worth thinking about whether to attempt `uv` / `pyenv` invocations as alternates — first cut: no, stick with system `python3`.
- **`rg` availability.** Lower stakes — `grep` fallback is real. Setup recommends but doesn't require.
- **First-vault model download.** ~30 MB FastEmbed download on first ever McBrain install. Surprises users on metered or air-gapped connections. Setup should announce this clearly before running it ("Downloading the embedding model (~30 MB, one-time, shared across all your McBrain vaults)…") and offer to skip — skipped vaults run lexical-only until first successful download.
- **Shared cache lifecycle.** `~/.cache/fastembed/` is owned by FastEmbed, not by McBrain. If the user uninstalls every vault, ~30 MB stays orphaned. The `mcbrain-ops uninstall` subcommand can detect "no other vaults reference this cache" and clean it, but discovering "other vaults" is non-trivial without a registry. First cut: leave it, document it, add `mcbrain-ops cache clear` as an explicit command.
- **Index drift outside Claude.** If the user edits a wiki page in Obsidian directly, the index goes stale. `index sync` uses content hashes (not just mtime), so the next call reconciles. The risk is the *next query* uses stale results because nothing triggered `sync` first. Mitigation: have `mcbrain-ops query` opportunistically run `sync` if it's been more than N seconds since the last sync, OR always run `sync` as a quick prelude to `query`. Hash-only sync on a small wiki is genuinely fast (sub-second when nothing changed), so prelude-sync is probably fine. Decide during implementation.
- **Drive-synced vaults.** Google Drive may rewrite mtimes on sync. Hashing rather than relying on mtime sidesteps this.
- **sqlite-vec install.** `sqlite-vec` ships as a loadable extension. On some platforms (notably system Python on macOS) the bundled sqlite3 isn't built with extension loading enabled. Mitigation: the `pip install sqlite-vec` package bundles a Python-loadable variant; verify on macOS / Linux / Windows during implementation. If a platform fails, fall back to numpy-based brute-force search (~50 LoC, fine up to ~10k chunks).
- **Multi-language wikis.** `bge-small-en-v1.5` is English-tuned. Mostly fine; flag for future swap to a multilingual model if a user needs it.
- **Concurrent writes.** Two Claude sessions editing the same vault could race on `index sync`. SQLite handles single-writer locking; one will block briefly and retry. Acceptable.
- **Large pages.** Per-page chunking breaks for pages > ~1500 tokens (truncation, weaker embeddings). Plan revisits chunking on `##` boundaries if this becomes common.

## Verification

The plugin ships Markdown skills + reference scripts + one Python module. Acceptance is based on file existence, content shape, and end-to-end runnable checks. Per `AGENTS.md`, every Bead's acceptance criteria end with concrete behavioural checks (no typecheck step — this isn't a TypeScript project).

**Static (file existence and shape):**

- `plugins/mcbrain/skills/mcbrain-ops/{SKILL.md,README.md}` exist with valid YAML frontmatter.
- `plugins/mcbrain/skills/mcbrain-ops/references/{requirements.txt,mcbrain_ops.py,schema.sql,gitignore-snippet.md}` all exist.
- `mcbrain_ops.py` runs `--help` cleanly and lists all five subcommands.
- `claude-md-template.md` no longer instructs Claude to "Read `wiki/index.md` to find relevant pages" as the *only* retrieval step. Its `## Operations → Query` section delegates to the `mcbrain-ops` skill.
- `claude-md-template.md` contains a new `## Query engine` section.
- `mcbrain-setup/SKILL.md` contains the new Step 5.5 (prereq detection) and Step 8.5 (provisioning).
- `mcbrain/SKILL.md` contains "Post-edit hook", "Query routing", and "Migration prompt" subsections.

**End-to-end (manual, on a real vault):**

- *Fresh install end-to-end.* Run `mcbrain-setup` against a brand-new directory. Confirm `<vault>/.mcbrain/{venv,bin,index.db}` exist, `index status` reports 0 chunks, model name correct.
- *Ingest → query loop.* Drop a markdown file into `raw/`, run an ingest. Confirm a wiki page appears, confirm `index sync` ran (chunks count incremented), then ask a paraphrased question that doesn't share keywords with the page title and confirm the page is in the top results.
- *Hybrid wins.* Construct a query whose exact words appear in one wiki page (lexical winner) and whose meaning matches a different page (semantic winner). Confirm both appear in the top-K from `mcbrain-ops query`.
- *Migration.* Take a vault that pre-dates this feature (no `.mcbrain/`). Open the `mcbrain` skill against it. Confirm the migration prompt fires and provisioning runs to completion. Repeat the ingest-to-query loop.
- *No-Python fallback.* On a system without `python3`, run setup. Confirm setup completes, surfaces a clear warning, and the resulting vault's query path falls back to lexical-only without crashing.
- *Backup respect.* On a git-strategy vault, `git status` after a full provision shows `.mcbrain/venv/` and `.mcbrain/index.db` ignored, while `.mcbrain/bin/` is tracked.
- *Rebuild.* Run `mcbrain-ops index rebuild`; confirm chunk count matches `find wiki -name '*.md' | wc -l`.
- *Uninstall.* Run `mcbrain-ops uninstall`; confirm `.mcbrain/` is gone and the rest of the vault is untouched.

**Plugin install sanity:**

- Running the marketplace install in Claude Desktop with the worktree as the source still loads the plugin without manifest errors.
- The `mcbrain-ops` skill appears in the available-skills list and is callable directly.

## Out of Scope (for this issue)

- Cross-vault search (querying multiple McBrains in one shot).
- A daemon / filesystem watcher for true real-time index sync.
- Cross-encoder reranking — pure embedding similarity + RRF is enough at personal scale.
- Web-UI / Obsidian-plugin search panel.
- Switching the embedding model dynamically per-vault.
- Multilingual model defaults.
- Replacing the filesystem MCP with a custom McBrain MCP.
