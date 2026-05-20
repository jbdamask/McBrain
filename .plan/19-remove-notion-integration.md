# Plan: Remove Notion Integration from McBrain (Issue #19)

## Context

GitHub issue #19 — "Move the Notion integration into a dedicated branch."

A snapshot branch `Notion` has been pushed to `origin/Notion` to preserve
the Notion-enabled codebase. This worktree
(`/Users/johndamask/code/19-move-notion-integration-into-dedicated-branch`,
branch `19-move-notion-integration-into-dedicated-branch`) is where the
Notion integration is **purely removed** — no replacement, no fallback.
The local research tracker (`local-research-db` + `local-research-runner`)
already covers the research-workflow need, so the Notion-bridged ingest
mode, the engine's `ingest_from_notion` family of tools, the `notion.py`
module, the registry's Notion fields, the `notion-token` file concept,
and the Notion-specific setup branch all go away.

After this change, McBrain has exactly one research-tracker backend
(local JSONL) and the engine MCP exposes seven tools instead of ten.
Setup gets simpler: no Notion connector probe, no `enable_notion_for_vault`,
no token paste-in-terminal flow.

## Recommended Approach

Five phases, ordered to avoid leaving dangling imports between commits:

1. **Phase 1 — Engine code (remove Notion module + tools + registry fields).**
   Strip `notion.py`, the five `*_notion*` impl/tool functions in
   `mcbrain_engine.py`, the `notion_token_path` helper in `paths.py`, and
   the `notion_enabled` / `notion_db_id` machinery in `registry.py`. All
   of this is self-contained — no consumer outside this directory imports
   any of it directly; the rest of the plugin reaches it via MCP tool
   calls that are also being removed.

2. **Phase 2 — Remove the Notion skills and runner references.**
   Delete `plugins/mcbrain/skills/notion-research-db/` and
   `plugins/mcbrain/skills/notion-research-runner/` entirely. They are
   skill bundles with their own SKILL.md and a reference prompt; nothing
   else in the plugin imports them.

3. **Phase 3 — Setup skill (collapse the research-tracker question).**
   `mcbrain-setup/SKILL.md` has an `8a` choice with three options
   (`local`, `notion`, `none`) and a `notion` branch (`8b`–`8g`) plus an
   "Enable Notion ingest" sub-section in `8.5`. Collapse `8a` to a yes/no
   ("Set up local tracker now?"), delete the Notion branch entirely, and
   drop the `8.5` Notion sub-section. Update the Required intake table
   (row 8 is no longer multi-choice).

4. **Phase 4 — Vault-side templates and operating skills.**
   Rewrite `claude-md-template.md` so the `## Research tracker` section
   only documents `local` and `none`. Remove the "Ingest from Notion
   research tracker" sub-section from the `## Operations → Ingest`
   block; the standard ingest path is the only flow. Strip Notion mode
   from `mcbrain/SKILL.md`'s routing section. Strip Notion tool
   descriptions from `mcbrain-ops/SKILL.md`. Strip Notion mentions from
   `local-research-db/SKILL.md` and `local-research-runner/SKILL.md`.

5. **Phase 5 — Top-level docs, manifests, tests, plan archives, images.**
   README.md, plugin.json, marketplace.json, mcp-server/README.md all
   need their Notion language and keywords cleaned up. The legacy test
   fixture and the test that asserts the legacy `## Notion companion
   databases` reference survives must be updated. The two
   `.plan/16-…` and `.claude/plans/16-…` historical plan documents
   describe a now-superseded design — leave them alone (they're
   historical), but check that test fixtures pointing at them aren't
   surprised. Drop the Notion-themed images from `img/` and the
   README's references to them.

A single commit per phase keeps the diff reviewable; running the test
suite at the end of phases 1, 4, and 5 catches regressions early.

## Changes

### Phase 1 — Engine code

#### `plugins/mcbrain/mcp-server/notion.py`
- **Delete the entire file.** It is the Notion REST client + Notion
  block → markdown converter, used only by `mcbrain_engine.py`'s
  Notion impls and by `ingest_from_notion_impl`'s frontmatter reader.

#### `plugins/mcbrain/mcp-server/mcbrain_engine.py`
- Delete the entire `# Notion ingest` block (lines ~1009–1156): the
  helpers `_vault_notion_config`, `enable_notion_for_vault_impl`,
  `disable_notion_for_vault_impl`, `ingest_from_notion_impl`, and
  `_find_local_by_notion_id`.
