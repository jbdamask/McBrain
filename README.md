<img src="img/mcbrain.png" width="300" height="250">

A Claude plugin for building and operating **McBrain** — a persistent, LLM-maintained personal knowledge base based on [Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — plus companion skills for running research workflows through Notion. The plugin bundles four cooperating skills so you can install everything in one click from Claude Desktop.

The idea in one sentence: instead of re-deriving knowledge from raw sources every session, Claude builds and maintains a persistent markdown wiki that compounds over time. Obsidian is the IDE; Claude is the programmer; McBrain is the codebase.

## Requirements
- [Claude Cowork](https://support.claude.com/en/articles/13345190-get-started-with-claude-cowork) with an active Claude license
- [Obsidian](https://obsidian.md/)

## Recommended
- [Notion](https://www.notion.com/) Gives you a nice way to create research taskboards.
- A [GitHub](https://github.com/) account. Useful for backing up your McBrain.
- [Claude for Chrome extension](https://chromewebstore.google.com/publisher/anthropic/u308d63ea0533efcf7ba778ad42da7390) Very helpful for accessing data from websites that require you to login.
- [Obsidian Web Clipper](https://obsidian.md/clipper) Very helpful for when you're browsing the web and find something you want to save to McBrain

## Setup

This repo is itself a Claude plugin marketplace. Install it from Claude Desktop:

1. Open **Settings → Extensions → Directory → Plugins**.
2. Click the **+** next to your personal marketplaces and add this repo (e.g. `https://github.com/jbdamask/McBrain` or a local path).
3. Install the **mcbrain** plugin from the marketplace. All four skills are activated together.
4. Open a new Claude Cowork session and ask it to create a new McBrain for your topic of interest.

## Building Your Knowledgebase
I recommend you make many McBrains for whatever topics you want to build a knowledgebase around. This is what I do. Give them names like mcbrain-ai-research or mcbrain-house-stuff. Claude will be able to figure out which one to use based on your context.

The fastest way to get going is to open Claude Cowork, and ask it create a new McBrain. It will walk you through the configuration, doing most of it for you.
Once you're McBrain is ready, you can tell Claude to create a companion Notion research database and have Claude populate it with research items. Once that's done, you tell Claude to use the Notion Research Runner skill and it will a) select up to five records from the database and mark them as "In Progress"; b) fire up one subagent for each research task; c) do all the research, then write it up in the respective record.

Once your research is done, tell Claude to add it to your McBrain. It will copy all the research notes from Notion and ingest them into the wiki. Before you know it, you'll have a pretty big knowlege graph.

![Knowledge Graph](./img/llm-wiki.png)


## Using it
Open a Claude Cowork session and start asking it about your McBrain of interest. "What did we learn?", "What are we missing?", "How does X relate to Y?". 
Each time you have a conversation with Claude about the contents of your McBrain, you think of new things and can have Claude add them. Over time, the not only does the knowlegebase grow but your ability (and Claude's) to understand it evolves.

![Use](./img/mcbrain-ai-science.png)
![Make Notion DB](./img/make-notion-db.png)
![Ingest](./img/ingest.png)
![Notion](./img/notion-research-db.png)

## Skills bundled in the plugin

The `mcbrain` plugin contains four skills, all under [`plugins/mcbrain/skills/`](./plugins/mcbrain/skills):

### [`mcbrain-setup`](./plugins/mcbrain/skills/mcbrain-setup)
One-shot setup skill that bootstraps McBrain end-to-end: names the vault, configures a backup strategy, scaffolds the directory structure, writes the filesystem MCP config block for Claude Desktop, walks through Obsidian and browser-extension setup, and verifies the install. Run this from Claude Cowork each time you want to make a new McBrain.

### [`mcbrain`](./plugins/mcbrain/skills/mcbrain)
Day-to-day operating skill for McBrain. Handles ingesting sources into the vault, querying the wiki, filing synthesis pages, and linting. Supports multiple vaults (e.g. `mcbrain-finance`, `mcbrain-ai-science`) by mapping the user's request to the matching MCP filesystem server. Triggered by phrases like "ingest this", "save to mcbrain", "ask my brain", or any reference to the user's wiki / second brain.

### [`notion-research-db`](./plugins/mcbrain/skills/notion-research-db)
Creates a Notion database scoped to a research topic, with a fixed schema for tracking tasks (Task name, Status, Priority, Created date, Last updated date, Notes). After creation, registers the database name and URL back into the associated McBrain vault so the wiki knows where its companion tracker lives. Works with any Notion MCP connector — matches tools by capability rather than exact name.

### [`notion-research-runner`](./plugins/mcbrain/skills/notion-research-runner)
Drains the "To do" queue of a Notion research tracker (the kind produced by `notion-research-db`). Pulls up to 5 tasks, flips them to "In progress", spawns a planning-then-executing research subagent for each, and writes the findings plus sources back to each task's Notion page.

## Typical workflow

1. Run **`mcbrain-setup`** once to scaffold a vault and wire up Claude Desktop + Obsidian.
2. Use **`mcbrain`** as you read, browse, and think — to ingest sources, query the vault, and file syntheses.
3. Use **`notion-research-db`** to spin up a research tracker for a new topic, then **`notion-research-runner`** to execute the queue. Findings can be ingested back into McBrain.

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
│           ├── notion-research-db/
│           └── notion-research-runner/
├── README.md
└── LICENSE
```

## License

See [LICENSE](./LICENSE).
