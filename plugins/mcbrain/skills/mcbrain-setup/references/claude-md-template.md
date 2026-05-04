# CLAUDE.md — McBrain Schema

This is the schema file for McBrain, the user's personal LLM-maintained knowledge base. Read this at the start of every McBrain session before touching any files in this vault.

## What McBrain is

A personal knowledge base maintained by Claude. Raw sources live in `raw/`. Claude owns and maintains everything in `wiki/`. The user never writes wiki pages — Claude does. The user drops sources, asks questions, and directs the analysis.

## What lives where

Three layers, three jobs:

- **CLAUDE.md** — Claude's operating manual for this vault: schema, page conventions, ingest/query/lint procedures, backup config, MCP plumbing, and registered companion systems (e.g. Notion trackers). Anything Claude needs to *do its job* lives here.
- **`raw/`** — immutable source documents. Inputs.
- **`wiki/`** — compiled knowledge derived from `raw/`. Concept pages, entity pages, syntheses. Every page has provenance (`sources:` frontmatter pointing back to `raw/`).

The rule: **if Claude needs it to do its job, it lives in CLAUDE.md; if it's a fact about the world derived from a source, it lives in `wiki/`.** Plumbing (the Notion DB registry, the git remote, the MCP path) does not belong in `wiki/` — wiki pages have provenance and citations, plumbing doesn't. When in doubt, ask which question the content answers: *"how do I operate this vault?"* (CLAUDE.md) vs *"what do we know about X?"* (`wiki/`).

## Directory layout

```
raw/          Immutable source documents. Never modify these.
  articles/   Web clips and saved articles
  papers/     Research papers and PDFs
  notes/      Personal notes, journal entries, transcripts
  assets/     Downloaded images and attachments

wiki/         LLM-maintained compiled knowledge
  index.md    Master catalog — update on every ingest
  log.md      Append-only operation log — update on every operation
  overview.md High-level synthesis — update periodically
  [topic].md  Topic/concept/entity pages — you create and maintain these
```

## Page conventions

Every wiki page must have YAML frontmatter:

```yaml
---
type: concept | entity | source | comparison | synthesis
tags: [tag1, tag2]
sources: [raw/articles/filename.md]
confidence: high | medium | low
updated: YYYY-MM-DD
research_date: YYYY-MM-DD
source_dates: YYYY to YYYY
---
```

**Date metadata rules — non-negotiable:**
- `updated`: the date this wiki page was last edited by Claude.
- `research_date`: the date Claude conducted the research behind this page. Must always be set. Tells the user how fresh the underlying research is.
- `source_dates`: the publication date range of sources cited (e.g. `2019 to 2026`). For a single source, use that date. For internal notes or email threads with no publication date, use the document date.

**Why this matters:** Knowledge bases go stale. Technology benchmarks, API terms, pricing, regulatory status, and competitive landscapes can shift within months. Every page must be auditable for currency so the user knows what to trust and what to refresh. During lint passes, flag any page where `research_date` is more than 6 months old and external claims may be stale.

Use `[[wikilinks]]` for every cross-reference. Every named thing (person, project, concept) that appears in more than one page gets its own wiki page. Link aggressively — the graph is the value.

Keep page filenames lowercase with hyphens (e.g. `machine-learning.md`). The filename is the canonical name.

### Page body format

After the YAML frontmatter, every wiki page should follow this structure:

```markdown
# Page Title

**Summary:** One to two sentences describing this page.

**Sources:** List of raw source files this page draws from (with publication dates where known).

**Research date:** YYYY-MM-DD — when Claude researched this page.

**Source dates:** YYYY to YYYY — publication date range of sources cited.

**Last updated:** YYYY-MM-DD

---

Main content goes here. Use clear headings and short paragraphs.

Link to related concepts using [[wikilinks]] throughout the text.

## Related pages

- [[related-concept-1]]
- [[related-concept-2]]
```

## Citation rules

- Every factual claim should reference its source file — use the format `(source: filename.md)` after the claim
- If two sources disagree, note the contradiction explicitly on the relevant page rather than silently picking one
- If a claim has no source, mark it `(source: unverified)` so it can be resolved later

## Operations

**Always sync the search index after any wiki write.** Whenever you create, modify, or delete a file under `wiki/` — whether through a formal ingest, a lint pass that updates `log.md`, an ad-hoc page update, or a synthesis filing — call the `mcbrain-engine` MCP's `index_sync` tool against this vault afterward so subsequent queries see the change. Cheap (sub-second on no-op). The specific operation procedures below all end with this step explicitly; this paragraph is the catch-all for anything not enumerated.