- Delete the four `@mcp.tool()` wrappers: `enable_notion_for_vault`,
  `disable_notion_for_vault`, `ingest_from_notion`.
- Remove the three Notion entries from `TOOL_NAMES` so the registered
  surface drops from ten tools to seven (`query`, `index_sync`,
  `index_rebuild`, `index_status`, `migrate`, `uninstall`,
  `list_vaults`).
- Remove the `import notion` lines (inside the deleted helpers — both
  go with their parent block).

#### `plugins/mcbrain/mcp-server/registry.py`
- Update the module docstring schema example to drop `notion_enabled`
  and `notion_db_id`.
- In `put_vault`, delete the loop that carries forward `notion_*` keys
  from `existing` into the new entry.
- Delete `set_notion_config` and `_normalize_db_id` (only callers are
  the now-removed `enable_notion_for_vault_impl` /
  `disable_notion_for_vault_impl`).

#### `plugins/mcbrain/mcp-server/paths.py`
- Delete `notion_token_path()` (lines ~92–94). Only caller is
  `notion.py:read_token()`, which is also being deleted.

### Phase 2 — Remove Notion skill bundles

#### `plugins/mcbrain/skills/notion-research-db/`
- **Delete the entire directory** (`SKILL.md` is the only file).

#### `plugins/mcbrain/skills/notion-research-runner/`
- **Delete the entire directory** (`SKILL.md` plus
  `references/research_subagent_prompt.md`). The local runner has its
  own copy of the subagent prompt at
  `local-research-runner/references/research_subagent_prompt.md`,
  so nothing is orphaned.

### Phase 3 — Setup skill

#### `plugins/mcbrain/skills/mcbrain-setup/SKILL.md`
- **Required intake table (row 8).** Today: `RESEARCH_TRACKER_BACKEND
  ∈ {local, notion, none}`. Change to a yes/no
  ("Initialize a local research tracker now? yes/no", or fold into
  step 8 narrative — no `AskUserQuestion` card needed).
- **Step 8 header and intro.** Rewrite so there is no "two backends are
  supported" framing. Just one optional setup step: initialize the
  local JSONL tracker.
- **Delete sub-step `8a`** (the three-option `AskUserQuestion`). Replace
  with a single confirmation prompt.
- **Delete the entire "Notion backend branch" (sub-steps 8b–8g),**
  including the security warning block about the bearer token, the
  macOS/Linux/Windows token-install scripts, and the integration-token
  verification step.
- **Keep sub-step `8L`** as the only setup branch, renumbered as just
  "Step 8" body. References inside it that point to "Compare the Notion
  branch below…" must be removed.
- **Step 8.5 — "Enable Notion ingest" sub-section.** Delete entirely.
  The remaining migrate + index-sync flow is unaffected.
- **Step 0 cross-platform path table.** Drop the per-OS rows that only
  exist for the token file (none currently — the token path is only
  inline in 8f, so this is just confirming nothing extra needs trimming).
- **Forbidden Bash list in the STOP section.** No change — the list is
  generic.
- Audit and remove the words "Notion" / "notion" everywhere except in
  the historical "if you came from a Notion-paired vault" callouts (we
  drop those too — no migration story is being supported).
- Update intake guidance about "verify a Notion MCP connector is loaded"
  — that whole probe goes away.

#### `plugins/mcbrain/commands/mcbrain-setup.md`
- One mention: "Notion DB intent" in the AskUserQuestion list. Remove
  that bullet so the command description matches the new SKILL.

### Phase 4 — Vault-side templates and operating skills

#### `plugins/mcbrain/skills/mcbrain-setup/references/claude-md-template.md`
- `## What lives where` section — remove the "(e.g. Notion trackers)"
  and the "Notion DB registry" plumbing example.
- `## Operations → Ingest` — **delete the entire `#### Ingest from
  Notion research tracker` sub-section** (lines ~120–161). The
  introductory paragraph in `### Ingest` ("Ingest has two modes…")
  needs to become "Ingest has a single mode…" or just be removed
  entirely.
- `## Research tracker` section body — remove the `notion` bullet from
  the supported-backends list (keep `local` and `none`). Update the
  downstream-skills enumeration to drop `notion-research-runner` and
  `notion-research-db`. **Remove the "Legacy fallback: … `## Notion
  companion databases` …" paragraph** entirely (no legacy fallback is
  supported on this branch). Remove the HTML comment template block
  for `Backend: notion`.
- `## Source ingestion paths` — drop the "Notion research tracker"
  bullet; keep the "Local research tracker" bullet.

