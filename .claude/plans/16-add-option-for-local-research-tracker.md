# Plan: Add option for local research tracker (issue #16)

## Context

McBrain currently relies on Notion for tracking research tasks via two skills:

- `notion-research-db` — creates a Notion DB with the tracker schema (Task, Status, Priority, dates, Notes), then registers it in the vault's `CLAUDE.md` under `## Notion companion databases`.
- `notion-research-runner` — drains "To do" rows, spawns research subagents, and writes findings back to each Notion page. Output is then ingested into the wiki via the `mcbrain-engine` MCP's `ingest_from_notion` tool.

This works well for users who have Notion **and** admin rights to create Notion Connections + integration tokens. For everyone else it's friction. Issue #16 asks for a second option: store research tasks as a local JSONL file inside the vault, with the same schema, and add two parallel skills (`local-research-db`, `local-research-runner`) that mirror the Notion ones. The chosen backend is registered in the vault's `CLAUDE.md` so downstream skills know which one to use. The Notion path remains the default; the new option is additive.

## Recommended Approach

1. **Mirror the Notion schema in JSONL.** Each tracker is a folder under `<vault>/raw/research_tasks/<topic-slug>/` containing exactly one append-only `tasks.jsonl` file, one JSON object per task. Topic-per-folder mirrors how Notion currently models it (one DB per topic) and keeps room for per-topic config later. Single-file-per-topic is much simpler to reason about than one-file-per-task, and atomic enough for this workload (single user, no concurrent writers).
2. **Schema parity with Notion.** Each task object has `id`, `task_name`, `status` (`To do` | `In progress` | `Done`), `priority` (`High` | `Medium` | `Low`), `created_date`, `last_updated_date` (ISO-8601), `notes` (string), `findings` (string, multiline markdown — the runner writes back here instead of to a Notion page body), and `sources` (array of objects). Adding `findings` and `sources` as first-class fields is cleaner than mimicking Notion's "page body vs. property" split, since on disk we don't have the property/body dichotomy.
3. **CLAUDE.md is the source of truth for the chosen backend.** Add a new top-level section `## Research tracker` containing a `Backend:` line whose value is `notion`, `local`, or `none`, plus per-backend metadata (Notion DB list as today, or local folder path for local). Both `mcbrain-setup` (creation time) and the user (later, by re-running the setup skill or by hand) can write this section. Downstream skills (the `mcbrain` skill, the new local skills) read it on every invocation.
4. **Add two new skills bundled with the plugin.** `local-research-db` creates a new local tracker folder + empty `tasks.jsonl` and registers it in `CLAUDE.md`. `local-research-runner` drains up to 5 `To do` rows, claims them in-place (atomic rewrite of the JSONL), spawns research subagents using the existing prompt template, and writes findings back into each row. They live next to `notion-research-db` and `notion-research-runner` under `plugins/mcbrain/skills/` so they ship with the plugin and need no per-user install.
5. **Hook `mcbrain-setup` into the choice.** Step 8 today is "Notion companion database (optional)". Generalize it: ask `Backend?` first (`local` / `notion` / `none`), then run the existing 8a–8g flow only on the Notion branch and a new lightweight 8L flow on the local branch (scaffold the folder, write the CLAUDE.md section, no token, no MCP calls). Default-recommend `local` because it has zero dependencies; users who want Notion can pick it.
6. **Update the Notion-bridged ingest path to be backend-aware.** The `mcbrain` skill's "Routing ingest to the right mode" already routes between Notion and standard ingest. Add a parallel "local-research-bridged ingest" mode that reads completed tasks (`status == In progress` with non-empty `findings`) from the local JSONL, writes each to `raw/notes/<task-id>.md` with provenance frontmatter, then runs the standard ingest. No new engine MCP tool is needed for this — it's pure markdown writing inside the vault, and the filesystem MCP can do it.
7. **Backwards compatibility.** Vaults that already exist with `## Notion companion databases` keep working: the new `## Research tracker` section is *added* (not replacing the existing Notion-DB list section, which the engine still references). When the new mcbrain skill reads CLAUDE.md and finds no `## Research tracker` section, it falls back to "look for Notion companion databases", preserving today's behavior.

