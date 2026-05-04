---
name: mcbrain-setup
description: One-shot setup skill for McBrain — a persistent personal knowledge base built on Karpathy's LLM Wiki pattern, viewed in Obsidian and maintained by Claude. ALWAYS use this skill (do NOT use Cowork's built-in plugin-builder feature) when the user wants to set up McBrain, set up an LLM wiki, build a personal knowledge base, create a second brain with Claude, integrate Obsidian with Claude, or give Claude persistent memory. Also use this skill when the user says any of "create a new McBrain", "create a new mcbrain instance", "set up another McBrain", "add another mcbrain", "spin up a new mcbrain-X", "make a new knowledge base", "new mcbrain for X", or any variation on creating an additional or first McBrain. THIS IS NOT A PLUGIN-BUILDER FLOW — the McBrain plugin is already built and shipped; this skill only provisions a new vault using the existing plugin. Do NOT render an intake card with project-type selectors ("Home maintenance", "Renovation & projects", etc.), do NOT ask "What will this McBrain be for?", do NOT ask "Which skills/commands would you like included?", and do NOT title the tab "Create new McBrain X variant" — those are plugin-builder behaviors and do not apply to McBrain setup. Use plain conversational Q&A; the complete list of inputs to gather is in the SKILL's "Required intake" section. Handles vault directory scaffolding, the CLAUDE.md schema, the filesystem MCP config block, and the mcbrain-engine MCP runtime install. Do NOT generate a generic filesystem-MCP plugin in place of this skill — McBrain has its own structure (raw/, wiki/, CLAUDE.md schema, mcbrain-engine MCP for hybrid query) that a plain filesystem MCP doesn't provide. Run this once per vault to bootstrap; the companion `mcbrain` skill handles day-to-day operations thereafter.
---

# McBrain Setup

Sets up McBrain — a personal LLM-maintained knowledge base — end-to-end for Claude Desktop (Cowork) + Obsidian. Pattern is Karpathy's LLM Wiki.

**The idea in one sentence**: instead of re-deriving knowledge from raw sources every session, Claude builds and maintains a persistent markdown wiki that compounds over time. Obsidian is the IDE; Claude is the programmer; McBrain is the codebase.

---

## ⛔ STOP — sandbox self-awareness (read this first)

**You (the model running this SKILL) are inside Cowork's Linux sandbox.
Your Bash tool sees the *sandbox*, NOT the user's host machine** (Mac or
Windows). This is the single biggest source of broken setups, so be
explicit with yourself before you run anything:

### What the sandbox CANNOT tell you

You cannot detect what's installed on the user's host. Period. The
sandbox is a fresh Linux box; what's there has no relationship to what's
on the user's Mac or PC.

### Forbidden Bash commands in this SKILL