#### `plugins/mcbrain/skills/mcbrain/SKILL.md`
- `## Deferrals to CLAUDE.md` — drop the Notion entry from "Source
  ingestion paths".
- `## Routing "ingest" to the right mode` — **delete the entire
  Mode-A / Mode-B framing and the "Use the server-side ingest tool"
  mandatory routing block** (lines ~77–116). Replace with a single
  paragraph: "Ingest reads files from `raw/`, runs the procedure in
  CLAUDE.md's `## Operations → Ingest from raw/`, and updates the
  wiki. There is one mode."

#### `plugins/mcbrain/skills/mcbrain-ops/SKILL.md`
- "The `mcbrain-engine` MCP exposes eight maintenance tools handled
  here" → "exposes seven maintenance tools…" (or update the count to
  match the new tool list; query stays out of this skill so it's
  six maintenance tools + query).
- Delete the three sub-sections: `enable_notion_for_vault`,
  `disable_notion_for_vault`, `ingest_from_notion`.
- Remove the closing "After `ingest_from_notion` writes files…"
  paragraph.

#### `plugins/mcbrain/skills/local-research-db/SKILL.md`
- Description (frontmatter) — replace "does not depend on Notion" with
  a positive description (e.g. "JSONL-based research tracker for a
  McBrain vault").
- Remove the "local-backend twin of `notion-research-db`" framing and
  the "Pick this one when the user does not have Notion…" paragraph.
- `## Workflow → step 4`: delete the "If the section exists with
  `Backend: notion`: stop and tell the user this vault is already
  paired with Notion…" branch — that backend no longer exists.
- `## Failure modes`: drop the "Backend conflict (vault is on `notion`)"
  bullet.
- `## Non-Goals`: drop "Do not silently switch a vault's backend from
  `notion` to `local`."

#### `plugins/mcbrain/skills/local-research-runner/SKILL.md`
- Remove the "local-backend twin of `notion-research-runner`" framing.
- `## Prerequisites`: remove the "Legacy fallback: … `## Notion
  companion databases` …" parenthetical.
- "There is no Notion connector to match, no database URL to paste."
  → just delete; it's only meaningful in contrast to the other skill.

### Phase 5 — Top-level docs, manifests, tests, plan archives

#### `README.md`
- Line ~31: remove "or a **Notion database** (visual taskboard)".
- Line ~43: delete the Notion recommended-tool bullet.
- Lines ~57–61: collapse the "depending on which backend you picked"
  paragraphs into a single description of the local flow.
- Lines ~71, 73: remove the `make-notion-db.png` and
  `notion-research-db.png` image references.
- **`## Research tracker backends` section** — delete the `notion`
  sub-section and the `Switching backends` paragraph (no backends to
  switch between). Rename the section to `## Research tracker` and
  flatten it.
- "Vaults that pre-date this option keep working — McBrain falls back
  to the legacy `## Notion companion databases` registration…" —
  delete; we're not supporting that legacy path on this branch.
- `## Skills bundled in the plugin` — drop `notion-research-db` and
  `notion-research-runner` entries; update the count from "seven" to
  "five".
- `## Repo layout` tree — drop the two `notion-research-*/` directories.

#### `plugins/mcbrain/.claude-plugin/plugin.json`
- Description: rewrite to drop "either local- or Notion-backed
  research trackers" — describe only the local tracker.
- Keywords: drop `"notion"`.
- Consider bumping the plugin version (e.g. `2.2.0` → `3.0.0`) since
  this is a breaking change for existing Notion-paired vaults. Flag
  this for the user before committing.

#### `.claude-plugin/marketplace.json`
- `metadata.description`: remove "plus companion skills for running
  research workflows through Notion".
- `plugins[0].description`: remove the Notion clause.
- `plugins[0].keywords`: drop `"notion"`.
- Bump `metadata.version` and `plugins[0].version` to match the
  plugin manifest.

#### `plugins/mcbrain/mcp-server/README.md`
- Runtime-layout file tree: delete the `notion.py` line.
- "Setup install rule" paragraph: rewrite the "(like `notion.py` in
  v2.1)" example to use a different hypothetical module name (or just
  drop the parenthetical).

