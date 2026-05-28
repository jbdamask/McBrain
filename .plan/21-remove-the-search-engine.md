# Plan: Remove the "Search Engine" (Query Engine) from McBrain

## Context

GitHub issue #21 ("Remove the search engine") asks for the full removal of
the query engine feature from McBrain because it has been causing
installation problems. The "search engine" in the user's language is the
hybrid lexical+semantic **query engine** originally introduced in plan/issue
#2 and pivoted in plan/issue #11 to ship as a per-machine
`mcbrain-engine` stdio MCP server.

This is a **full removal** — not a deprecation. Code, MCP server, dedicated
skill, tests, docs, configuration, dependencies, and all wiring that exists
solely to support the query engine must be deleted. After removal, McBrain
should remain a fully functional personal knowledge base that uses:

- The filesystem MCP for Claude → vault I/O.
- The day-to-day `mcbrain` skill for ingest / lint / synthesis.
- The local research tracker skills (`local-research-db`,
  `local-research-runner`) — these are independent of the query engine
  and stay.
- Plain reading of `wiki/` files (no programmatic query / index step).

Work happens in worktree `/Users/johndamask/code/21-remove-the-search-engine`
on branch `21-remove-the-search-engine`.

## Recommended Approach

Treat this as a "Remove the Notion integration" (PR #19/#20) shaped change,
applied to a more deeply integrated subsystem. The engine touches more
surfaces than the Notion backend did:

1. **Delete** the engine MCP server source tree and the `mcbrain-ops`
   skill in full.
2. **Excise** every query-engine reference from the surviving skills
   (`mcbrain`, `mcbrain-setup`) and from `claude-md-template.md`. Convert
   the **Query** operation from "call the `mcbrain-engine` MCP's `query`
   tool" back to "read `wiki/index.md` to find relevant pages, then read
   them". Drop the "always run `index_sync` after wiki writes" rule.
3. **Delete** the engine-specific tests and fixtures; keep
   `test_local_research.py` (it tests the unrelated research tracker).
   Update `tests/conftest.py`, `tests/README.md`, and `tests/requirements.txt`
   accordingly.
4. **Delete** the engine's Python dependencies (`mcp`, `fastembed`, `numpy`).
   None of these are used by the surviving code.
5. **Update README, plugin.json, marketplace.json** to remove the query
   engine sections and adjust the description/skill counts.
6. **Bump plugin version to 4.0.0** (breaking change for installed vaults
   whose `CLAUDE.md` currently routes Query through the engine MCP). Add a
   user-facing note mirroring the Notion-removal pattern: existing vaults
   keep working but lose semantic search; an `origin/query-engine` (or
   pre-removal tag) branch is preserved for users who want to keep running
   the engine.
7. **No migration tooling.** Same posture as PR #19/#20 for Notion — users
   who want the engine stay on the pre-4.0 branch; users who upgrade get a
   wiki-index-only Query operation. We do not generate code to strip
   `## Query engine` from existing user vaults.

## Changes

### 1. Delete the engine MCP server tree

Delete the entire directory:

- `plugins/mcbrain/mcp-server/` (launcher.py, mcbrain_engine.py, paths.py,
  registry.py, schema.sql, requirements.txt, README.md)

### 2. Delete the `mcbrain-ops` skill

Delete the entire directory:

- `plugins/mcbrain/skills/mcbrain-ops/` (README.md, SKILL.md)

### 3. Edit the `mcbrain` skill (`plugins/mcbrain/skills/mcbrain/SKILL.md`)

- Remove **Step 2.5: Migration check (query engine)** in full — including
  the three-state diagnosis, the migration prompt, and the
  "lexical-only fallback" note. Renumber subsequent step references if
  any exist (currently none do — the doc jumps from 2.5 to free-form
  sections).
- Remove the architectural-principle paragraph that names the
  `mcbrain-engine` MCP and `index_sync`.
- The "Step 2: Read CLAUDE.md" content stays — CLAUDE.md is still the
  vault schema; only the engine-specific section it referenced goes away.
- Keep the rest (ingest, backup, log, tone).

### 4. Edit the `mcbrain-setup` skill (`plugins/mcbrain/skills/mcbrain-setup/SKILL.md`)

This is the largest surgical edit. The skill currently has multiple
engine-specific steps:

- **Required intake table:** drop row #7 (`PYTHON_OK` prerequisite). The
  ordering tip paragraph that mentions "Python check" gets reworded —
  Python is no longer a prerequisite.
- **Cross-platform reference table:** drop rows for "McBrain engine
  runtime install dir", "McBrain vault registry", "Python install
  command", "ripgrep install", "Venv interpreter inside venv", "Engine
  launcher path". Drop the Windows-specific %LOCALAPPDATA% note about
  two separate grants — without the engine, only the Claude config grant
  remains.
- **Step 5.5 (Confirm Python prerequisite):** delete entirely.
- **Step 5.6 (Install the engine runtime + register the MCP):** delete
  entirely. The Claude Desktop config no longer needs the
  `mcbrain-engine` `mcpServers` entry — only the per-vault filesystem
  MCP (added in Step 5) remains.
- **Step 8.5 (Trigger first MCP launch + provision this vault's index):**
  delete entirely.
- **Step 9 (Install the companion operating skill):** keep but drop
  references to `mcbrain-engine`.
- **Step 10 (First ingest):** keep.
- **"Key operations to teach the user" section:** drop the
  Query-engine-maintenance bullets (full reindex, status, uninstall,
  remove runtime). Keep ingest/query/lint phrasing but reword the
  "Query" example so it doesn't imply a search tool.
- **Step 3 → `.gitignore` template:** drop the `.mcbrain/index.db*`
  lines. Drop the explanatory paragraph that justifies them.
- **Step 3 → CLAUDE.md generation:** unchanged at the file level (the
  template itself is edited separately in change #6), but the comment
  about "Step 8.5 patches in `## Query engine`" needs to go.
- **Step 8 (Research tracker setup):** keep the whole step. Remove the
  "After this, continue at Step 8.5" trailing pointer; replace with
  "After this, continue at Step 9".
- **Order-of-operations callout** about "Step 3 = create from template +
  append Web Ingestion + Backup; Step 8 = update Research tracker;
  Step 8.5 = patch Query engine" — drop the Step 8.5 reference.

### 5. Edit the `/mcbrain-setup` command (`plugins/mcbrain/commands/mcbrain-setup.md`)

No engine references — leave as-is. (Verified by grep.)

### 6. Edit `claude-md-template.md` (`plugins/mcbrain/skills/mcbrain-setup/references/claude-md-template.md`)

- **`## Operations` preamble:** delete the "Always sync the search index
  after any wiki write" paragraph.
- **`### Ingest` procedure step 8:** delete the `index_sync` step.
  Renumber if necessary; in practice the step is currently the trailing
  item so the list just ends one earlier.
- **`### Query` procedure:** rewrite to the pre-engine flow. New
  procedure body:
  1. Read `wiki/index.md` to find candidate pages.
  2. Read the candidate pages (and follow `[[wikilinks]]` outward when
     useful).
  3. Synthesize an answer with `[[wikilinks]]` citations.
  4. Offer to file the answer as a new wiki page if it's worth keeping.
  Keep the "Answers don't have to be prose" paragraph — formats are
  unrelated to the engine.
- **`## Query engine` section** (lines ~193–202): delete entirely.
- Remove the trailing sentence on the same section pointing at the
  `mcbrain` skill's migration detection.
- **`### Lint`:** unchanged; no engine refs.

### 7. Edit `README.md`

- **Top-of-file callout:** the Notion-upgrade callout is already there.
  Add a parallel note for plugin 4.0.0 that explains query-engine removal
  and points at a pre-removal git ref (branch/tag name TBD — suggest
  `origin/query-engine`, mirroring the Notion `origin/Notion` snapshot).
- **"How does it work?" bullet list:** drop the
  "Create a local search engine that allows Claude to quickly find
  information regardless of how big your McBrain gets." bullet.
- **"The plugin bundles five cooperating skills"** → change to **four**.
- **Setup section / step 3** ("Install the mcbrain plugin... All four
  skills are activated together") — already says four; verify after
  edits. Actually currently says "four" but the README lists five
  skills below. The truth is five-going-to-four, so this stays "four"
  and the skill list shrinks.
- **`## Query engine` section** (~lines 79–116) — delete entirely,
  including the macOS troubleshooting subsection.
- **`## Skills bundled in the plugin`:** delete the `### mcbrain-ops`
  subsection. The intro line ("The mcbrain plugin contains five skills")
  becomes "contains four skills".
- **`## Typical workflow`:** unchanged (no engine refs).
- **Repo layout tree:** drop `mcbrain-ops/` line and the (implicit)
  mcp-server reference. The tree currently doesn't show `mcp-server/`;
  just drop `mcbrain-ops/`.
- **`## Running tests`:** rewrite. The current block points at the
  engine's requirements file and a deleted skill path. Replace with a
  simpler block targeted at the surviving local-research tests:
  ```sh
  python3 -m venv .test-venv
  .test-venv/bin/pip install -r tests/requirements.txt
  .test-venv/bin/python -m pytest tests/ -v
  ```

### 8. Edit `plugins/mcbrain/.claude-plugin/plugin.json`

- Bump `version` to `"4.0.0"`.
- Rewrite `description` to drop the "Hybrid lexical+semantic search runs
  through a shared local mcbrain-engine MCP server installed once per
  machine, so McBrain works in both Claude Code and Claude Cowork
  (Desktop)" clause. The new description should be a clean two-sentence
  summary of vault setup + day-to-day ops + the local research tracker.

### 9. Edit `.claude-plugin/marketplace.json`

- Bump `metadata.version` to `"4.0.0"`.
- Bump `plugins[0].version` to `"4.0.0"`.
- Rewrite `plugins[0].description` to drop "backed by a local
  mcbrain-engine MCP server (installed once per machine) so search works
  in both Claude Code and Claude Cowork."

### 10. Delete engine-coupled tests and fixtures

- Delete `tests/test_static.py` (tests `import mcbrain_ops`).
- Delete `tests/test_unit.py` (tests RRF / excerpt / blob helpers from
  the engine).
- Delete `tests/test_patcher.py` (tests the CLAUDE.md patcher inside the
  engine).
- Delete `tests/test_index.py` (engine SQLite/embedding lifecycle).
- Delete `tests/test_search.py` (engine lexical/semantic/RRF query).
- Delete `tests/test_e2e.py` (engine CLI end-to-end).
- Delete `tests/fixtures/claude_md_legacy.md` (patcher fixture).
- Delete `tests/fixtures/claude_md_h4_after_query.md` (patcher fixture).
- Delete `tests/fixtures/wiki/` (sample wiki pages for engine search).
- Keep `tests/test_local_research.py` (verified independent — references
  research-tracker skills + claude-md-template only).

### 11. Edit `tests/conftest.py`

The current file is engine-shaped: it adds `mcbrain-ops/references/`
to `sys.path`, defines `fresh_vault` / `vault_with_schema` /
`shared_embedder` / `populated_vault` fixtures, all engine-coupled.
After removal, `test_local_research.py` is the only consumer; it does
**not** use any of the fixtures (verified by grep — it builds its own
`tmp_path` setups). Two options:

- **Option A (recommended):** delete `tests/conftest.py` entirely.
  Pytest works fine without one.
- **Option B:** keep an empty `conftest.py`. Not necessary.

Plan picks **Option A**.

### 12. Edit `tests/README.md`

Rewrite from scratch — short doc describing what's left:

- Pytest suite for the local research tracker
  (`local-research-db` + `local-research-runner`).
- One file (`test_local_research.py`), pure-stdlib, no extra venv steps
  needed beyond `pip install -r tests/requirements.txt`.
- Drop the Testing-Trophy taxonomy table — only one tier remains.

### 13. Edit `tests/requirements.txt`

Currently: `pytest>=8.0,<9` plus a comment about layering on top of the
engine requirements. Drop the comment (and the FastEmbed mention) — just
leave `pytest>=8.0,<9`.

### 14. Optional: preserve a snapshot branch / tag

Same pattern as the Notion removal: before merging, create
`origin/query-engine` (or a tag like `v3.1.0-pre-engine-removal`) so
users who want to keep the hybrid search can stay on it. This is an
**operator step**, not code — surface it in the PR description and the
README callout, not in the codebase itself.

## Critical Files

| Path | Action |
|---|---|
| `plugins/mcbrain/mcp-server/` (whole directory) | Delete |
| `plugins/mcbrain/skills/mcbrain-ops/` (whole directory) | Delete |
| `plugins/mcbrain/skills/mcbrain/SKILL.md` | Edit — drop Step 2.5 + engine refs |
| `plugins/mcbrain/skills/mcbrain-setup/SKILL.md` | Edit — drop Steps 5.5, 5.6, 8.5, Python intake row, engine columns, key-ops bullets |
| `plugins/mcbrain/skills/mcbrain-setup/references/claude-md-template.md` | Edit — restore wiki-index Query op, drop `## Query engine` section, drop `index_sync` rule |
| `plugins/mcbrain/.claude-plugin/plugin.json` | Edit — version 4.0.0, new description |
| `.claude-plugin/marketplace.json` | Edit — version 4.0.0, new description |
| `README.md` | Edit — drop Query engine section + macOS troubleshooting, drop mcbrain-ops skill blurb, fix skill count, rewrite tests section, add 4.0.0 callout |
| `tests/test_static.py` | Delete |
| `tests/test_unit.py` | Delete |
| `tests/test_patcher.py` | Delete |
| `tests/test_index.py` | Delete |
| `tests/test_search.py` | Delete |
| `tests/test_e2e.py` | Delete |
| `tests/fixtures/claude_md_legacy.md` | Delete |
| `tests/fixtures/claude_md_h4_after_query.md` | Delete |
| `tests/fixtures/wiki/` (whole directory) | Delete |
| `tests/conftest.py` | Delete |
| `tests/README.md` | Rewrite (local-research-only) |
| `tests/requirements.txt` | Edit — drop FastEmbed comment |
| `plugins/mcbrain/skills/local-research-db/` | No changes (verified) |
| `plugins/mcbrain/skills/local-research-runner/` | No changes (verified) |
| `plugins/mcbrain/commands/mcbrain-setup.md` | No changes (verified) |

## Dependencies & Ordering

The edits are loosely coupled and can be done in any order, but the
sanest workflow is:

1. **Delete the engine source tree** (`mcp-server/`, `mcbrain-ops/`,
   engine-coupled tests + fixtures, `conftest.py`). One commit — easy
   to review.
2. **Update the template and skills** that reference the engine
   (`claude-md-template.md`, `mcbrain/SKILL.md`,
   `mcbrain-setup/SKILL.md`). One commit — this is the substantive
   surgical edit and the one most likely to need revisions during PR
   review.
3. **Update metadata + docs** (`plugin.json`, `marketplace.json`,
   `README.md`, `tests/README.md`, `tests/requirements.txt`). One
   commit.
4. **Verify** (see below).
5. **Open PR** referencing issue #21.

No automated dependency between the three commits — they're three
disjoint surfaces — but doing them in this order makes review easier
because each commit is internally consistent.

## Risks & Open Questions

1. **Existing user vaults break for semantic search.** Same risk profile
   as the Notion removal. Mitigation: README callout + preserve a
   pre-removal snapshot branch/tag. Confirm with the user that they
   want the same "snapshot branch + no in-vault migration" stance, vs.
   adding a small script that strips `## Query engine` from existing
   `CLAUDE.md` files. Plan currently assumes **no in-vault migration**.
2. **Plugin major version bump (3.0.0 → 4.0.0).** Confirm with the user
   that 4.0.0 is the right number (vs. 3.1.0 or another scheme). Notion
   removal was 3.0.0, so a major bump for engine removal matches that
   precedent.
3. **Snapshot branch name.** PR #20 used `origin/Notion`. Suggesting
   `origin/query-engine` for consistency with the Notion pattern.
   Alternative: tag instead of long-lived branch.
4. **`tests/conftest.py` future use.** Deleting it is the cleanest path
   today, but if more tests are added later they may want a shared
   `tmp_path` helper. Recreating an empty `conftest.py` later is
   trivial, so deletion is the right move now.
5. **Skill list activation.** `marketplace.json` and `plugin.json` don't
   enumerate skills — Claude Desktop discovers them from
   `plugins/mcbrain/skills/`. So removing the `mcbrain-ops` directory
   removes it from the installed plugin without further config edits.
   Verified by inspection of the plugin manifest.
6. **`.beads/` directory and prior plan files** (`.claude/plans/*.md`,
   `.plan/*.md`) contain history of the engine. These are historical
   artifacts and stay untouched — not part of the user-facing surface.
7. **Possible orphan references in vault `CLAUDE.md` files in the wild**
   pointing at `mcbrain-engine` MCP tools (`index_sync`, `query`,
   `migrate`). After removal, the `mcbrain` skill encountering those
   should degrade gracefully because Step 2.5 is gone — but if a user's
   CLAUDE.md `### Query` block still says "call the `mcbrain-engine`
   MCP's `query` tool", Claude will try and fail. **Acceptable** under
   the no-in-vault-migration stance; surface in the README callout.

## Verification

After all edits:

1. **Static checks**
   ```sh
   cd /Users/johndamask/code/21-remove-the-search-engine
   # Confirm no stale engine references remain in surviving files.
   grep -rn "mcbrain-engine\|mcbrain-ops\|fastembed\|index_sync\|index_rebuild\|index_status\|\.mcbrain/" \
     plugins/ tests/ README.md .claude-plugin/ \
     --include="*.md" --include="*.json" --include="*.py" --include="*.sql"
   # Expected: empty output (or only this plan file itself, which lives under .claude/plans/).
   ```
2. **Plugin manifest sanity**
   ```sh
   python3 -c "import json; print(json.load(open('plugins/mcbrain/.claude-plugin/plugin.json')))"
   python3 -c "import json; print(json.load(open('.claude-plugin/marketplace.json')))"
   ```
   Both should parse and show version `4.0.0`.
3. **Skill directory listing**
   ```sh
   ls plugins/mcbrain/skills/
   # Expected: local-research-db  local-research-runner  mcbrain  mcbrain-setup
   ls plugins/mcbrain/
   # Expected: .claude-plugin  commands  skills   (no mcp-server)
   ```
4. **Test suite**
   ```sh
   python3 -m venv .test-venv
   .test-venv/bin/pip install -r tests/requirements.txt
   .test-venv/bin/python -m pytest tests/ -v
   ```
   Expected: only `tests/test_local_research.py` runs; all tests pass;
   no `ModuleNotFoundError: mcbrain_ops`.
5. **Manual smoke (optional, since setup is Cowork-resident)**: walk a
   single user through `mcbrain-setup` against a fresh dummy vault on
   the worktree and confirm:
   - No Python prerequisite prompt.
   - No `mcbrain-engine` MCP config block written to
     `claude_desktop_config.json`.
   - Vault scaffolding writes `CLAUDE.md` whose `### Query` block points
     at `wiki/index.md` (not the MCP tool) and contains no
     `## Query engine` section.
   - Day-to-day `mcbrain` skill against the vault doesn't ask about
     migration.
6. **Issue close-out**: PR title / body should reference issue #21.
   After merge, close #21 (and confirm beads tracking is updated per
   AGENTS.md's "Landing the Plane" workflow).

---

End of plan.
