<img src="img/mcbrain.png" width="300" height="250">

A Claude plugin for building and operating **McBrain** — a persistent, LLM-maintained personal knowledge base based on [Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — plus companion skills for running research workflows. Pick your tracker backend: a **local JSONL file** inside the vault (zero dependencies, works offline) or a **Notion database** (visual taskboard). The plugin bundles seven cooperating skills so you can install everything in one click from Claude Desktop.

The idea in one sentence: instead of re-deriving knowledge from raw sources every session, Claude builds and maintains a persistent markdown wiki that compounds over time. Obsidian is the IDE; Claude is the programmer; McBrain is the codebase.

## Requirements
- [Claude Cowork](https://support.claude.com/en/articles/13345190-get-started-with-claude-cowork) with an active Claude license
- [Obsidian](https://obsidian.md/)

## Recommended
- A [GitHub](https://github.com/) account. Useful for backing up your McBrain.
- [Claude for Chrome extension](https://chromewebstore.google.com/publisher/anthropic/u308d63ea0533efcf7ba778ad42da7390) Very helpful for accessing data from websites that require you to login.
- [Obsidian Web Clipper](https://obsidian.md/clipper) Very helpful for when you're browsing the web and find something you want to save to McBrain
- [Notion](https://www.notion.com/) *(optional)* — gives you a visual taskboard for research backlogs. The local JSONL backend is the default and needs no external service; Notion is just a nicer UI if you already use it.

## Setup

This repo is itself a Claude plugin marketplace. Install it from Claude Desktop:

1. Open **Settings → Extensions → Directory → Plugins**.
2. Click the **+** next to your personal marketplaces and add this repo (e.g. `https://github.com/jbdamask/McBrain` or a local path).
3. Install the **mcbrain** plugin from the marketplace. All four skills are activated together.
4. Open a new Claude Cowork session and ask it to create a new McBrain for your topic of interest.

## Building Your Knowledgebase
I recommend you make many McBrains for whatever topics you want to build a knowledgebase around. This is what I do. Give them names like mcbrain-ai-research or mcbrain-house-stuff. Claude will be able to figure out which one to use based on your context.

The fastest way to get going is to open Claude Cowork, and ask it create a new McBrain. It will walk you through the configuration, doing most of it for you. During setup it will ask which **research tracker backend** you want — `local` (a JSONL file in the vault, recommended), `notion` (a Notion database), or `none` (skip for now). See *[Research tracker backends](#research-tracker-backends)* below for what each one does.

Once your McBrain is ready, you can tell Claude to add research items to your tracker. Then tell Claude to "run the research queue" — depending on which backend you picked, that runs either `local-research-runner` or `notion-research-runner`. The runner: a) claims up to five "To do" records and marks them "In progress"; b) fires up one subagent for each research task in parallel; c) does all the research, then writes the findings back to the vault.

Once your research is done, the wiki ingest is automatic for the local backend (findings land directly in `raw/notes/` where the standard ingest picks them up) — for Notion, tell Claude to copy the research notes into McBrain. Either way, before you know it, you'll have a pretty big knowledge graph.

![Knowledge Graph](./img/llm-wiki.png)


## Using it
Open a Claude Cowork session and start asking it about your McBrain of interest. "What did we learn?", "What are we missing?", "How does X relate to Y?". 
Each time you have a conversation with Claude about the contents of your McBrain, you think of new things and can have Claude add them. Over time, the not only does the knowlegebase grow but your ability (and Claude's) to understand it evolves.

![Use](./img/mcbrain-ai-science.png)
![Make Notion DB](./img/make-notion-db.png)
![Ingest](./img/ingest.png)
![Notion](./img/notion-research-db.png)

## Query engine

Each McBrain vault ships with a built-in hybrid lexical + semantic query engine, provisioned automatically by `mcbrain-setup` (Step 8.5) and maintained by the `mcbrain-ops` skill. There's nothing to configure — `mcbrain-setup` creates a per-vault Python venv at `<vault>/.mcbrain/venv/`, installs the indexer dependencies, builds the initial search index, and patches your vault's `CLAUDE.md` to route queries through it.

- **Lexical** uses ripgrep (`rg`) when available, with a `grep` / pure-Python fallback so the lexical path always works.
- **Semantic** uses [FastEmbed](https://github.com/qdrant/fastembed)'s CPU-only ONNX embedding model (`BAAI/bge-small-en-v1.5`, 384 dims, ~30 MB). Queries like "cardiac event" still surface a page about *myocardial infarction* even though those words don't overlap.
- **Hybrid ranking** uses Reciprocal Rank Fusion (RRF) over the two modalities.

The embedding model lives in FastEmbed's shared cache (`~/.cache/fastembed/` on macOS/Linux, `%LOCALAPPDATA%\fastembed\` on Windows) so adding a second McBrain vault doesn't re-download it. Per-vault state — venv, index — lives at `<vault>/.mcbrain/` and is rebuildable from the wiki content. **No system-wide installs**: McBrain only detects whether `python3` and `rg` are available and surfaces install instructions if they aren't; everything else stays inside `<vault>/.mcbrain/`.

Existing vaults that pre-date the engine get an automatic migration prompt the next time the `mcbrain` skill is invoked against them.

### Fixing `mcbrain-engine` on macOS

If Cowork's `mcbrain-engine` is failing with `Python 3.10+ required` or `ensurepip ... exit status 1`, run this:

```bash
# Install pyenv and Python 3.13
brew install pyenv
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.zshrc
echo '[[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.zshrc
echo 'eval "$(pyenv init - zsh)"' >> ~/.zshrc
source ~/.zshrc
pyenv install 3.13.13
pyenv global 3.13.13

# Make Cowork find it (it doesn't read your .zshrc)
ln -sf "$(pyenv which python3.13)" /opt/homebrew/bin/python3

# Clear mcbrain's broken venv and restart Cowork
rm -rf "$HOME/Library/Application Support/mcbrain-engine/venv"
```

Quit Cowork (Cmd+Q) and relaunch. First start takes ~30 seconds to build the venv — if it errors, restart once more.

**Intel Macs:** swap `/opt/homebrew/bin` for `/usr/local/bin`.

**Why this works:** macOS's system Python is 3.9 (too old, can't replace). Homebrew's 3.13/3.14 bottles currently have a `libexpat` bug that breaks venv creation. pyenv builds Python from source, dodging the bug. The symlink is needed because GUI apps don't inherit your shell `PATH`.

## Research tracker backends

Each McBrain vault can be paired with **one** research tracker. `mcbrain-setup` Step 8 asks you to pick a backend; the choice is recorded in the vault's `CLAUDE.md` under a `## Research tracker` section, and downstream skills route on it. The two backends are mutually exclusive per vault.

### `local` — recommended (default)

Research tasks live as JSONL rows in a single file inside the vault: `<vault>/raw/research_tasks/tasks.jsonl`. One topic per row (with a `topic_slug` field), all topics in one file. The `local-research-runner` skill drains "To do" rows, runs research subagents in parallel, writes one markdown file per completed task to `<vault>/raw/notes/research-<topic-slug>-<task-id>.md`, and flips the row to "Done" — and that's it. The standard ingest flow then picks the new files up alongside Web Clipper / hand-drop notes; no special bridged-ingest mode.

- **Zero external dependencies.** No Notion connector, no integration token, no MCP-engine call. Just files in your vault.
- **Works offline.** Travel-friendly.
- **Atomic writes.** Concurrent writers (two terminals, future parallel-agent architectures) are serialized by an exclusive-create lock + `os.replace` rewrite — Python stdlib only, identical behavior on macOS, Linux, and Windows.
- **Hand-editable.** It's a JSONL file. Open it in your editor, append rows manually, change priorities, whatever.

To add a topic to an existing local tracker, run the **`local-research-db`** skill (or just ask Claude). To drain the queue, run **`local-research-runner`** ("run my local research queue", "drain the local tracker").

### `notion` — for visual taskboards

Research tasks live in a Notion database with the standard schema (Task, Status, Priority, dates, Notes). The `notion-research-runner` skill drains the database and writes findings back to each task's Notion page; the Notion-bridged ingest mode then copies those pages into `raw/notes/` via the engine's server-side `ingest_from_notion` tool (page bodies stream straight to disk, never through chat context).

Needs a Notion MCP connector (Anthropic's claude.ai Notion connector, Notion's official `@notionhq/notion-mcp-server`, or any equivalent) **and** admin rights to create a Notion integration token (one-time, ~90 seconds during setup). If you don't have those, pick `local`.

To add a database, run **`notion-research-db`**. To drain the queue, run **`notion-research-runner`**.

### Switching backends

A vault has one backend at a time, set at vault-creation time. Switching from one backend to the other after the fact is **not** an automatic flow — the `## Research tracker` section in CLAUDE.md needs to be edited, and the new backend's tracker has to be initialized. If you need to switch, ask Claude to walk you through it: it'll ask whether you want to migrate existing tasks or start fresh.

Vaults that pre-date this option keep working — McBrain falls back to the legacy `## Notion companion databases` registration as `Backend: notion` if no `## Research tracker` section is present.

## Skills bundled in the plugin

The `mcbrain` plugin contains seven skills, all under [`plugins/mcbrain/skills/`](./plugins/mcbrain/skills):

### [`mcbrain-setup`](./plugins/mcbrain/skills/mcbrain-setup)
One-shot setup skill that bootstraps McBrain end-to-end: names the vault, configures a backup strategy, scaffolds the directory structure, writes the filesystem MCP config block for Claude Desktop, **provisions the per-vault query engine**, walks through Obsidian and browser-extension setup, and verifies the install. Run this from Claude Cowork each time you want to make a new McBrain.

### [`mcbrain`](./plugins/mcbrain/skills/mcbrain)
Day-to-day operating skill for McBrain. Handles ingesting sources into the vault, querying the wiki, filing synthesis pages, and linting. Supports multiple vaults (e.g. `mcbrain-finance`, `mcbrain-ai-science`) by mapping the user's request to the matching MCP filesystem server. Triggered by phrases like "ingest this", "save to mcbrain", "ask my brain", or any reference to the user's wiki / second brain.

### [`mcbrain-ops`](./plugins/mcbrain/skills/mcbrain-ops)
The query engine itself: a hybrid lexical + semantic search index over each vault's `wiki/`. Provisioned automatically by `mcbrain-setup` and called automatically by `mcbrain` after every wiki edit (to keep the index current) and at query time. You normally never invoke this directly — but it's the right skill if you ever need to rebuild the index, check its status, migrate an older vault, or remove the engine.

### [`local-research-db`](./plugins/mcbrain/skills/local-research-db)
Initializes a local research-task tracker for a McBrain vault — the JSONL file at `<vault>/raw/research_tasks/tasks.jsonl` and the matching `## Research tracker` registration in CLAUDE.md. Use this when you want a research backlog that lives entirely inside the vault, no external services. Run it once per topic to register additional topics on an existing local tracker.

### [`local-research-runner`](./plugins/mcbrain/skills/local-research-runner)
Drains the "To do" queue of a local JSONL research tracker. Atomically claims up to 5 rows (cross-platform exclusive-create lock + `os.replace`), spawns a planning-then-executing research subagent for each in parallel, writes findings as markdown files into the vault's `raw/notes/` directory with `source: local-research-tracker` frontmatter, and flips the rows to "Done". The standard ingest flow then picks the new files up — no special bridged-ingest mode.

### [`notion-research-db`](./plugins/mcbrain/skills/notion-research-db)
The Notion-backend twin of `local-research-db`. Creates a Notion database scoped to a research topic, with a fixed schema for tracking tasks (Task name, Status, Priority, Created date, Last updated date, Notes). After creation, registers the database name and URL back into the associated McBrain vault so the wiki knows where its companion tracker lives. Works with any Notion MCP connector — matches tools by capability rather than exact name.

### [`notion-research-runner`](./plugins/mcbrain/skills/notion-research-runner)
The Notion-backend twin of `local-research-runner`. Drains the "To do" queue of a Notion research tracker (the kind produced by `notion-research-db`). Pulls up to 5 tasks, flips them to "In progress", spawns a planning-then-executing research subagent for each, and writes the findings plus sources back to each task's Notion page.

## Typical workflow

1. Run **`mcbrain-setup`** once to scaffold a vault and wire up Claude Desktop + Obsidian. Pick `local` (recommended) or `notion` as your research-tracker backend during Step 8.
2. Use **`mcbrain`** as you read, browse, and think — to ingest sources, query the vault, and file syntheses.
3. Add research tasks to your tracker (drag rows into `tasks.jsonl` or have Claude write them; for Notion, use the Notion UI). When you're ready, ask Claude to "run the research queue" — it'll pick the right runner (`local-research-runner` or `notion-research-runner`) based on your CLAUDE.md backend setting. Findings flow back into the wiki.

## Repo layout

```
McBrain/
├── .claude-plugin/
│   └── marketplace.json          # this repo is a marketplace
├── plugins/
│   └── mcbrain/
│       ├── .claude-plugin/
│       │   └── plugin.json       # the plugin manifest
│       └── skills/
│           ├── mcbrain-setup/
│           ├── mcbrain/
│           ├── mcbrain-ops/
│           ├── local-research-db/
│           ├── local-research-runner/
│           ├── notion-research-db/
│           └── notion-research-runner/
├── README.md
└── LICENSE
```

## Running tests

The query engine has a pytest suite under [`tests/`](./tests/) covering pure-function unit tests, the CLAUDE.md patcher, the index lifecycle (rebuild / sync / status), search behavior (lexical cascade, semantic recall, hybrid query, lazy text fetch), and a full end-to-end lifecycle through the script's CLI. Organized following Kent C. Dodds' [Testing Trophy](https://kentcdodds.com/blog/the-testing-trophy-and-testing-classifications): the bulk of the investment lives in integration tests with a real SQLite DB and a real FastEmbed embedder.

```sh
python3 -m venv .test-venv
.test-venv/bin/pip install \
  -r plugins/mcbrain/skills/mcbrain-ops/references/requirements.txt \
  -r tests/requirements.txt

.test-venv/bin/python -m pytest tests/ -v
```

First run downloads the FastEmbed model (~30 MB) into `~/.cache/fastembed/`. Subsequent runs reuse it and complete the full suite in a few seconds. See [`tests/README.md`](./tests/README.md) for layout and design notes.

## License

See [LICENSE](./LICENSE).