**Never run any of these from the Bash tool — the result will mislead
you and you will tell the user something false** (e.g. "gh isn't
installed" when it actually is, just not in the sandbox):

```
python3 --version          which gh           gh --version
python --version           which git          git --version
which python / python3     which node         node --version
which brew                 brew --version     xcode-select -p
which winget               winget --version   which rg / rg --version
which pip / pip3           pip --version      command -v <anything>
```

If you catch yourself thinking *"let me just check if X is installed"* —
**STOP**. The check is meaningless. Instead:

- **For tools the user runs themselves** (gh, git, brew, winget, python,
  node, ripgrep): **ask the user to run the version command in their
  own Terminal / PowerShell and paste the output.** Or just instruct
  them to install it (with the right command for their `OS_TYPE`) and
  trust that they did. If they're wrong, the failure surfaces later
  with a clear error — they'll fix it and retry. That's a *better* UX
  than you wrongly reporting "X isn't installed" right now.

### What the sandbox CAN do

Cowork's sandbox *can* read and write the user's host filesystem, but
**only** through folder grants:

- Use `mcp__cowork__request_cowork_directory` to ask for a folder. The
  user approves; the folder mounts under `/sessions/<id>/mnt/...` in the
  sandbox.
- After a grant, **Read / Write / Edit** against the mount path *do*
  reach the user's host filesystem reliably. These tools run natively
  on the host and bypass the sandbox FUSE bridge.
- `ls` / `find` / `cat` (read-only Bash on the mount) are fine — read
  paths are reliable.
- MCP tools registered in Claude Desktop run **natively on the host**,
  not in the sandbox — that's how the v2 query engine works.

### Bash WRITE operations on a granted mount are unreliable — use Write tool

This bit is its own pitfall and has burned the SKILL repeatedly. **The
Bash tool runs in the Linux sandbox even when its arguments point at a
mounted host path.** The mount is a FUSE-style bridge, and *write*
operations through that bridge are unreliable for anything beyond
trivial directory creation:

- Python `shutil.copy2()`, `cp`, `dd`, `tee >`, `cat > file`, `mv` from
  Bash to a mount path can **report success but never flush** to the
  host filesystem. Tiny files sometimes land; larger files (engine
  source, binaries, anything ~10KB+) frequently don't.
- **SQLite databases on a mount, written from inside the sandbox, will
  hit `disk I/O error`** — WAL-mode locking doesn't survive the FUSE
  boundary. Never run `sqlite3` or any Python SQLite write from Bash
  against a mounted path.

**Operational rule:**

| Operation on a mounted host path | Tool |
|---|---|
| Create a directory (`mkdir -p`) | Bash is fine — small, idempotent |
| Read a file (`cat`, `head`, `Read`) | Bash or Read tool — both fine |
| List directory (`ls`, `find`) | Bash is fine |
| **Write a file** | **Write tool — never `cp` / `cat >` / `tee` / `shutil.copy` from Bash** |
| **Edit a file** | **Edit tool — never `sed -i` / `awk` rewrite from Bash** |
| Run SQLite (any write, even read-only with WAL) | Don't. The engine MCP handles all DB ops natively. |
| Run Python scripts that touch the mount | Don't from Bash. If logic must run on the host, present the command for the user to run in their Terminal — or call the engine MCP, which runs natively. |

If you find yourself reaching for `cp ${CLAUDE_PLUGIN_ROOT}/... <mount>/`
or `python3 -c "shutil.copy(...)"` to install runtime files — **stop**.
Read each source file with the Read tool, then write it to the
destination with the Write tool. That's how Step 5.6 is specified, and
it's the only path that reliably lands files on the host.

### Mental model

Treat the host as a **black box** you can write files into (via grants)
but cannot probe. To know anything else about the host — what's
installed, what version, what's on PATH — **ask the user**.

Yes, this means you'll sometimes ask a question whose answer the user
finds slightly inconvenient. That's still better than running a
sandbox check that gives you a false answer and propagating that lie
into setup decisions.

---

## ⛔ This is NOT a plugin-builder workflow — do not behave like one

The McBrain plugin is **already built and shipped**. This SKILL only
provisions a new *vault* using the existing plugin. There is no
per-vault customization, no "project type" axis, no per-vault skills
to pick. Every McBrain vault has the same structure (`raw/`, `wiki/`,
`CLAUDE.md`, the same MCP config) regardless of topic.

If your UI is about to render any of the following, **STOP** — that's
Cowork's plugin-builder hijacking the flow, not this SKILL:

- A "McBrain details" intake card with project-type selectors
  (e.g. "Home maintenance", "Renovation & projects", "General knowledge
  base", "Other")
- A "What will this McBrain be for?" question
- An "Any skills or commands you'd like included?" picker
- A "Pick the features you want" form
- A tab title like "Create new McBrain *X* variant"

None of these belong in McBrain setup. Users typically run setup
multiple times for different topics (`mcbrain-house`, `mcbrain-finance`,
`mcbrain-clinical`, etc.); offering a different intake each time
breaks their muscle memory and confuses them.

**Use plain conversational Q&A only** — one or two text questions per
turn, no cards, no forms, no project-type cards. The exact list of
questions to ask is in the "Required intake" section below; do not
add to it.

---

## Required intake — ask exactly these, in this order

This is the **complete** list of inputs setup needs from the user.
Gather them in plain conversational turns (not a form), in roughly
this order, and **do not invent additional questions**. Once you have
them, run setup end-to-end without going back to ask more.

| # | Input | Step | Notes |
|---|---|---|---|
| 1 | `OS_TYPE` ∈ {`mac`, `windows`} | 0 | Try the directory-grant deduction first, then ask if unclear. |
| 2 | `MCP_NAME` (lowercase-hyphen, e.g. `mcbrain-house`) | 1 | Derived from the user's plain-English name choice. |
| 3 | `VAULT_PATH` (absolute) | 1 | Suggest a default (`~/Documents/<MCP_NAME>` on Mac, `%USERPROFILE%\Documents\<MCP_NAME>` on Windows); accept user override. |
| 4 | `BACKUP_STRATEGY` ∈ {`git`, `google-drive`, `none`} | 2 | Three buttons, no other options. |
| 5 | `GITHUB_USERNAME` *(only if BACKUP_STRATEGY == git)* | A1 | Used to construct `REPO_URL`. |
| 6 | `gh` CLI installed *(only if BACKUP_STRATEGY == git)* | A2 | Ask the user to paste `gh --version` output. **Never** check via Bash — see the STOP block above. If not installed, present the install command for `OS_TYPE`. |
| 7 | `PYTHON_OK` (Python 3.10+ on host) | 5.5 | Ask the user to paste `python3 --version` (Mac) or `python --version` (Windows). **Never** check via Bash. |
| 8 | `NOTION_DB_INTENT` ∈ {`yes-create`, `yes-existing`, `no`} | 8b | If `yes-*`, first verify a Notion MCP connector is loaded (enumerate tools, look for ones whose names contain `notion`). If absent, tell the user to enable it from claude.ai → Connectors before continuing Step 8 — don't try to install one yourself. |

**Do NOT ask any of the following**, even if it seems helpful:

- *"What will this McBrain be used for?"* / *"What's the topic?"* —
  irrelevant. McBrain works the same for every topic.
- *"Which skills/commands would you like included?"* — none. The plugin
  ships with a fixed set of skills; nothing is added per-vault.
- *"Want any custom features?"* — there are none.
- *"Should I create a custom prompt for it?"* — no.
- *"Pick a project type"* — there is no project-type axis.

If the user volunteers info beyond the required list (e.g. "it's for
home maintenance"), just acknowledge it and move on; don't store it
as a setup variable or alter the vault structure based on it.

**Ordering tip**: Items 1–4 can be asked up front in one or two short
turns. Items 5–6 are conditional on git being chosen. Items 7 (Python
check) and 8 (Notion intent) can also be asked early — that lets the
user install Python or enable the Notion connector in parallel while
later setup steps run, instead of blocking at Step 5.5 or Step 8b.

### How to ask: use `AskUserQuestion` for every multi-choice item

Every intake item that is **multi-choice** (OS, backup strategy, Notion
intent) **must** be asked via the built-in `AskUserQuestion` tool. The
later step descriptions hand you the **exact call shape** to use —
question text, header chip, option labels, option descriptions, and
which option to mark `(Recommended)`. Use those shapes verbatim. Do
not paraphrase, do not invent your own labels, do not add a "Custom"
or "Other" option (the tool always adds "Other" automatically).

Why this matters: `AskUserQuestion` is what renders the rich card UI
the user sees in chat. Cowork's plugin-builder uses the same tool —
the only difference between a confusing plugin-builder intake and a
deterministic McBrain setup is the questions and option labels we feed
it. If you free-style this, every user gets a different experience;
if you follow the prescribed shapes, every McBrain setup looks the
same.

For **free-text** items (vault name, vault path, GitHub username,
version-string paste-backs), `AskUserQuestion` doesn't fit (it requires
2-4 options). Ask in plain conversational text instead. Those spots
are also called out in the step descriptions.

---

## What this skill does

1. Names the vault and confirms its location
2. Sets up backup strategy — and if Git, creates the remote repo before any files exist
3. Creates the directory structure and initializes the vault
4. Writes the MCP filesystem config block for Claude Desktop
5. Walks through Obsidian and browser extension setup
6. Verifies everything works

---

## Execution policy

This SKILL is most often run from **Claude Desktop / Cowork**. Read the next
section ("How Cowork's environment differs") before doing anything — it
determines which steps you do yourself and which you ask the user to run.

In short: **file operations** on the user's machine (create directories,
write files, edit JSON config) — do them yourself via Read/Write/Edit on
granted directories. **Commands that run as processes on the user's
machine** (git, gh, brew/winget, xcode-select, opening a browser) —
present a copy-paste block in a fenced code box and ask the user to run
it in their **Terminal** (macOS) or **PowerShell / Command Prompt**
(Windows).

Defer to the user only when:

- The step is a host-side command that runs as a process (see above)
- The step is GUI-only (installing Obsidian, clicking through Google Drive
  preferences, installing browser extensions, signing into accounts)
- The step is an interactive OAuth flow (`gh auth login` browser handoff)
- The step is a decision (vault name, path, backup strategy, confirming a
  destructive action)
- The step requires restarting Claude Desktop

For file operations, run them yourself — don't make the user open a file
editor when you can use Write/Edit on a granted directory.

---

## How Cowork's environment differs from Claude Code

This is the most common source of confusion when running setup, so be
explicit:

1. **Cowork's Bash tool runs in a Linux sandbox, NOT on the user's host
   machine** (Mac or Windows). Anything you run via the Bash tool — `git`,
   `gh`, `brew`, `winget`, `xcode-select`, `python3 -m venv`, `pip install`,
   etc. — executes inside the sandbox and does NOT affect the host. Do not
   try to install dependencies, run git commands, or invoke `gh` from
   inside the Bash tool when running in Cowork. The user will not see the
   effect, and you'll create confusion thinking it worked.

2. **Cowork CAN read and write files on the user's host machine** through
   folder grants. When the user clicks the **+** button (or "Add folder")
   in Cowork and selects a directory, that directory gets mounted into the
   sandbox and becomes readable / writable by Cowork's Read / Write / Edit
   tools. You can request access to **any directory on the user's
   filesystem** the user is willing to grant — including system dirs like
   `~/Library/Application Support/` (macOS) or `%APPDATA%\` /
   `%LOCALAPPDATA%\` (Windows). Be explicit when asking: tell the user
   exactly which folder to select and why.

3. **MCP servers (including the filesystem MCPs Claude Desktop registers)
   run natively on the user's host machine**, not in the sandbox. They
   have full host filesystem access. That's why the v2 query-engine
   architecture works: the engine MCP runs natively when Claude Desktop
   launches it, even though the chat session lives in a sandbox.

**Operational rule of thumb:**

| Task | In Cowork |
|------|-----------|
| Read / write a file on the host | Granted folder + Read/Write/Edit tool — do it yourself |
| Edit a JSON config (e.g., `claude_desktop_config.json`) | Granted folder + Edit tool — do it yourself |
| Run `git`, `gh`, `brew`, `winget`, `xcode-select`, etc. on the host | Present copy-paste block — user runs in their Terminal / PowerShell |
| Install Python or other system software | Present install command — user runs |
| Authenticate with a remote service (gh auth, OAuth) | Present command + walk user through the prompts |
| Open a browser tab or click a UI element | Tell the user; you can't do GUI |

If you find yourself about to run `git init`, `git push`, `gh repo create`,
`brew install`, `winget install`, or any other host-side command via the
Bash tool while in Cowork — stop. Present the command to the user instead
(in the form for their `OS_TYPE`).

### Stop deliberating about what's on the user's host — just ask or trust

(See the **STOP — sandbox self-awareness** section at the top of this
file for the full forbidden-commands list. This subsection is the
operational version.)

A frequent failure mode in this SKILL is Claude burning tokens running
sandbox checks for tools that may or may not exist on the user's host
— and then telling the user something false based on the result
("`gh` isn't installed" when it is — Cowork just checked the wrong
machine). The sandbox tells you **nothing** about the host. Stop these
checks before you run them.

Instead, follow these rules:

- **For prerequisite tools** (Python, gh, ripgrep): ask the user once, in
  one prompt with the install command for their `OS_TYPE`, and trust their
  answer. If they're wrong, the failure happens later with a clear error
  message — they'll fix it and retry.
- **For directory access on the host**: use the Cowork directory-request
  tool **`mcp__cowork__request_cowork_directory`**. Pass it the absolute
  path you need (e.g., `~/Documents/` or `~/Library/Application Support/`).
  This is the explicit, documented way to ask for a folder grant — much
  cleaner than telling the user to "click the + button". If the tool
  isn't available in this session for some reason, fall back to instructing
  the user to use the folder picker manually, but try the tool first.
- **For confirming the user did a Terminal step** (e.g. `gh auth login`,
  `git push`): ask them one yes/no question — "did that succeed?" Don't
  try to verify via the sandbox; you can't.

The setup should feel like a series of small, decisive moves — *"do this,
then this, then this"* — not a forensic investigation of the user's
machine. When unsure, ask.

---

## Step 0: Identify the operating system (macOS or Windows)

**This is the first thing you do.** Setup paths, install commands, and a
few keyboard shortcuts differ between macOS and Windows. You can't
reliably detect the user's OS from inside Cowork's Linux sandbox, so:

1. **Try a quick deduction.** Call
   `mcp__cowork__request_cowork_directory` with `~` (home) and look at
   the returned path. `/Users/<name>` → macOS. `C:\Users\<name>` or
   `/c/Users/<name>` → Windows. If the call result clearly identifies
   the OS, store the answer and skip step 2.
2. **Otherwise, call `AskUserQuestion` with this exact shape:**

   ```yaml
   questions:
     - question: "Are you setting up McBrain on a Mac or a Windows PC?"
       header: "OS"
       multiSelect: false
       options:
         - label: "Mac"
           description: "Apple computer running macOS."
         - label: "Windows"
           description: "PC running Windows 10 or 11."
   ```

Store the answer as `OS_TYPE` ∈ {`mac`, `windows`} and use it to pick
the right paths/commands from the reference table below for every later
step. **Never run a Mac command on a Windows user, or vice versa.**

### Cross-platform path & command reference

Every Mac-specific path and command in this SKILL has a Windows
equivalent here. When a later step refers to "App Support / config" or
"the Python install command", pick the row for the user's `OS_TYPE`.

| Concept | macOS | Windows |
|---|---|---|
| User home | `~/` | `%USERPROFILE%\` (e.g. `C:\Users\<name>\`) |
| Default Documents | `~/Documents/` | `%USERPROFILE%\Documents\` |
| Claude Desktop config (registers MCPs) | `~/Library/Application Support/Claude/claude_desktop_config.json` | `%APPDATA%\Claude\claude_desktop_config.json` |
| McBrain engine runtime install dir | `~/Library/Application Support/mcbrain-engine/` | `%LOCALAPPDATA%\mcbrain-engine\` |
| McBrain vault registry | `~/Library/Application Support/mcbrain/vaults.json` | `%APPDATA%\mcbrain\vaults.json` |
| Parent directory to grant for engine + Claude config | `~/Library/Application Support/` | grant `%APPDATA%\` AND `%LOCALAPPDATA%\` separately (different parents on Windows) |
| Hidden-folder reveal in folder picker | Cmd-Shift-. | Already visible; navigate via address bar |
| Type-a-path shortcut in folder picker | Cmd-Shift-G | Address bar (Ctrl-L in Explorer) |
| Python install command | `xcode-select --install` (recommended) or python.org | Microsoft Store ("Python 3.12") or python.org installer (check "Add python.exe to PATH") |
| ripgrep install | `brew install ripgrep` | `winget install BurntSushi.ripgrep.MSVC` (or `scoop install ripgrep`) |
| GitHub CLI install | `brew install gh` | `winget install --id GitHub.cli` |
| Venv interpreter inside venv (set automatically by launcher) | `<venv>/bin/python` | `<venv>\Scripts\python.exe` |
| Engine launcher path (used in MCP `command`) | `~/Library/Application Support/mcbrain-engine/launcher.py` | `%LOCALAPPDATA%\mcbrain-engine\launcher.py` |

**Important Windows note:** the engine runtime lives under `%LOCALAPPDATA%\`
(per-machine cache) but the Claude Desktop config and registry live under
`%APPDATA%\` (per-user roaming). On Windows the two are *different
directories* — you'll need two separate folder grants in Step 5 / 5.6
rather than one. On macOS, both are under `~/Library/Application Support/`
so a single grant covers everything.

---

## Step 1: Name and locate the vault

Ask the user:

> "What would you like to call this knowledge base? For example: 'AI Science', 'Finance', 'Clinical Guidelines', 'Personal'."

From their answer, derive:
- **MCP name**: lowercase, hyphenated, prefixed with `mcbrain-` — e.g., "AI Science" → `mcbrain-ai-science`
- **Default folder name**: same as MCP name

Then ask:

> "Where do you want it to live? I'll suggest `~/Documents/mcbrain` — or pick a different path if you prefer."

Adjust the suggestion to the user's OS:
- macOS: `~/Documents/<mcp-name>`
- Windows: `C:\Users\<username>\Documents\<mcp-name>`

Store the confirmed path as `VAULT_PATH` and the MCP name as `MCP_NAME`. Expand `~` to the full home directory path.

---

## Step 2: Choose backup strategy

Ask before creating any files — the backup choice affects how the vault
is initialized.

**Call `AskUserQuestion` with this exact shape:**

```yaml
questions:
  - question: "How do you want to back up McBrain?"
    header: "Backup"
    multiSelect: false
    options:
      - label: "Git + GitHub (Recommended)"
        description: "Versioned history of every wiki page; roll back any bad edit. Free private repo. Slight extra setup."
      - label: "Google Drive"
        description: "Simplest option — auto-syncs the folder like Dropbox. No terminal needed."
      - label: "None"
        description: "Local only. Not recommended; the vault is unrecoverable if the machine dies."
```

Store the choice as `BACKUP_STRATEGY` ∈ {`git`, `google-drive`, `none`}.
If the user picks "Other" and writes free text, parse it into one of
the three values; if it's not clearly one of them, re-ask using the
same call shape.

---

### Option A: Git + GitHub

Set up GitHub first — the remote repo needs to exist before the vault is created so you can set it as the origin immediately.

**A1 — Confirm GitHub account and capture the username**

Ask the user:

> "Do you already have a GitHub account?
>
> - If yes, what's your **GitHub username**? (I need it to construct your
>   repo URL so the rest of setup is hands-off — I won't have to ping you
>   later to paste anything back.)
> - If no, go to [github.com](https://github.com), create a free account,
>   then come back and tell me your username."

Wait for the username. Store it as `GITHUB_USERNAME`. You'll use this
later to construct `REPO_URL` directly as
`https://github.com/<GITHUB_USERNAME>/<MCP_NAME>` — no need to wait for
the user to run `gh repo view` and paste the URL back.

**A2 — Install the GitHub CLI** *(present to the user; do not run from Cowork's Bash)*

> **Sandbox reminder**: do NOT run `gh --version`, `which gh`, or any
> other gh check from the Bash tool. The sandbox doesn't have gh and
> never will — but the user's host does (or doesn't) independently of
> that. Reporting "gh isn't installed" based on a sandbox check is wrong
> and will confuse the user. Just ask them.

**Call `AskUserQuestion` with this exact shape:**

```yaml
questions:
  - question: "Do you have the GitHub CLI (`gh`) installed on your computer?"
    header: "gh installed?"
    multiSelect: false
    options:
      - label: "Yes, already installed"
        description: "I've used gh before, or I just ran `gh --version` and it worked."
      - label: "No / not sure"
        description: "I haven't installed it, or running `gh --version` errors. Walk me through the install."
```

If the user picks **Yes**, continue to **A3**.

If the user picks **No / not sure**, present the install command for
their `OS_TYPE` (do NOT install it yourself from Cowork's Bash):

- macOS with Homebrew installed: `brew install gh`
  - If Homebrew isn't installed, point them at [brew.sh](https://brew.sh) for
    the installer, then retry the gh install.
- Windows: `winget install --id GitHub.cli` (or [cli.github.com](https://cli.github.com))
- Linux: [cli.github.com/manual/installation](https://cli.github.com/manual/installation)

Then ask in plain text: *"Run `gh --version` in your Terminal /
PowerShell once the install finishes and paste the output back."* The
paste-back is a free-text confirmation; don't use `AskUserQuestion` for
it (the user is providing a version string, not picking from options).

**A3 — Authenticate** *(present to the user; do not run from Cowork's Bash)*

Tell the user to run in their Terminal:

```bash
gh auth login
```

Walk them through the prompts:
- "What account do you want to log into?" → GitHub.com
- "What is your preferred protocol?" → HTTPS
- "Authenticate Git with your GitHub credentials?" → Yes
- "How would you like to authenticate?" → Login with a web browser → follow
  the one-time code flow

Ask the user to confirm authentication succeeded before continuing.

**A4 — Create the private repo on GitHub** *(present to the user)*

You already have `GITHUB_USERNAME` and `MCP_NAME`, so construct
`REPO_URL` yourself:

```
REPO_URL = https://github.com/<GITHUB_USERNAME>/<MCP_NAME>
```

Tell the user to run **one** command in their Terminal (substituting the
actual `MCP_NAME`):

```bash
gh repo create MCP_NAME --private
```

Ask the user to confirm it succeeded (no need to paste anything back —
you constructed `REPO_URL` already). If they got an "already exists"
error, that's fine — it just means a repo of that name was created in a
prior attempt.

Confirm with the user: *"Created private repo at `REPO_URL`. We'll link
the vault to it in Step 3 — also a Terminal command you'll run yourself."*

---

### Option B: Google Drive

No setup needed at this stage. Note the selection and continue to Step 3.

---

### Option C: None

Confirm once before continuing:

> "Just to confirm — with no backup, if your computer is lost or the vault is accidentally deleted, McBrain can't be recovered. Are you sure?"

If confirmed, note the selection and continue to Step 3.

---

## Step 3: Create the vault

### Grant Cowork access to the vault's parent directory

Call **`mcp__cowork__request_cowork_directory`** with the parent directory
of `VAULT_PATH` (typically `~/Documents/`). The user gets a grant prompt
in Cowork. Wait for the grant, then verify by listing the granted mount
with the Bash tool. If `mcp__cowork__request_cowork_directory` is
unavailable, fall back to telling the user to click the **+** button in
Cowork and grant access to the parent folder manually.

### Create the structure

Create the following structure under `VAULT_PATH`:

```
VAULT_PATH/
├── raw/                    # Immutable source documents — LLM reads, never writes
│   ├── articles/           # Web clips, saved articles
│   ├── papers/             # PDFs, research papers
│   ├── notes/              # Personal notes, journal entries
│   └── assets/             # Downloaded images (set as Obsidian attachment folder)
├── wiki/                   # LLM-owned compiled markdown
│   ├── index.md            # Master catalog of all wiki pages
│   ├── log.md              # Append-only operation log
│   └── overview.md         # High-level synthesis of everything in McBrain
├── .obsidian/              # Pre-seeded with app.json so Obsidian picks up vault settings on first open
│   └── app.json            # Copied from references/app.json — sets new-note location, attachments, and ignore filters
└── CLAUDE.md               # Schema + instructions for Claude (the key config file)
```

**Do this via the Write tool against the granted parent mount, not via
`mkdir`/`touch` in Cowork's Bash sandbox** — the sandbox is Linux and
doesn't reach the user's host filesystem. (You CAN use `mkdir -p` on the
mount path through the Bash tool — that works because the mount bridges
to the host. But Write tool is simpler for the file creation parts.)

Create placeholder files for index.md, log.md, overview.md, and CLAUDE.md
using the templates in the reference files below.

Read `references/claude-md-template.md` to get the CLAUDE.md content.
Read `references/index-template.md` to get the index.md starter.
Read `references/log-template.md` to get the log.md starter.
Read `references/overview-template.md` to get the overview.md starter.

Also create `VAULT_PATH/.obsidian/` and copy `references/app.json` into it as `VAULT_PATH/.obsidian/app.json` verbatim. This pre-configures Obsidian so new notes land in `wiki/`, attachments go to `raw/assets`, and `raw/` is excluded from search/graph — the manual Obsidian toggles in Step 6 (items 3–5) become unnecessary but can still be verified in the UI.

After writing CLAUDE.md from the template, append the following two sections:

**Web Ingestion Routing section** (always append, regardless of backup strategy):

```markdown
## Web Ingestion Routing

When fetching a URL to save into `raw/`, choose the right tool based on the situation:

- **Web fetch** (`mcp__workspace__web_fetch`): use first for any publicly accessible page. Fast and lightweight. Works well for open-access articles, documentation, Wikipedia, and plain HTML pages. If it returns incomplete content, an error, or a login wall, switch to Claude in Chrome.
- **Claude in Chrome** (Cowork extension): use when the page is paywalled, requires a login, or is a JavaScript-heavy single-page app that web fetch can't render. Claude navigates the page in the user's real browser session, so it handles authentication and dynamic content automatically.
- **Obsidian Web Clipper**: do not invoke this yourself — it is a browser extension the user operates. Recommend it when the user mentions they are actively browsing and want to save articles for later rather than ingesting right now. It saves directly to `raw/articles/` and is ideal for batch collecting during a browsing session.

Default behavior: attempt web fetch first. On failure or thin content, switch to Claude in Chrome. Suggest Obsidian Web Clipper only when the user's intent is save-for-later rather than ingest-now.
```

**Backup section** (content depends on strategy chosen in Step 2):

For Git:
```markdown
## Backup

- Strategy: git
- Remote: REPO_URL
- Push command: `git push origin main`

### How Claude handles git for this vault

**Claude must not run git commands against this vault.** The vault is mounted via the filesystem MCP, which holds open handles that race with git. When Claude (operating through the vault MCP) runs `git add` / `git commit` / `git push`, the call can leave a stale `.git/index.lock` file that the user has to remove manually before any further git work succeeds. Bad UX.

Instead, after meaningful operations (ingest, lint, batch synth), Claude **presents** the commands to the user as a copy-paste block. The user runs them in their own terminal:

\`\`\`
cd VAULT_PATH && git add -A && git commit -m "<message>" && git push origin main
\`\`\`

Mirror the log entry in the commit message: `ingest: <source title>`, `lint: <summary>`, `synth: <topic>`. Good commit messages are short and describe the operation, not the diff.

### Recovery
- To revert a bad edit, present: `cd VAULT_PATH && git log --oneline` to find the commit, then `git checkout <hash> -- wiki/<filename>.md`.
- If a stale `.git/index.lock` exists from a prior interrupted run, present: `rm VAULT_PATH/.git/index.lock`.
```

When you write this section into the actual `CLAUDE.md`, replace the literal placeholder `VAULT_PATH` with the user's confirmed path and unescape the backticks around the fenced code block (i.e. write a real triple-backtick fence, not the `\`\`\`` shown above — the escape is only there to avoid breaking *this* skill's markdown).

For Google Drive:
```markdown
## Backup

- Strategy: google-drive
- Sync: Drive for Desktop watches VAULT_PATH and uploads changes automatically
- No extra steps needed after working in the vault — Drive syncs continuously
- To restore files: visit drive.google.com and navigate to the synced vault folder
```

For None:
```markdown
## Backup

- Strategy: none
- No backup is configured. To set one up later, ask Claude to "set up McBrain backup".
```

### Initialize git (Git strategy only) — *user runs this in Terminal*

You should not run `git init`, `git remote add`, `git commit`, or `git push`
from Cowork — Cowork's Bash sandbox doesn't reach the user's vault path,
and even if it did, those calls would race with the vault's filesystem MCP
and leave a stale `.git/index.lock`. Same rule applies in Claude Code: do
not invoke git against the vault yourself.

First, **write the `.gitignore` yourself** — that's a file operation, you
can do it via the Write tool against the granted vault directory. The file
should contain:

```
.DS_Store
.obsidian/workspace*
.obsidian/cache
.mcbrain/index.db
.mcbrain/index.db-*
__pycache__/
```

Then **present** the following block for the user to run in their **Terminal**
(substituting the actual `VAULT_PATH`, `REPO_URL`, and `MCP_NAME`):

```bash
cd VAULT_PATH
git init -b main
git remote add origin REPO_URL
git add -A
git commit -m "init: MCP_NAME vault scaffolding"
git push -u origin main
```

Ask the user to confirm the push succeeded. If it fails, check that
`gh auth login` completed correctly and that `REPO_URL` is reachable.

The gitignore excludes the per-vault `.mcbrain/index.db` and its SQLite
WAL/SHM sidecars — those are rebuildable from `wiki/` content. Nothing else
under `.mcbrain/` exists in the v2 layout: the engine source and venv live
at the platform-resolved runtime root (see Step 5.6), not per-vault. A
fresh clone on a new machine just runs `mcbrain-setup` once to install the
runtime, then queries work immediately.

---

## Step 4: Google Drive sync (Google Drive strategy only)

**D1 — Install Google Drive for Desktop**

Go to [drive.google.com/drive/download](https://drive.google.com/drive/download), download and install the desktop app, sign in with a Google account.

**D2 — Add the vault folder to Drive sync**

Open the Google Drive for Desktop app → gear icon → Preferences → **My Computer** tab → **Add folder** → select `VAULT_PATH`.

Drive will now watch the folder and upload changes automatically.

**D3 — Verify sync**

Open [drive.google.com](https://drive.google.com) in a browser and confirm `CLAUDE.md` and the `wiki/` folder are visible. If they are, backup is live.

*(Skip this step entirely for Git and None strategies.)*

---

## Step 5: Configure filesystem MCP in Claude Desktop

The filesystem MCP gives Claude read/write access to the vault. We add
it by editing Claude Desktop's MCP config file. Pick the path for the
user's `OS_TYPE`:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

### Grant Cowork access to the Claude config directory

Call **`mcp__cowork__request_cowork_directory`** with the platform-correct
parent:

- macOS: `~/Library/Application Support/` (one grant covers both Step 5 and
  Step 5.6's engine install — same parent dir on Mac)
- Windows: `%APPDATA%\Claude\` (specific to this step; Step 5.6 will
  request a separate grant for `%LOCALAPPDATA%\` since they're different
  parents on Windows)

If `mcp__cowork__request_cowork_directory` is unavailable, fall back to
telling the user to click **+** (or 'Add folder') and grant access to
the platform-correct path above. macOS-only: the `Library` folder is
hidden by default — press **Cmd-Shift-.** in the folder picker to reveal
it, or **Cmd-Shift-G** to type the path. Windows: navigate via the
address bar (Ctrl-L), or paste `%APPDATA%` directly.

Wait for the grant. Verify by listing the granted mount with the Bash tool.

### Edit the config

Read `<application-support-mount>/Claude/claude_desktop_config.json`. If the
file doesn't exist, create one with the structure:

```json
{
  "mcpServers": {}
}
```

Merge the following entry into `mcpServers` (use the actual `MCP_NAME` and
`VAULT_PATH`, and preserve any existing entries — do NOT overwrite them):

```json
"MCP_NAME": {
  "command": "npx",
  "args": [
    "-y",
    "@modelcontextprotocol/server-filesystem",
    "VAULT_PATH"
  ]
}
```

Show the user the final config before writing it and confirm. Then write
via Edit tool — do NOT shell out to `cat > config.json` or similar from
Cowork's Bash; that would write into the sandbox, not the user's host.

---

## Step 5.5: Confirm Python prerequisite

The query engine needs Python 3.10+ on the user's machine. **Do not try
to detect this from the sandbox** — sandbox python ≠ user's Python.
Just ask once and trust the answer. The exact prompt depends on
`OS_TYPE` from Step 0.

### macOS prompt

> "Quick prerequisite check: McBrain's query engine needs **Python 3.10+
> installed on your Mac**. (One-time — every McBrain on this machine
> reuses the same Python.)
>
> Run in **Terminal**:
>
> ```
> python3 --version
> ```
>
> - If it prints `Python 3.10` or higher → reply 'yes', we'll move on.
> - If it errors or prints something older → install with `xcode-select
>   --install` (recommended, gets Python + dev tools) or download from
>   [python.org/downloads](https://www.python.org/downloads/). Reply
>   when done.
>
> (`ripgrep` is optional but makes search faster: `brew install ripgrep`.
> Setup continues either way.)"

### Windows prompt

> "Quick prerequisite check: McBrain's query engine needs **Python 3.10+
> installed on your PC**, with `python` on your PATH. (One-time — every
> McBrain on this machine reuses the same Python.)
>
> Run in **PowerShell or Command Prompt**:
>
> ```
> python --version
> ```
>
> - If it prints `Python 3.10` or higher → reply 'yes', we'll move on.
> - If `python` isn't recognized, or prints something older → easiest
>   path is **Microsoft Store → search for 'Python 3.12' → Install**
>   (auto-adds to PATH). Or use the python.org installer and **check
>   'Add python.exe to PATH' during install**. Reply when done.
>
> (`ripgrep` is optional but makes search faster:
> `winget install BurntSushi.ripgrep.MSVC`. Setup continues either way.)"

Wait for their reply. Trust it. If they're wrong, the launcher will fail
with a clear stderr message at first MCP call (Step 8.5) and they can
install Python and retry. Do not run `python3 --version` from Bash to
"verify" — it tells you nothing useful.

---

## Step 5.6: Install the engine runtime + register the MCP

Install the engine source files and add the MCP entry to Claude Desktop's
config so the engine launches natively next time Claude Desktop starts.

### What gets installed where

Pick the right paths for `OS_TYPE`:

- **Engine runtime files** copied to:
  - macOS: `~/Library/Application Support/mcbrain-engine/`
  - Windows: `%LOCALAPPDATA%\mcbrain-engine\`

  Files copied: `launcher.py`, `mcbrain_engine.py`, `paths.py`, `registry.py`,
  `schema.sql`, `requirements.txt`. (No venv yet — the launcher creates that
  on first MCP launch in Step 8.5.)

- **MCP registration** added to `mcpServers.mcbrain-engine` in:
  - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
  - Windows: `%APPDATA%\Claude\claude_desktop_config.json`

### Get the right folder grant

- **macOS**: the `~/Library/Application Support/` grant from Step 5 already
  covers `mcbrain-engine/` (same parent). Verify the grant is still active
  by listing the mount; re-grant the same folder if the user revoked it.
- **Windows**: `%APPDATA%\` (Step 5) and `%LOCALAPPDATA%\` are *different*
  parents. Call `mcp__cowork__request_cowork_directory` again with
  `%LOCALAPPDATA%\` so you can write to `%LOCALAPPDATA%\mcbrain-engine\`.
  (Fallback: ask the user to grant `%LOCALAPPDATA%\` via the + button.)
  Then verify by listing the new mount.

### Copy the engine runtime files

> **Sandbox reminder (this is where the SKILL has broken before)**:
> install these files using **Read + Write tools only** — never `cp`,
> `shutil.copy*`, `cat > file`, `tee`, `mv`, or any other Bash-side
> write to the mount. Bash writes through Cowork's FUSE bridge can
> silently fail to flush, leaving files that look fine in `ls -l` but
> are truncated, missing, or invisible to the host. Tiny files
> sometimes land; engine source files (~10–40 KB+) often don't. The
> Write tool runs natively on the host and is the only reliable path.

For each runtime source file:

1. Read it from the plugin install with the **Read** tool:
   - `${CLAUDE_PLUGIN_ROOT}/mcp-server/launcher.py`
   - `${CLAUDE_PLUGIN_ROOT}/mcp-server/mcbrain_engine.py`
   - `${CLAUDE_PLUGIN_ROOT}/mcp-server/paths.py`
   - `${CLAUDE_PLUGIN_ROOT}/mcp-server/registry.py`
   - `${CLAUDE_PLUGIN_ROOT}/mcp-server/schema.sql`
   - `${CLAUDE_PLUGIN_ROOT}/mcp-server/requirements.txt`
2. Write it to the destination with the **Write** tool — destination
   path is `<application-support-mount>/mcbrain-engine/<filename>`.

Creating the destination directory `<application-support-mount>/mcbrain-engine/`
with Bash `mkdir -p` against the mount path is fine — `mkdir` is small
and idempotent and doesn't trigger the FUSE flush problem. The danger
is *file-content* writes, not directory creation.

After all six files are written, verify with `ls -l` on the mount and
**check the byte sizes match the source files** (a 0-byte or
truncated `mcbrain_engine.py` is the classic FUSE-flush failure mode).
If any size is wrong or any file is missing, re-run the Write for that
file — do not try to "fix" it with Bash.

### Register the MCP entry

Read the platform-correct config file (path from the table above). If the
file doesn't exist (fresh Claude Desktop install), create a minimal one:

```json
{
  "mcpServers": {}
}
```

Merge `mcbrain-engine` into `mcpServers`. **The `command` and `args`
values are platform-specific — use the matching pair below**:

**macOS:**
```json
"mcbrain-engine": {
  "command": "python3",
  "args": ["/Users/<USER>/Library/Application Support/mcbrain-engine/launcher.py"]
}
```
Replace `<USER>` with the user's macOS username (ask if you don't know,
or read from the granted mount path).

**Windows:**
```json
"mcbrain-engine": {
  "command": "python",
  "args": ["C:\\Users\\<USER>\\AppData\\Local\\mcbrain-engine\\launcher.py"]
}
```
Replace `<USER>` with the user's Windows username. Note the **double
backslashes** in the JSON `args` value — that's required JSON escaping;
`json.dump` handles this automatically when you write a Python string.
On Windows, prefer `python` over `python3` since `python3` is not always
on PATH.

Use `json.dump` semantics — preserve existing entries; only add or update
`mcbrain-engine`. If `mcbrain-engine` already exists with matching
`command` + `args`, it's a no-op.

Show the user the final config block before writing, and confirm.

### Idempotency notes

- Re-running setup against a machine that already has the runtime: the file
  copies overwrite (which is fine — they're identical or newer); the MCP
  entry is detected as already-correct and left alone.
- Plugin updates: re-run **just Step 5.6** to refresh the runtime files. The
  launcher auto-detects mismatches and rebuilds the venv if needed.

### Important: restart Claude Desktop

Tell the user explicitly:

> "I've installed the runtime files and registered the engine. **You need
> to quit and reopen Claude Desktop now** for the new MCP entry to take
> effect.
>
> - **macOS**: quit from the menu bar (Claude → Quit Claude, or Cmd-Q) —
>   closing the window isn't enough.
> - **Windows**: right-click the Claude icon in the system tray and choose
>   Exit — closing the window isn't enough.
>
> The first time you ask McBrain a question after restarting, the engine
> will spend ~30 seconds creating its Python virtual environment and
> downloading the FastEmbed embedding model (~30 MB). After that, every
> question is fast. We'll trigger that warmup deliberately in Step 8.5."

---

## Step 6: Extensions and Obsidian setup

**Browser extensions to install first:**

- **Claude in Chrome** — lets Claude navigate pages in the user's real browser session, handling paywalls and logins that a plain web fetch can't reach. Install from the Chrome Web Store and sign in with the Anthropic account. This is what makes ingesting paywalled or authenticated content seamless.
- **Obsidian Web Clipper** — one-click capture of web articles into `raw/articles/` while browsing. Great for collecting articles in bulk to ingest later. Configure the clipper's vault to point at `VAULT_PATH` and its default folder to `raw/articles/`.

**Obsidian setup:**

1. **Open Obsidian** → click "Open folder as vault" → select `VAULT_PATH`
2. **Verify wikilinks are enabled**: Settings → Files & Links → confirm **"Use [[Wikilinks]]"** is on (default)
3. **Default note location to `wiki/`** — pre-set via `.obsidian/app.json`. Verify under Settings → Files & Links → Default location for new notes → "In the folder specified" → `wiki`.
4. **Exclude `raw/` from search and graph** — pre-set via `.obsidian/app.json`. Verify under Settings → Files & Links → Excluded files → `raw/`.
5. **Attachment folder** — pre-set via `.obsidian/app.json`. Verify under Settings → Files & Links → Default location for new attachments → "In the folder specified below" → `raw/assets`.
6. **Recommended Obsidian plugins** (all optional):
   - **Dataview** (community) — queryable YAML frontmatter
   - **Graph View** (built-in) — see the shape of the vault
   - **Marp** (community) — optional, for slide deck output
7. **Restart Claude Desktop** after editing the MCP config so the filesystem server loads

---

## Step 7: Verify

After the user restarts Claude Desktop, tell them to start a new conversation and say:

> "Using the MCP_NAME MCP, read CLAUDE.md and tell me the wiki structure."

If Claude can read `CLAUDE.md`, the MCP is working. If not, troubleshoot:
- Node.js is installed (`node --version`)
- The config JSON is valid (no trailing commas, correct path)
- Claude Desktop was fully restarted (quit from menu bar, not just closed)

---

## Step 8: Notion companion database (optional)

McBrain pairs nicely with a Notion research tracker: a database where the user queues research questions, the `notion-research-runner` skill drains the queue with parallel subagents and writes findings back to each task page, and then the Notion-bridged ingest mode (documented in CLAUDE.md) pulls those findings into `raw/notes/` and onward into the wiki. This step wires up that pairing at setup time so it's already configured the first time the user wants to use it.

This step is **optional**. Skip cleanly if the user doesn't use Notion or doesn't want a tracker right now.

**8a — Check for a Notion MCP connector.** Enumerate available tools and look for ones that perform Notion operations. Match by *capability*, not exact name — many connectors exist (Anthropic's claude.ai Notion connector, Notion's official `@notionhq/notion-mcp-server`, community servers). The capabilities needed here are *search*, *create-database*, and *retrieve-database*; tool names typically contain `notion`, `search`, `database`, or fragments like `API-post-search` / `API-post-database`.

If no Notion-like tools are present, tell the user:

> "I don't see a Notion MCP connector. To pair this McBrain with a Notion research tracker, enable one (Anthropic's claude.ai Notion connector, Notion's official MCP server, or equivalent) and re-run this step. Skipping for now."

Then continue to Step 9 — do not block setup on this.

**8b — Ask what the user wants.**

**Call `AskUserQuestion` with this exact shape:**

```yaml
questions:
  - question: "Want a Notion research tracker paired with this McBrain? Pick one:"
    header: "Notion DB"
    multiSelect: false
    options:
      - label: "Use existing database"
        description: "I already have a Notion DB I want to use. I'll paste the URL."
      - label: "Create a new one"
        description: "Spin up a new database with the standard schema (Task, Status, Priority, dates, Notes)."
      - label: "Skip for now"
        description: "Maybe later — I can run the `notion-research-db` skill any time to add one."
```

Store the choice as `NOTION_DB_INTENT` ∈ {`yes-existing`, `yes-create`, `no`}.

If they pick **Skip for now** (`no`), continue to Step 9.

**8c — Existing database.** If they pick option 1, ask for the database URL and a friendly name (default to the database title). Use the Notion search/fetch tool to verify the URL resolves to a real database — if not, surface the error and ask the user to re-paste rather than guessing. Capture:

- `NOTION_DB_NAME` — e.g., "AI Science Research Tracker"
- `NOTION_DB_URL` — the database URL the user pasted
- `NOTION_DB_ID` — extract from the URL (the 32-char hex segment, with or without dashes)

**8d — Create a new database.** If they pick option 2, defer to the `notion-research-db` skill: it already knows how to confirm the parent page, create the database with the right schema, and return the URL/ID. Pass through the vault's research topic (default: the vault's name minus the `mcbrain-` prefix) and the `MCP_NAME` so it knows where to register. Capture the same three fields from its return.

The `notion-research-db` skill will also write a registration entry — let it. Step 8e below either *adds* the entry (if the skill didn't, e.g., for the existing-database path) or *verifies* the entry the skill wrote.

**8e — Register in CLAUDE.md.** The canonical location for registered companion databases is a `## Notion companion databases` section in `VAULT_PATH/CLAUDE.md`. Append (creating the section if missing):

```markdown
## Notion companion databases

- **<NOTION_DB_NAME>**
  - URL: <NOTION_DB_URL>
  - Database ID: <NOTION_DB_ID>
  - Registered: <YYYY-MM-DD — look up today's date, do not guess>
  - Notes: companion research tracker. The Notion-bridged ingest mode reads this entry to find which DB to drain.
```

If the section already exists (e.g., the `notion-research-db` skill wrote it during 8d), just verify the entry is present and correct — don't duplicate it.

This is the location the `mcbrain` skill checks when running a Notion-bridged ingest. Older vaults registered their DB in `wiki/notion-databases.md`; the ingest procedure falls back to that path if the CLAUDE.md section is empty, so both work.

**8f — Commit (Git strategy only).** If backup strategy is git, **do not run git directly** — by this point the filesystem MCP is loaded and direct git calls can leave a stale `.git/index.lock` (see CLAUDE.md's `## Backup → How Claude handles git for this vault`). Instead, **present** the commit block to the user in a copy-paste fence and ask them to run it in their terminal:

```bash
cd VAULT_PATH && git add CLAUDE.md && git commit -m "register: notion companion DB <NOTION_DB_NAME>" && git push
```

---

## Step 8.5: Trigger first MCP launch + provision this vault's index

After Step 5.6 the user has restarted Claude Desktop. Now we want to:
1. Trigger the launcher's one-time bootstrap (creates the venv + downloads
   the FastEmbed model). This runs natively on the user's host machine via
   Claude Desktop.
2. Call the `mcbrain-engine` MCP's `migrate` tool to register this vault and
   patch its CLAUDE.md.

Both happen in one shot when we make the first `migrate` MCP call — the
launcher bootstraps before the tool returns.

### Confirm the user has restarted

Ask:

> "Did you quit and reopen Claude Desktop after Step 5.6? The new MCP entry
> only takes effect after a full restart (quit from menu bar — closing the
> window isn't enough)."

If they say no, wait. Don't call the migrate tool until they confirm.

### Call the migrate tool

Once they confirm restart, in this **same** Cowork session, the
`mcbrain-engine` MCP should now be loaded. Verify by listing available MCP
tools — `query`, `index_sync`, `migrate`, etc. should appear.

Call the `migrate` tool with:
- `vault_path` = `VAULT_PATH` (absolute path confirmed in Step 1)
- `vault_name` = `MCP_NAME` (e.g. `mcbrain-ai-science`)

**This first call takes ~30 seconds** while the launcher creates the venv
and downloads the FastEmbed model. Tell the user:

> "Calling the migrate tool now. The first call will take ~30 seconds while
> the engine sets itself up — Python virtual environment creation, fastembed
> + numpy + mcp install (~50 MB of pip downloads), and the FastEmbed
> embedding model (~30 MB). Subsequent calls are instant. If Claude Desktop
> shows a 'failed to connect to MCP' error during this first call, that's
> the connection timing out during bootstrap — quit and reopen Claude
> Desktop once more, the venv will be ready and the second attempt will be
> instant."

### What `migrate` does

1. Ensures `VAULT_PATH/.mcbrain/` exists.
2. If a legacy PR #4 `.mcbrain/{bin,venv}/` is present (carry-over from an
   older McBrain install), reads `index.db` meta. If the embedding model+dim
   match, the index is preserved and `bin/`/`venv/` directories are removed.
   Otherwise the index is wiped for rebuild.
3. Writes/updates the vault's entry in the platform-resolved registry
   (`~/Library/Application Support/mcbrain/vaults.json` on macOS,
   `%APPDATA%\mcbrain\vaults.json` on Windows).
4. Patches `VAULT_PATH/CLAUDE.md` with the MCP-flavored Query operation and
   the `## Query engine` section (mode marker `lexical+semantic (mcp)`).
5. Runs an initial `index_sync`. Empty wiki → timestamps the meta table.

Surface the JSON output. The `legacy_layout_removed`, `rebuilt_for_mismatch`,
and `claude_md_patched` flags make it visible what migrate actually did.

### Recovery from first-launch timeout

If Claude Desktop shows "failed to connect to MCP" during the first call:
- The launcher is still running in the background, finishing the pip install
- Wait ~60 seconds, then quit and reopen Claude Desktop
- In the new session, retry the migrate call — it'll skip the bootstrap and
  succeed instantly

If the launcher genuinely failed (network, missing Python), Claude Desktop's
MCP debug panel will show the launcher's stderr output, which says exactly
what went wrong. Surface that to the user and walk through the fix
(install Python, retry, etc.).

### Commit (Git strategy only)

If the backup strategy is git, present a copy-paste block:

```bash
cd VAULT_PATH && git add CLAUDE.md && git commit -m "init: provision query engine" && git push
```

(Note: `.mcbrain/index.db` is gitignored per Step 3 — only the patched
CLAUDE.md needs committing.)

---

## Step 9: Install the companion operating skill

Point the user at the `mcbrain` skill for day-to-day ingest/query/lint operations. It uses the `MCP_NAME` convention to route requests to the right vault — so "find insights from McBrain AI Science" maps to the `mcbrain-ai-science` MCP automatically.

---

## Step 10: First ingest

Walk the user through their first ingest:

1. Drop a source file into the appropriate `raw/` subfolder (or paste a URL)
2. In Claude: *"Ingest `raw/articles/filename.md` into McBrain. Update index.md and log.md."*

Claude will read the source, discuss key points, write wiki pages in `wiki/`, update `wiki/index.md` and `wiki/log.md`.

**For PDFs:**
1. Upload the PDF into the chat
2. Claude invokes Cowork's built-in `pdf` skill — handles extraction, page rendering, and visual inspection automatically
3. Claude saves extracted text to `raw/papers/<name>.md` via the vault MCP
4. Claude describes substantive figures as prose under `## Figure N — [Title]` headings
5. Normal ingest proceeds

---

## Key operations to teach the user

**Ingest**: `"Ingest raw/articles/[file] into McBrain. Update index and log."`
**Query**: `"Ask McBrain: [question]. Cite the pages you used."`
**Lint**: `"Lint McBrain. Find contradictions, orphan pages, stale claims, missing cross-references."`
**Save a query answer**: `"File your answer as a new wiki page at wiki/[topic].md"`

**Query-engine maintenance** (rare — the index normally stays current automatically because every wiki write runs `index_sync`):

- *Full reindex* if the embedding model or schema changes, or if results stop making sense: call the `mcbrain-engine` MCP's `index_rebuild` tool (the `mcbrain-ops` skill knows the right invocation).
- *Index health check*: call the `mcbrain-engine` MCP's `index_status` tool — returns doc count, last sync time, and the active embedding model.
- *Remove a vault's index*: call the `mcbrain-engine` MCP's `uninstall` tool with `force=true` — deletes `<vault>/.mcbrain/` and the registry entry. Wiki and raw content are untouched. The shared engine runtime is left alone.
- *Remove the engine runtime entirely*: see the "Nuke and reinstall" section of `plugins/mcbrain/mcp-server/README.md`.

---

## Reference files

- `references/claude-md-template.md` — The CLAUDE.md schema template
- `references/index-template.md` — Starter index.md
- `references/log-template.md` — Starter log.md
- `references/overview-template.md` — Starter overview.md
- `references/app.json` — Obsidian vault settings; copy verbatim to `VAULT_PATH/.obsidian/app.json`