#### `tests/test_local_research.py`
- `test_claude_md_template_has_research_tracker_section` —
  **delete the final assertion** that requires `## Notion companion
  databases` to still appear in the template. No legacy fallback is
  being supported.

#### `tests/fixtures/claude_md_legacy.md`
- The fixture has a `#### Ingest from Notion research tracker`
  sub-block. The fixture's job is to test the legacy-CLAUDE.md
  patcher in `mcbrain_engine.py`, which is still in scope. Decide
  between: (a) leave the fixture as-is — patcher tests don't care
  about Notion content, only that the patcher rewrites `### Query`
  correctly; (b) replace the Notion block with a vanilla "raw"-only
  ingest block. **Recommendation: (a) — minimal-change.** Confirm
  patcher tests still pass.

#### `.plan/` and `.claude/plans/`
- These are historical planning archives — **leave untouched** as
  per-issue history. The two `16-…` files document the design that
  introduced the local backend; they are not consulted by code or
  tests and editing them would muddle the project record.

#### `img/`
- Delete `make-notion-db.png` and `notion-research-db.png`. They are
  only referenced from README.md, which is being updated in the same
  phase.

#### `.gitignore`
- No Notion-specific entries. No change.

#### `.beads/issues.jsonl`
- The beads issue tracker holds historical issue records. **Leave
  untouched** — closing #19 is the user's job, not this plan's.

## Critical Files

**Deleted:**
- `plugins/mcbrain/mcp-server/notion.py`
- `plugins/mcbrain/skills/notion-research-db/SKILL.md`
- `plugins/mcbrain/skills/notion-research-runner/SKILL.md`
- `plugins/mcbrain/skills/notion-research-runner/references/research_subagent_prompt.md`
- `img/make-notion-db.png`
- `img/notion-research-db.png`

**Heavily modified (>100 lines diff each):**
- `plugins/mcbrain/mcp-server/mcbrain_engine.py` — removes ~150 lines (the Notion ingest block + tool wrappers).
- `plugins/mcbrain/skills/mcbrain-setup/SKILL.md` — removes the entire Notion sub-step block (~200 lines).
- `plugins/mcbrain/skills/mcbrain-setup/references/claude-md-template.md` — drops the Notion-bridged ingest sub-section and the Notion `Research tracker` entries (~50 lines).
- `plugins/mcbrain/skills/mcbrain/SKILL.md` — collapses the routing/Mode-A block.
- `README.md` — drops the Notion backend section and associated paragraphs/images.

**Lightly modified:**
- `plugins/mcbrain/mcp-server/registry.py` — drop `set_notion_config`, `_normalize_db_id`, and the carry-forward loop in `put_vault`.
- `plugins/mcbrain/mcp-server/paths.py` — drop `notion_token_path`.
- `plugins/mcbrain/mcp-server/README.md` — file-tree line + parenthetical example.
- `plugins/mcbrain/skills/mcbrain-ops/SKILL.md` — drop three tool sub-sections.
- `plugins/mcbrain/skills/local-research-db/SKILL.md` — drop "twin of notion" framing and the backend-conflict branches.
- `plugins/mcbrain/skills/local-research-runner/SKILL.md` — drop "twin of notion" framing.
- `plugins/mcbrain/.claude-plugin/plugin.json` — description + keywords + (optional) version bump.
- `.claude-plugin/marketplace.json` — description + keywords + version bump.
- `plugins/mcbrain/commands/mcbrain-setup.md` — drop "Notion DB intent" mention.
- `tests/test_local_research.py` — drop one assertion.

## Dependencies & Ordering

Phases must run in order — earlier phases delete what later phases
would otherwise still refer to:

1. **Phase 1 (engine code)** must run first. The engine code is the
   bottom of the import graph; deleting it first means subsequent
   skill-doc edits don't need to mention tools that no longer exist
   ambiguously.
2. **Phase 2 (Notion skills)** can run independently of Phase 1 but
   ordering it second keeps the diff clean (engine + its callers go
   together).
3. **Phase 3 (setup skill)** depends on Phase 1 (cannot reference
   `enable_notion_for_vault` after it's deleted) and Phase 2 (cannot
   reference `notion-research-db` after the skill bundle is removed).
4. **Phase 4 (vault templates + operating skills)** depends on Phases
   1–3.