### Ingest

"Ingest" has two modes. The `mcbrain` skill picks one based on conversation context (see its routing section). Both modes ultimately funnel through the same wiki-update steps below — the difference is where the source comes from.

#### Ingest from raw/

Use when the user says "ingest" with no Notion context, or names a file already in `raw/`.

1. **Find what's unprocessed.** If the user did not name a specific file, scan `raw/` recursively and diff against the `sources:` frontmatter field of every page in `wiki/`. Anything in `raw/` that no wiki page lists as a source is a candidate. List the candidates and confirm with the user before processing more than one or two at a time.
2. Read each source file in full.
3. Discuss key takeaways (briefly, unless asked for more).
4. Write or update a source summary page in `wiki/`.
5. Create or update entity and concept pages touched by this source.
6. Update `wiki/index.md` with the new page(s).
7. Append to `wiki/log.md`: `## [YYYY-MM-DD] ingest | [source title]`.
8. Call the `mcbrain-engine` MCP's `index_sync` tool against this vault so the new wiki page(s) are searchable immediately. Cheap (sub-second on no-op).

A single source may touch 5-15 wiki pages. That's normal.

#### Ingest from Notion research tracker

Use when the user has just finished a `notion-research-runner` pass, or references the Notion tracker / completed research / those tasks. The `notion-research-runner` skill leaves completed research on Notion task pages with Status `In progress`; this mode pulls that output into `raw/` and then runs the standard wiki-update steps.

**Hard rule: do not re-research.** Do not spawn research subagents, do not call `notion-research-runner`, do not run web search for the topic. The research is already done — your job is to file it, not redo it.

1. **Find the companion DB.** Look in this CLAUDE.md's `## Notion companion databases` section first — that's the canonical registry, populated by `mcbrain-setup` (Step 8) and by `notion-research-db`. If that section is missing or empty, fall back to `wiki/notion-databases.md` (the legacy location used by older vaults). Capture the database URL / ID. If multiple trackers are registered for this vault, ask the user which one.
2. **Pull completed task pages.** Query the tracker for rows with Status `In progress` whose page body contains a research run (look for the `## Summary` / `## Key Findings` / `## Sources` section headings the runner writes). If the user named specific tasks, use those instead. Skip rows whose Notion page ID already appears in the `sources:` frontmatter of any wiki page — those have already been ingested.
3. **Copy to `raw/notes/` verbatim.** For each task, write the page body to `raw/notes/<slug>.md` (slug = lowercased, hyphenated task title). Prepend YAML frontmatter capturing provenance:

   ```yaml
   ---
   source: notion-research-tracker
   notion_url: <task page URL>
   notion_page_id: <task page ID>
   tracker: <tracker name>
   task_title: <task name>
   research_date: <date the runner wrote the page>
   captured: YYYY-MM-DD
   ---
   ```

   Do not edit, summarize, or reformat the research output. Copy it as-is — `raw/` is immutable history.
