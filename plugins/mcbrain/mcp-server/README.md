# mcbrain-engine

Stdio MCP server backing the `mcbrain` and `mcbrain-ops` skills. One install
per machine, serves every McBrain vault on the box via a `vault` argument.

## Prerequisite

**Python 3.10+ must be installed on the user's machine before setup.** The
setup SKILL asks the user to confirm this. Install per OS:

- **macOS**: `xcode-select --install` (recommended) or
  [python.org/downloads](https://www.python.org/downloads/).
- **Windows**: Microsoft Store ("Python 3.12") — auto-adds to PATH — or the
  python.org installer with "Add python.exe to PATH" checked.
- **Linux**: distro package (`apt install python3`, `dnf install python3`, etc.)
  or [python.org/downloads](https://www.python.org/downloads/).

## Runtime layout

The `mcbrain-setup` skill copies these source files into a per-machine
runtime directory. The first time Claude Desktop launches the MCP, the
`launcher.py` script creates a venv next to the source files and pip installs
the requirements. After first launch the runtime looks like:

```
<runtime_root>/
├── launcher.py           # bootstraps venv on first launch (stdlib-only)
├── mcbrain_engine.py     # the MCP server entry point
├── paths.py              # cross-platform path resolver (stdlib-only)
├── registry.py           # vault registry I/O
├── schema.sql            # SQLite schema for <vault>/.mcbrain/index.db
├── requirements.txt      # mcp, fastembed, numpy
└── venv/                 # created by launcher on first launch
```

## How the launcher works

Claude Desktop registers the MCP entry as:

```json
"mcbrain-engine": {
  "command": "python3",
  "args": ["<runtime_root>/launcher.py"]
}
```

On first launch, `launcher.py`:
1. Verifies Python ≥ 3.10
2. Creates `<runtime_root>/venv/`
3. `pip install -r requirements.txt` into the venv (~50 MB download)
4. `os.execv` into `<venv>/bin/python <runtime_root>/mcbrain_engine.py` — the
   MCP server takes over the same process slot
5. FastEmbed downloads its embedding model (~30 MB) on the first `query`
   call into `~/Library/Caches/fastembed/`

Total first-launch cost: ~30 sec. Claude Desktop may show a transient
"connection failed" error if it times out during bootstrap; restart Claude
Desktop and the next launch is instant.

On every subsequent launch, `launcher.py` checks that the venv is healthy
(can import `fastembed, numpy, mcp`) and `os.execv`s straight into the
engine — no measurable startup overhead.

`<runtime_root>` resolves per-OS:

| OS      | Runtime root                                              |
| ------- | --------------------------------------------------------- |
| macOS   | `~/Library/Application Support/mcbrain-engine/`           |
| Windows | `%LOCALAPPDATA%\mcbrain-engine\`                          |
| Linux   | `~/.local/share/mcbrain-engine/` (`$XDG_DATA_HOME` wins)  |

## Vault registry

`vaults.json` lists every vault that has been migrated. Each `migrate` call
appends or refreshes an entry; `uninstall` removes it.

| OS      | Registry path                                              |
| ------- | ---------------------------------------------------------- |
| macOS   | `~/Library/Application Support/mcbrain/vaults.json`        |
| Windows | `%APPDATA%\mcbrain\vaults.json`                            |
| Linux   | `~/.config/mcbrain/vaults.json` (`$XDG_CONFIG_HOME` wins)  |

## Manual launch (debugging)

The MCP server normally runs under Claude Desktop or Claude Code. To
smoke-test it directly over stdio:

```bash
# macOS / Linux — through the launcher (handles venv bootstrap)
python3 ~/Library/Application\ Support/mcbrain-engine/launcher.py
```

```cmd
:: Windows
python3 %LOCALAPPDATA%\mcbrain-engine\launcher.py
```

The process speaks JSON-RPC over stdin/stdout; pipe in an MCP `initialize`
request to verify it loads. The launcher's stderr shows whether the venv was
ready or had to be bootstrapped.

## Nuke and reinstall

The runtime is treated as a cache — safe to delete and re-create from a fresh
`mcbrain-setup` run. Indexes inside each vault's `.mcbrain/` are independent
and will be re-detected on the next `migrate` or `index_sync`.

```bash
# macOS
rm -rf ~/Library/Application\ Support/mcbrain-engine ~/Library/Application\ Support/mcbrain

# Linux
rm -rf ~/.local/share/mcbrain-engine ~/.config/mcbrain
```

```cmd
:: Windows
rmdir /s /q "%LOCALAPPDATA%\mcbrain-engine"
rmdir /s /q "%APPDATA%\mcbrain"
```

After deletion, re-run the `mcbrain-setup` skill — it'll detect the missing
runtime and reprovision.
