# CLAUDE.md — Edge case

The Query section is followed immediately by an H4 subsection (`#### Notes`) rather than a sibling H3. The naive section-end detector would swallow the H4. The patcher must stop at any heading.

## Operations

### Query
When asked a question against the wiki:
1. Read `wiki/index.md` to find relevant pages
2. Synthesize an answer with citations.

#### Notes about the Query operation
This subsection must survive the patcher.

### Lint
1. Lint wiki.