4. **Run the standard wiki-update steps** (steps 2–7 of *Ingest from raw/* above) against the new `raw/notes/<slug>.md` files. Cite them in wiki pages exactly the same way you'd cite any other raw source.
5. **Close the loop in Notion.** After the wiki write succeeds, flip the Notion task's Status to `Done`. If the wiki write failed for a task, leave the Notion status at `In progress` and surface the error so the user can retry.
6. Append to `wiki/log.md`: `## [YYYY-MM-DD] ingest (notion) | <tracker> — <N> tasks`.
7. Call the `mcbrain-engine` MCP's `index_sync` tool against this vault so the new wiki page(s) are searchable immediately.

### Query
When asked a question against the wiki:
1. Call the `mcbrain-engine` MCP's `query` tool with `vault=<this vault's name>` (or `vault_path` as fallback) and `text=<question>`. It returns a ranked list of wiki page paths + scores as JSON, fused from lexical (ripgrep) and semantic (FastEmbed) search via Reciprocal Rank Fusion.
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

### Lint
When asked to lint:
1. Read `wiki/index.md`
2. Sample wiki pages
3. Report:
   - **Contradictions** between pages
   - **Orphan pages** (no inbound links)
   - **Stale claims** that newer sources have superseded
   - **Missing cross-references** — pages that should `[[link]]` each other but don't
   - **Concept gaps** — important concepts mentioned but lacking their own page
   - **Format drift** — pages that don't follow the page body format
   - **Data gaps** — claims that could be verified or enriched with a web search; propose specific searches
   - **Suggested new sources** — topics or questions where the wiki would benefit from more material; name concrete sources (papers, articles, books) worth adding to `raw/`
   - **Stale pages** — any page where `research_date` is more than 6 months before today's date AND the page contains external claims (pricing, API terms, regulatory status, competitive landscape). Flag these explicitly with a suggested refresh action.
4. Propose specific fixes for each finding
5. Append to `wiki/log.md`: `## [YYYY-MM-DD] lint`

## Rules

- Never modify files in `raw/`. Read-only.
- Always update `wiki/index.md` and `wiki/log.md` on every operation.
- Use `[[wikilinks]]` not bare text for every cross-reference.
- Prefer updating existing pages over creating new ones when the concept already has a page.
- If a source contradicts an existing wiki claim, note the contradiction on the relevant page — don't silently overwrite.
- Keep page filenames lowercase with hyphens — the filename is the canonical name.
- Write in clear, plain language. Short paragraphs, clear headings.
- When uncertain about how to categorize something, ask the user.

## Source ingestion paths

Sources can arrive in `raw/` through several paths. All feed the same ingest procedure once the file is on disk:

- **Obsidian Web Clipper** (browser extension) — clips web articles directly into `raw/articles/` as markdown. Fast one-click capture from any tab the user is already on.
- **Claude in Chrome via Cowork** — when the user asks Claude to ingest a URL, Claude can navigate the page (including authenticated/paywalled ones, since it shares the user's browser session), convert to markdown, and save to `raw/articles/<slug>.md`.
- **Hand drops** — the user drags PDFs into `raw/papers/`, pastes notes into `raw/notes/`, or otherwise adds files manually.
- **Notion research tracker** — completed research run by `notion-research-runner` lives on Notion task pages. The Notion-bridged ingest mode (see `## Operations → Ingest from Notion research tracker`) copies those page bodies into `raw/notes/<slug>.md` with provenance frontmatter, then proceeds like any other raw source. Never re-run the research — the output already exists.

Treat all of these identically once the file is on disk. Run the standard ingest procedure regardless of how the source got there.

## Handling images in sources

Articles clipped from the web often contain inline image references (Obsidian Web Clipper downloads them to `raw/assets/`). LLMs can't read markdown and its inline images in a single pass. The workflow:

1. Read the source markdown text first.
2. Note which images look substantive (diagrams, screenshots with data, photos central to the content) vs. decorative (hero images, stock photos).
3. View the substantive images separately, one or a few at a time, to extract what they convey.
4. Integrate image content into the relevant wiki pages. Link to the image file in `raw/assets/` if the image itself is worth referencing; otherwise just absorb the information into prose.

Skip decorative images — they waste context.

## Log format

Each log entry:
```
## [YYYY-MM-DD] operation | title
One-line description of what was done.
Files touched: wiki/page1.md, wiki/page2.md
```

## Query engine

- mode: lexical+semantic (mcp)
- embedding_model: BAAI/bge-small-en-v1.5
- embedding_dim: 384
- index_path: .mcbrain/index.db

The query engine is a global stdio MCP server (`mcbrain-engine`) shared by every McBrain on this machine. It runs hybrid lexical (ripgrep / grep / pure-Python fallback) plus semantic (FastEmbed, brute-force cosine via numpy over a SQLite embedding column) search, fused via Reciprocal Rank Fusion. The per-vault index lives at `.mcbrain/index.db`. Use the `mcbrain-engine` MCP's tools (`query`, `index_sync`, `index_rebuild`, `index_status`, `migrate`, `uninstall`, `list_vaults`) to interact with it.

If this section is missing or still uses the legacy `mode: lexical+semantic` marker (no `(mcp)` suffix), the `mcbrain` skill detects it on next invocation and offers to upgrade by calling the `mcbrain-engine` MCP's `migrate` tool.

## Notion companion databases

Companion Notion research trackers registered to this vault. The Notion-bridged ingest mode reads this section to find which DB to drain. `mcbrain-setup` (Step 8) populates the first entry; `notion-research-db` adds entries when the user creates additional trackers. Leave the section empty (just the heading + this paragraph) if no Notion DB is paired.

<!-- Entries below — one per database -->

## Domain

[User fills this in — what is McBrain about? What's the primary focus area?]

## Notes from past sessions

