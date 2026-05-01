# Tests

Pytest suite for the `mcbrain-ops` query engine, organized following Kent C. Dodds' [Testing Trophy](https://kentcdodds.com/blog/the-testing-trophy-and-testing-classifications):

| File | Tier | What it covers |
|---|---|---|
| `test_static.py` | Static / smoke | Module imports, `--help` lists every subcommand, regression-grep for the no-unbounded-text-SELECT contract |
| `test_unit.py` | Unit | Pure-Python logic: RRF math, excerpt rendering, hashing, cross-platform paths, blob round-trip |
| `test_patcher.py` | Unit | CLAUDE.md text-manipulation helpers (this is the most fragile region — covered exhaustively) |
| `test_index.py` | Integration | Real SQLite DB + real FastEmbed embedder: init, rebuild, sync (add/change/delete), status, model-compatibility check |
| `test_search.py` | Integration | Lexical cascade (rg / grep / Python fallback), semantic recall on paraphrased queries, hybrid query, lazy-text-fetch regression |
| `test_e2e.py` | E2E | Full lifecycle through the script's CLI: status → query × 3 → sync → query → uninstall |

The bulk of the tests are in the integration tier — that's where the trophy says investment pays best.

## Running

The tests need fastembed, numpy, and pytest installed in the same Python that runs them. Easiest setup is a dedicated test venv:

```sh
# From the repo root
python3 -m venv .test-venv
.test-venv/bin/pip install \
  -r plugins/mcbrain/skills/mcbrain-ops/references/requirements.txt \
  -r tests/requirements.txt

.test-venv/bin/python -m pytest tests/ -v
```

First run downloads the FastEmbed model (~30 MB) into `~/.cache/fastembed/`. Subsequent runs reuse it.

## Speed

The model load is the dominant cost. The `shared_embedder` fixture is session-scoped, so the model is loaded exactly once across all integration and e2e tests. A clean run is ~10–20 seconds on a laptop after the model is cached.

Unit tests (`test_static.py`, `test_unit.py`, `test_patcher.py`) don't touch fastembed at all and complete in under a second.

To run only the fast tier:

```sh
.test-venv/bin/python -m pytest tests/test_static.py tests/test_unit.py tests/test_patcher.py -v
```

## Fixtures

- `tests/fixtures/wiki/*.md` — four sample wiki pages (myocardial-infarction, scaling-laws, sourdough, photosynthesis) chosen so semantic queries with zero lexical overlap have an unambiguous expected top-1
- `tests/fixtures/claude_md_legacy.md` — a CLAUDE.md without `## Query engine`, used to test the migration patcher
- `tests/fixtures/claude_md_h4_after_query.md` — the H4-after-Query edge case that exposed an early bug in the section-end finder

## What's deliberately NOT tested

- **Cross-platform Windows behavior end-to-end.** The unit tests cover the path-construction logic via mocking, but a true Windows run requires Windows. Consider a CI matrix if/when McBrain has Windows users.
- **The full `migrate` subcommand including `python -m venv` + `pip install`.** That path requires network and ~30 seconds; the integration tests skip the bootstrap and test the post-bootstrap behavior directly. The static tests verify the migrate help works.
- **Claude Desktop integration.** The skill is invoked by Claude through Bash; that side of the contract isn't covered here and is verified by manual smoke against a real vault.
