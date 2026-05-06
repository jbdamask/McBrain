# Research Subagent Prompt Template

Use this template when spawning a `general-purpose` subagent for one local research task. Substitute the bracketed fields. Keep the prompt self-contained — the subagent has no memory of the parent conversation.

The output produced by this template is the verbatim body of the markdown file that the runner writes to `<vault>/raw/notes/research-<topic-slug>-<task-id>.md` (after a YAML frontmatter block prepended by the runner). The standard ingest flow then picks that file up.

## Template

```
You are researching one item from a local research tracker. Work in two phases:

PHASE 1 — PLAN (do this first, internally):
- Restate the research question in your own words.
- List 3–6 sub-questions that together cover the topic.
- Decide which source types matter (primary literature, docs, standards bodies, reputable news, vendor pages, etc.).
- Note what "done" looks like for this task.

PHASE 2 — EXECUTE:
- Use WebSearch / WebFetch to gather evidence for each sub-question.
- Prefer primary and authoritative sources. Skip SEO spam and content farms.
- Track every source you actually used (URL + title + one-line why-it-matters).
- Synthesize findings. Do not pad. If evidence is thin or contradictory, say so.

TASK CONTEXT
- Research topic: {{RESEARCH_TOPIC}}
- Task name: {{TASK_NAME}}
- Priority: {{PRIORITY}}
- Existing notes from the tracker row: {{NOTES_OR_"(none)"}}

OUTPUT FORMAT — return exactly this Markdown, nothing else:

## Summary
<3–6 sentence executive summary answering the task>

## Detailed Results
<A narrative section that walks through the evidence sub-question by sub-question. Cite sources inline with [n] matching the numbered Sources list. This is where the substance of the research lives — Summary is the headline, this is the body. Aim for several paragraphs; do not pad.>

## Key Findings
- <finding 1 — one to three sentences, concrete>
- <finding 2>
- <finding 3>
- <add more as warranted; aim for 4–8 bullets total>

## Open Questions
- <anything unresolved or worth a follow-up task>

## Sources
1. [<Title>](<URL>) — <YYYY-MM-DD or YYYY-MM or YYYY> — Credibility: <1-5> — <one-line why-it-matters>
2. [<Title>](<URL>) — <YYYY-MM-DD or YYYY-MM or YYYY> — Credibility: <1-5> — <one-line why-it-matters>
...

CREDIBILITY RUBRIC (assign one score per source)
- 5 — Articles in high-impact peer-reviewed journals (Nature, Science, NEJM, Cell, top field-specific journals), official standards bodies, primary government data.
- 4 — Pre-prints on recognized servers (arXiv, bioRxiv, medRxiv, SSRN), conference proceedings, working papers from established institutions.
- 3 — Blog posts or essays from recognized domain experts, established trade publications, well-edited industry analyst reports.
- 2 — Personal Substacks / Medium posts from non-experts, mid-tier news aggregators, vendor marketing pages with original claims.
- 1 — Tweets / social media posts, anonymous forum comments, content farms, AI-generated summary sites.

If a source straddles two levels, pick the lower one and explain in the why-it-matters line.

HARD RULES
- Every non-trivial claim in Summary / Detailed Results / Key Findings must be traceable to a source in the Sources list.
- Cite inline with [n] matching the numbered Sources list.
- Every source MUST include a publication/last-updated date and a credibility score. If you cannot find a date, write "undated" and drop the credibility score by 1 (minimum 1).
- Do not invent URLs. If you did not open a page, do not list it.
```

## Notes on Using the Template

- Pass this prompt via the Agent tool with `subagent_type: "general-purpose"`.
- The returned Markdown is what gets written verbatim into the file body. The runner prepends a YAML frontmatter block (`source: local-research-tracker`, `topic`, `topic_slug`, `task_id`, `task_name`, `research_date`, `captured`) and then the subagent's output unchanged.
- Do not summarize, truncate, paraphrase, or drop any of the five sections — Summary, Detailed Results, Key Findings, Open Questions, Sources. If a section would be empty (e.g. no open questions), render it as `None.` rather than omitting it.
- If the subagent returns sources with bare URLs (no titles), fix them up before writing — titles make the file readable later.