## Changes

### `plugins/mcbrain/skills/local-research-db/` (NEW)

- `SKILL.md` — modeled on `notion-research-db/SKILL.md` but with these differences:
  - No Notion connector check, no parent-page selection.
  - **Inputs:** research topic; associated McBrain vault (resolved via the same `mcbrain-*` MCP enumeration logic — copy the *Identifying the McBrain vault* section verbatim).
  - **Workflow:**
    1. Resolve vault, derive `<topic-slug>` (lowercase, hyphenated).
    2. Ensure `<vault>/raw/research_tasks/<topic-slug>/` exists; create `tasks.jsonl` (empty file) and `README.md` (one-paragraph human-readable description of the schema, mostly for users browsing the folder in Obsidian).
    3. **Register in `CLAUDE.md`** under a new `## Research tracker` section:

       ```markdown
       ## Research tracker

       - Backend: local
       - Trackers:
         - **<Research Topic>**
           - Topic slug: <topic-slug>
           - Path: raw/research_tasks/<topic-slug>/tasks.jsonl
           - Registered: <YYYY-MM-DD>
           - Notes: companion local research tracker for this vault.
       ```

       If a `## Research tracker` section already exists (with `Backend: local` or unset), append the new entry under `Trackers:` rather than overwriting. If `Backend:` is `notion`, surface the conflict to the user and ask whether to switch the backend or keep both (we permit both — see *Mixed backends* note below).
    4. Append to `wiki/log.md`: `- YYYY-MM-DD — registered local tracker: <topic>`.
    5. **Backup.** Same git-vs-google-drive-vs-none copy-paste-fence pattern as `notion-research-db`.

  - **Why a separate folder per topic, not one flat file.** Mirrors Notion's "one DB per topic" model so existing vaults migrating from Notion don't lose granularity, makes per-topic git diffs/blame readable, and leaves space for future per-topic config (e.g. `config.json` with batch-size overrides) without changing the row format.

- No `references/` content needed — the schema is small enough to live inline.

### `plugins/mcbrain/skills/local-research-runner/` (NEW)

