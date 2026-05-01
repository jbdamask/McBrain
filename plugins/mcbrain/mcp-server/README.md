# mcbrain-engine

Stdio MCP server backing the `mcbrain` and `mcbrain-ops` skills. One install
per machine, serves every McBrain vault on the box via a `vault` argument.

## Runtime layout

The setup skill copies these files into a per-machine runtime directory and
provisions a venv next to them. After install, the runtime looks like:

```
<runtime_root>/
├── venv/                 # python + pinned deps (see requirements.txt)
├── mcbrain_engine.py     # the MCP server entry point
├── paths.py              # cross-platform path resolver (stdlib-only)
├── registry.py           # vault registry I/O
└── schema.sql            # SQLite schema for <vault>/.mcbrain/index.db
```

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

The MCP server normally runs under Claude Code or Claude Desktop. To smoke-test
it directly over stdio:

```bash
# macOS / Linux
~/Library/Application\ Support/mcbrain-engine/venv/bin/python -m mcbrain_engine
```

```cmd
:: Windows
%LOCALAPPDATA%\mcbrain-engine\venv\Scripts\python.exe -m mcbrain_engine
```

The process speaks JSON-RPC over stdin/stdout; pipe in an MCP `initialize`
request to verify it loads.

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