5. **Phase 5 (top-level docs, tests, images)** depends on Phases 1–4
   — README must not advertise tools, skills, or sections that no
   longer exist.

Intra-phase ordering is loose. Within Phase 4, however, edit
`claude-md-template.md` before `mcbrain/SKILL.md` so the SKILL.md edit
can reference the template's new shape with confidence.

## Risks & Open Questions

- **Existing user vaults that have a `Backend: notion` entry in their
  CLAUDE.md.** After upgrading the plugin, the `mcbrain` skill will no
  longer understand `Backend: notion`. The user can either:
  (a) downgrade to a pre-removal plugin version; (b) switch their
  CLAUDE.md to `Backend: local` and create a `tasks.jsonl` to
  re-home their work; (c) work from the snapshot `Notion` branch.
  Recommendation: call this out in the README and in the commit/PR
  description. Do **not** ship migration tooling on this branch — pure
  removal is the agreed scope.
- **Plugin version bump.** This is a breaking change for any
  Notion-paired vault. Strongly recommend bumping the plugin to
  `3.0.0` (and marketplace metadata likewise). Flag for the user to
  confirm before committing.
- **Bd issue tracker (`.beads/issues.jsonl`).** Contains issue records
  including past Notion work. Out of scope to edit; the user closes
  issue #19 via `bd close 19` after this lands.
- **The two historical plan files** (`.plan/16-…` and
  `.claude/plans/16-…`) describe the local-tracker design and mention
  Notion. **Leaving them untouched** preserves the project record;
  they're not consumed by code or tests. Confirm this with the user
  if there's a project convention to rewrite historical plans.
- **`tests/fixtures/claude_md_legacy.md`** has a `#### Ingest from
  Notion research tracker` block. The patcher only cares about
  `### Query` / `## Operations`, so this is cosmetic. Leaving as-is
  is the minimal-change path; the user may prefer to scrub it for
  cleanliness — flag and confirm.
- **The seven-skills count in README.md and `plugin.json` description.**
  After deletion, only five skills remain (`mcbrain`, `mcbrain-setup`,
  `mcbrain-ops`, `local-research-db`, `local-research-runner`).
  Update the count in every place it appears.
- **The `## Notion companion databases` legacy section in
  `claude-md-template.md`.** The current template does NOT actually
  include a `## Notion companion databases` *section heading*; it only
  has two prose references to that section name in the Operations and
  Research-tracker bodies. The test
  `test_claude_md_template_has_research_tracker_section` succeeds today
  by matching the prose mentions. After the cleanup, both prose
  mentions go away, so the assertion must be deleted — confirmed in
  Phase 5.

## Verification

After all phases land, run from the worktree:

```bash
.test-venv/bin/python -m pytest tests/ -v
```

Expected:
- `tests/test_local_research.py` passes (one assertion removed).
- `tests/test_patcher.py` passes (the legacy fixture's Notion block
  is cosmetic; patcher logic doesn't read it).
- `tests/test_index.py`, `tests/test_search.py`, `tests/test_e2e.py`,
  `tests/test_unit.py`, `tests/test_static.py` all pass — none of
  them touch Notion code.

Additional verification:

1. **Grep sanity.** From the worktree root:
   ```bash
   grep -rin "notion" plugins/ tests/ README.md AGENTS.md \
       --exclude-dir=__pycache__ --exclude-dir=node_modules
   ```
   Should return **zero matches** (or only matches inside the
   historical `.plan/` / `.claude/plans/` files, if the user chose
   to leave those untouched).

2. **Engine MCP smoke-test.** Launch the engine directly and inspect
   the tool list — should return exactly seven tools:
   `query`, `index_sync`, `index_rebuild`, `index_status`, `migrate`,
   `uninstall`, `list_vaults`.

3. **Setup walkthrough mental model.** Re-read `mcbrain-setup/SKILL.md`
   end-to-end as if running fresh. Verify there is no place that
   asks about Notion, no place that mentions a token file, and the
   research-tracker step is a single yes/no.

4. **README rendering.** Open `README.md` in a markdown previewer and
   confirm: the research-tracker section reads as a single backend, the
   skills list reads as five skills, and no broken image links remain.

5. **Plugin install dry-run.** If feasible, install the plugin into a
   fresh Claude Desktop profile and confirm the marketplace metadata
   loads cleanly and no skills are missing.

Once verification passes, the user can close issue #19 with
`bd close 19` and merge the branch.