- `SKILL.md` — modeled on `notion-research-runner/SKILL.md`. Same Phase 1–5 shape. Differences:
  - **No Notion-connector capability matching at startup.**
  - **Inputs:** which tracker (resolve via `## Research tracker` section in CLAUDE.md; ask if multiple); batch size (default 5, hard cap 5).
  - **Phase 1 — Fetch candidates.** Read `raw/research_tasks/<topic>/tasks.jsonl`, parse line-by-line, filter `status == "To do"`, sort by `priority` (High → Medium → Low) then `created_date` ascending, take the first N.
  - **Phase 2 — Claim.** Atomic rewrite of `tasks.jsonl`:
    1. Read the entire file into memory.
    2. For each claimed task, set `status = "In progress"` and `last_updated_date = <now ISO-8601>`.
    3. Write to a temp file in the same directory, then rename over the original. (Same machine, same filesystem; rename is atomic.) The filesystem MCP supports both `write` and `move`, so this is straightforward — and matches how Notion's runner claims tasks before research starts so a second run can't pick the same rows.
  - **Phase 3 — Plan + research.** Reuse the existing `notion-research-runner/references/research_subagent_prompt.md` template verbatim — it's backend-agnostic. Spawn one `general-purpose` subagent per claimed task in a single message (parallel).
  - **Phase 4 — Write findings back.** For each subagent return:
    - Open `tasks.jsonl`, find the row by `id`, populate `findings` with the *complete* Markdown output (all four sections: Summary, Key Findings, Open Questions, Sources — matching the Notion runner's "do not summarize, truncate, paraphrase, or drop anything" rule), populate `sources` with a structured array parsed from the Sources section (best-effort; on parse failure fall back to keeping the markdown intact in `findings` and leaving `sources` empty), bump `last_updated_date`. Atomic rewrite again.
    - **Do not** flip `status` to `Done` automatically — leave it at `In progress` so the user / Notion-bridged-equivalent ingest path can close the loop.
  - **Phase 5 — Report.** Each task title, one-line takeaway, and the row's `id` (so the user can grep for it in `tasks.jsonl`).
  - **Failure modes** (mirror the Notion runner's section): malformed subagent output, partial writes, runner interrupted mid-claim. Specifically call out that an interrupted Phase 2 leaves rows at `In progress` with empty `findings` — the runner detects this on next invocation (status == `In progress` AND `findings` empty) and offers to retry just those.

### `plugins/mcbrain/skills/mcbrain-setup/SKILL.md` — modify Step 8

Restructure Step 8 from "Notion companion database (optional)" to "Research tracker setup (optional)". The existing Notion sub-steps move under a `notion` branch.

- New **8a — Choose research-tracker backend.** Ask via `AskUserQuestion`:

  ```yaml
  questions:
    - question: "How do you want to track research tasks for this McBrain?"
      header: "Research tracker"
      multiSelect: false
      options:
        - label: "Local (Recommended — no Notion needed)"
          description: "Tracks tasks as a JSONL file in your vault under raw/research_tasks/. Zero dependencies, works offline."
        - label: "Notion"
          description: "Tracks tasks in a Notion database. Needs a Notion MCP connector and admin rights to create integrations."
        - label: "Skip for now"
          description: "Don't set one up. You can add one later by re-running mcbrain-setup or running local-research-db / notion-research-db."
  ```

  Store as `RESEARCH_TRACKER_BACKEND` ∈ {`local`, `notion`, `none`}.

- **8L — Local backend branch** (only if `local`). Create a default tracker named after the vault (e.g. `mcbrain-finance` → topic slug `finance`):
  1. Create `<vault>/raw/research_tasks/<topic-slug>/` and `<topic-slug>/tasks.jsonl` (empty file) using the Write tool against the granted vault mount.
  2. Append a `## Research tracker` section to `CLAUDE.md` with `Backend: local` and one entry under `Trackers:` (template above).
  3. No Notion token, no MCP-engine call (the engine doesn't need to know about local trackers — they're just files in the vault, which the filesystem MCP already has access to).
  4. If git backup, present a single copy-paste commit fence: `git add CLAUDE.md raw/research_tasks/ && git commit -m "init: local research tracker" && git push`.

- **8N — Notion backend branch** (only if `notion`). This is today's 8a–8g, renumbered. The existing token install (8f) and `enable_notion_for_vault` call (in 8.5) only happen on this branch. Skip them entirely on the local branch.

- Update the **Required intake** table at the top of the SKILL: rename row 8 to `RESEARCH_TRACKER_BACKEND ∈ {local, notion, none}`. Step 8.5's `enable_notion_for_vault` call becomes conditional on `RESEARCH_TRACKER_BACKEND == notion`.

### `plugins/mcbrain/skills/mcbrain-setup/references/claude-md-template.md` — modify

- **Replace** today's `## Notion companion databases` section with a unified `## Research tracker` section that supports both backends. Schema:

  ```markdown
  ## Research tracker

  Backend: <local | notion | none>

  <!-- If backend is local: -->
  Trackers:
    - **<Topic>**
      - Topic slug: <slug>
      - Path: raw/research_tasks/<slug>/tasks.jsonl
      - Registered: <YYYY-MM-DD>

  <!-- If backend is notion: -->
  Notion databases:
    - **<DB Name>**
      - URL: <URL>
      - Database ID: <hex>
      - Registered: <YYYY-MM-DD>

  <!-- If backend is none: empty body. -->
  ```

  Keep a one-paragraph explanation block above so a new reader (or Claude) knows what the section is for and which downstream skills consume it.

- **Update the existing "Ingest from Notion research tracker" sub-section** (in `## Operations → Ingest`) so the introductory paragraph references *Backend: notion* explicitly: "Use this when CLAUDE.md's `## Research tracker → Backend` is `notion`. For `local`, see *Ingest from local research tracker* below."

- **Add a parallel "Ingest from local research tracker" sub-section** that documents the local-bridged ingest:
  1. Read the local tracker's `tasks.jsonl`. Filter rows with `status == "In progress"` AND non-empty `findings`.
  2. For each, write `<vault>/raw/notes/<task-id>.md` with frontmatter `source: local-research-tracker`, `tracker: <topic-slug>`, `task_id`, `task_name`, `research_date: <last_updated_date>`, `captured: <today>`, plus the `findings` markdown body. Idempotent: skip files whose body matches the row's findings (cheap content hash).
  3. Run the standard wiki-update steps against the new `raw/notes/*.md` files.
  4. **Close the loop.** After the wiki write succeeds for a task, atomically rewrite `tasks.jsonl` to flip its `status` to `Done`. This mirrors the Notion path's flip-to-Done.
  5. Append to `wiki/log.md`: `- [YYYY-MM-DD] ingest (local) | <topic> — <N> tasks`.
  6. Call `index_sync` on the engine MCP.

  The intentional design is that this whole flow lives in CLAUDE.md (procedure) and uses only the filesystem MCP (already available) plus the engine MCP's `index_sync` (already available) — **no engine code changes required**.

### `plugins/mcbrain/skills/mcbrain/SKILL.md` — modify routing

- Update the "Routing ingest to the right mode" section so it routes to one of three modes instead of two:
  - **Mode A — Notion-bridged ingest.** Existing logic, gated on `## Research tracker → Backend == notion`.
  - **Mode B — Local-research-bridged ingest.** New. Triggered by the same conversational signals as Mode A ("the research", "those tasks", "the tracker", or after a `local-research-runner` pass) but gated on `Backend == local`. Route to the *Ingest from local research tracker* procedure in CLAUDE.md.
  - **Mode C — Standard ingest.** Existing.
- Update the "Deferrals to CLAUDE.md" bullet about source-ingestion paths to mention the local-research tracker as a fourth path alongside Web Clipper, Claude in Chrome, hand drops, and Notion.

### `plugins/mcbrain/.claude-plugin/plugin.json` — minor

- Bump `version` (e.g. `2.1.0` → `2.2.0` — minor, additive feature).
- Update `description` to mention "local or Notion-backed research trackers" (instead of just "Notion").
- Add `local-research` to `keywords`.

### `tests/` — add a small static-test layer

- New `tests/test_local_research.py`. The two new skills are pure markdown so they don't import in pytest the way the engine does — but the JSONL schema and the atomic-rewrite logic are testable:
  - **`test_jsonl_schema_documented`** — read both new SKILL.md files, assert each lists every required field (`id`, `task_name`, `status`, `priority`, `created_date`, `last_updated_date`, `notes`, `findings`, `sources`).
  - **`test_runner_claim_then_findings_round_trip`** — minimal Python helper (in the test itself, not in the skill) that simulates Phase 2 + Phase 4 against a fixture `tasks.jsonl`: load → claim 3 rows → verify atomic rewrite → "complete" them by writing findings → verify status/findings/last_updated_date are correct and other rows are unchanged. This exercises the procedure described in the SKILL even though the SKILL itself isn't Python.
  - **`test_claude_md_template_has_research_tracker_section`** — assert the template now has `## Research tracker` and that the legacy `## Notion companion databases` heading is *either* gone or clearly marked as the old name (so existing vaults still grep-matchable).
- No changes to engine tests — the engine doesn't change.

## Critical Files

- `plugins/mcbrain/skills/local-research-db/SKILL.md` — NEW.
- `plugins/mcbrain/skills/local-research-runner/SKILL.md` — NEW (uses Notion runner's prompt template via `references/research_subagent_prompt.md`; either symlink or duplicate — duplicate is safer for skill installability).
- `plugins/mcbrain/skills/local-research-runner/references/research_subagent_prompt.md` — NEW (verbatim copy of the Notion one; backend-agnostic content already).
- `plugins/mcbrain/skills/mcbrain-setup/SKILL.md` — MODIFIED (Step 8 restructure, Required-intake table update).
- `plugins/mcbrain/skills/mcbrain-setup/references/claude-md-template.md` — MODIFIED (new `## Research tracker` section; new `Ingest from local research tracker` sub-procedure).
- `plugins/mcbrain/skills/mcbrain/SKILL.md` — MODIFIED (three-mode ingest routing).
- `plugins/mcbrain/.claude-plugin/plugin.json` — MODIFIED (version bump, description, keywords).
- `tests/test_local_research.py` — NEW.

No engine MCP code changes. No new MCP tools. No changes to `notion.py`, `mcbrain_engine.py`, or `registry.py`.

## Dependencies & Ordering

1. Update `claude-md-template.md` first — defines the contract that every other change consumes.
2. Build the two new skills (`local-research-db`, `local-research-runner`) — they are independent of `mcbrain-setup` and `mcbrain` and can be developed and tested in isolation.
3. Update `mcbrain-setup/SKILL.md` Step 8 — depends on the template change (it writes the new section).
4. Update `mcbrain/SKILL.md` routing — depends on the template change (it reads the new section).
5. Bump `plugin.json` version — last, after everything is in.
6. Add tests — can be in parallel with steps 2–4, but run them at the end.

## Risks & Open Questions

- **Mixed backends.** A user could conceivably want both — Notion for one topic and local for another. The `## Research tracker` section design above forces a single `Backend:` value, which is a deliberate simplification. If we want to support mixed backends, the section would need per-tracker backend tags. **Recommendation: ship as single-backend, document the limitation, and revisit if a user actually asks for mixed.** Keeping it single-backend is also the only way the runner skills can unambiguously decide which to use without prompting every time.
- **JSONL atomicity.** The "read everything → write to temp → rename" pattern is correct and standard, but the filesystem MCP's `write` tool overwrites in place rather than supporting tmp+rename. Two options: (a) trust the filesystem MCP's `write` since this is single-user / single-writer with no concurrent runners — the worst case (interrupted write) leaves a truncated file, which is detectable on next read; (b) include the JSONL inside a small wrapper that includes a header line with a timestamp and writes to a sidecar `tasks.jsonl.tmp` followed by a `move`. **Recommendation: option (a). It's simpler and matches how mcbrain treats the rest of the vault — single writer, no concurrency.** The runner SKILL should explicitly call out the failure mode and the recovery path (truncated file → user keeps a git backup, or Drive sync, or none = local risk they accepted).
- **Schema for `sources`.** The Notion runner writes sources as numbered list items in the page body. Locally we have the option of structured JSON. Decision: keep the markdown rendering in `findings` (so the user reading the JSONL row sees the full content) AND best-effort parse the same Sources section into a structured `sources` array. If parsing fails, leave `sources: []` and the markdown still has everything. This way downstream tooling can use the structured field if it wants to, without losing the human-readable form.
- **Token-cost discipline.** A failure mode of the local runner is that `findings` blocks balloon the JSONL file, which the runner re-reads on every invocation. With ~5 tasks × 5 KB each that's 25 KB — fine. But after a year of use, a tracker with 200 closed tasks could be 1 MB+. The runner only reads (it doesn't pass file content into a prompt) and the file is markdown lines, so 1 MB is still fine. If it ever becomes an issue, archive completed tasks into a sibling `tasks.archived.jsonl`. Note this in the SKILL but don't implement it now.
- **Migration of existing Notion users.** Out of scope. Existing vaults keep their `## Notion companion databases` section working — the mcbrain SKILL's fallback (when `## Research tracker` is absent) preserves today's behavior. A user who *wants* to switch from Notion to local re-runs `mcbrain-setup` Step 8 (or runs `local-research-db` standalone), which writes the new section; they keep the Notion section too if they want to continue using both during a transition. Document this fallback behavior in `mcbrain/SKILL.md`.
- **Naming.** `local-research-db` is slightly awkward (there's no DB; it's a JSONL file). Considered alternatives: `local-research-tracker`, `local-research-init`. Sticking with `local-research-db` for symmetry with `notion-research-db` so users with muscle memory pick it up immediately. The SKILL description can disambiguate.
- **Plan-mode constraint.** This plan deliberately keeps engine changes off the table. If during implementation it becomes obvious that a `local_ingest` engine tool would be useful (for example to skip writing through the filesystem MCP for very large findings), that's a follow-up issue, not part of this one.

## Verification

**Manual end-to-end** (the most informative check, given that two of the new artifacts are skills, not Python):

1. **Setup with local backend.** Run `mcbrain-setup` from scratch in a fresh sandbox; pick `Local` at Step 8a. Expect: `<vault>/raw/research_tasks/<topic>/tasks.jsonl` exists (empty), CLAUDE.md has `## Research tracker` with `Backend: local`, no Notion token prompt appears.
2. **Setup with Notion backend.** Re-run setup, pick `Notion`. Expect: today's Step 8 flow runs (DB creation/registration, token install, `enable_notion_for_vault`). CLAUDE.md has `## Research tracker` with `Backend: notion` plus the `Notion databases:` sublist.
3. **Setup with none.** Re-run setup, pick `Skip for now`. Expect: `## Research tracker → Backend: none` and no other artifacts created. The mcbrain SKILL doesn't offer either runner.
4. **Add a tracker post-setup.** With a local-backend vault, run `local-research-db` for a new topic. Expect: a second tracker folder under `raw/research_tasks/`, the new entry appended under `Trackers:` in CLAUDE.md.
5. **Run the local runner end-to-end.** Hand-write three rows in `tasks.jsonl` with `status: To do` and varied priority. Run `local-research-runner`. Expect: rows flip to `In progress` immediately (Phase 2), three subagents run in parallel, the JSONL rewrites with `findings` populated and `sources` filled (or empty with markdown-only fallback), `status` stays at `In progress`.
6. **Local-bridged ingest.** Tell Claude "ingest the research" with the runner's output still at `In progress`. Expect: Mode B selected (state which mode in chat per the existing routing rule), one `raw/notes/<task-id>.md` per task with provenance frontmatter, wiki pages created/updated, JSONL rows flipped to `Done`, `index_sync` called.
7. **Backwards compatibility.** Open a vault that pre-dates this change (no `## Research tracker` section, only `## Notion companion databases`). Run `mcbrain` operations. Expect: the skill detects the legacy layout, falls back to `Backend == notion` behavior, doesn't error, doesn't ask the user to migrate.

**Automated** (`pytest tests/test_local_research.py`):

- All three tests in the new file pass.
- The existing engine test suite (`test_static.py`, `test_unit.py`, `test_patcher.py`, `test_index.py`, `test_search.py`, `test_e2e.py`) continues to pass unchanged — engine code wasn't touched.

**Idempotency checks:**

- Running `local-research-db` twice with the same topic is a no-op (folder + jsonl already exist; CLAUDE.md entry detected as already-present).
- Running `local-research-runner` twice in a row drains a different batch the second time (Phase 2's claim is what makes this true).
- Running the local-bridged ingest twice doesn't re-create raw notes for already-Done tasks.
