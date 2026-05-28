# Tests

Pytest suite for the local research tracker skills (`local-research-db` and `local-research-runner`).

| File | What it covers |
|---|---|
| `test_local_research.py` | End-to-end behavior of the JSONL-backed research tracker that lives in each vault at `raw/research_tasks/tasks.jsonl` |

## Running

Pure-stdlib tests; only pytest is needed.

```sh
# From the repo root
python3 -m venv .test-venv
.test-venv/bin/pip install -r tests/requirements.txt
.test-venv/bin/python -m pytest tests/ -v
```

The full suite finishes in well under a second.
