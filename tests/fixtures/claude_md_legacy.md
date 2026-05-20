# CLAUDE.md — Test Vault

A vault that pre-dates the McBrain query engine. The patcher should rewrite the Query operation to delegate to mcbrain-ops, append an index sync step to the Ingest procedure, and append a section that records the active engine configuration.

## Operations

### Ingest

#### Ingest from raw/

1. Read each source file in full.
2. Discuss key takeaways.
3. Write or update a wiki page.
4. Update `wiki/index.md`.
5. Append to `wiki/log.md`.
6. Done.

### Query
When asked a question against the wiki:
1. Read `wiki/index.md` to find relevant pages
2. Read those pages
3. Synthesize an answer with citations.

### Lint
1. Sample wiki pages.
2. Report issues.

## Backup

Strategy: none.
