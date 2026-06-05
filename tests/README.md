# Tests

Two suites: a pytest suite for the local research tracker skills (`local-research-db` and `local-research-runner`), and a `node:test` suite for the mcbrain MCP server.

| Suite | File | What it covers |
|---|---|---|
| pytest | `test_local_research.py` | End-to-end behavior of the JSONL-backed research tracker that lives in each vault at `raw/research_tasks/tasks.jsonl` |
| node:test | `../plugins/mcbrain/mcp-server/test/server.test.js` | The mcbrain MCP server: registry CRUD, file-gateway path scoping (traversal/symlink escapes), and `migrate_config` — spoken over real JSON-RPC stdio |

## Running

Python tests are pure-stdlib; only pytest is needed.

```sh
# From the repo root
python3 -m venv .test-venv
.test-venv/bin/pip install -r tests/requirements.txt
.test-venv/bin/python -m pytest tests/ -v
```

The MCP server tests need only Node.js (>= 18, no npm install):

```sh
# From the repo root
node --test plugins/mcbrain/mcp-server/test/
```

Both suites finish in seconds.
