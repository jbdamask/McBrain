# Plan: Pivot McBrain Query Engine to a Local MCP Server (Cowork compatibility)

GitHub issue: [#11](https://github.com/jbdamask/McBrain/issues/11)

## Context

The query engine landed by PR #4 (issue #2) runs as Bash-invoked Python with a per-vault venv at `<vault>/.mcbrain/venv/`. It works in Claude Code on the user's Mac because Bash sees the local filesystem. It does **not** work in Claude Cowork (Desktop), whose Bash tool runs in an isolated Linux sandbox that cannot see `/Users/<me>/...` — `cd <vault_path>` returns 127, and the venv on the user's Mac is invisible.

This plan refactors the engine into a single global stdio MCP server, **`mcbrain-engine`**, that runs outside any sandbox (Claude launches it directly, just like the existing per-vault filesystem MCPs). One MCP entry per machine; it serves all McBrain vaults via a `vault` argument. The per-vault `.mcbrain/` shrinks to just `index.db`. The setup skill registers the server idempotently across additional McBrains. A new `mcbrain-ops` skill (replacing the PR #4 skill of the same name) becomes the maintenance frontend that calls MCP tools instead of shelling out to Python.

The engine runtime (single shared venv + script + FastEmbed deps) lives at the platform user-data dir (resolved via the stdlib-only `paths.py` helper — see below): `~/.local/share/mcbrain-engine/` on Linux, `~/Library/Application Support/mcbrain-engine/` on macOS, `%LOCALAPPDATA%\mcbrain-engine\` on Windows. Installed once at first-McBrain setup and reused by every subsequent vault — no per-vault Python install, no per-vault script copy. **Cross-platform support (macOS + Windows) is in scope; Linux falls out of the same path resolution but is not a tested target for v1.**

## Branch Base

Worktree branch `11-pivot-query-engine-to-local-mcp-server` was cut at `db04ca8` (pre-PR #4). `origin/main` is now `637f11b` (post-PR #4). **Implementation must rebase or merge `origin/main` first** so the PR #4 artifacts (current `mcbrain-ops` skill, `mcbrain_ops.py` dispatcher, tests, CLAUDE.md template additions, `.gitignore` entries) are present to be refactored. Without that rebase, "what changes" descriptions in this plan don't apply to the working tree.

## Recommended Approach

Build a stdio MCP server in Python using the official `mcp` SDK (FastMCP-style decorators) that exposes seven tools. Lift PR #4's pure-Python logic (search, indexing, schema, RRF, embedding) into reusable functions (`mcbrain_engine.py`) and wrap them with thin MCP tool handlers. Add a vault registry at the platform user-config dir (resolved via `paths.registry_path()` — stdlib-only). Refactor `mcbrain-setup` to bootstrap the runtime and register the MCP via a cross-platform Python bootstrap script. Refactor `mcbrain` to drop Bash-based migration and rely on the MCP being there. Replace the PR #4 `mcbrain-ops` SKILL.md (currently "the only place Python lives") with a thin maintenance skill that calls MCP tools. Update the CLAUDE.md template to call MCP tools. Carry over PR #4 unit/integration tests against the new module, and add registry tests, legacy-cleanup tests, cross-platform `paths.py` tests, and a single wire E2E test that spawns the server over stdio.

### Decisions made (to close issue's open questions)

| Question | Decision | Rationale |
|---|---|---|
| stdio MCP vs long-running daemon | **stdio**, with module-level FastEmbed singleton | Cowork sessions are long-lived; the embedder loads on the first `query`/`index_sync` and stays warm. Daemon adds port/IPC/lifecycle complexity for no gain. Lazy-import inside handlers so non-search tools (`list_vaults`, `migrate`, `uninstall`, `index_status`) don't pay model load. |
| `mcbrain-ops` vault resolution | **Registry primary** (`list_vaults` MCP tool reads the platform-resolved registry path); `list_allowed_directories` of the active filesystem MCP used only as a disambiguator when the user says "this vault" mid-session | Registry is the source of truth maintained by `migrate`. FS MCP's allowed dirs only cover the single vault that MCP owns; useful for resolving "this vault" but insufficient as primary. |
| Per-vault `bin/` for back-compat | **Full shrink**: `.mcbrain/` contains only `index.db` post-migration | Aligns with one-MCP-per-machine architecture. Preserves the index DB on migration when model+dim still match (no forced rebuild). |
| Test strategy | **Mostly importable**: keep PR #4's pytest suite shape against the new `mcbrain_engine.py` module, plus one stdio-wire E2E that exercises each tool | The dispatcher-style tests give 80% of the coverage at 5% of the cost. One wire test catches MCP-protocol regressions. |
| Cross-platform support | **macOS + Windows in scope for v1.** All platform branching is centralized in a `paths.py` helper module and the setup skill's bootstrap script. No platform-specific code leaks into engine handlers, registry, or tests. | Windows users (Cowork on Windows) need this on day one. Branching once at the edges keeps the engine logic itself OS-agnostic. |

## Changes

### NEW: `plugins/mcbrain/mcp-server/` (engine source)

A new top-level directory under the plugin, sibling to `skills/`. Holds the runtime artifacts that get copied to the platform-resolved runtime root (`paths.runtime_root()`) at install time. Treat as a runtime artifact, not a skill reference.

- `mcbrain_engine.py` — the stdio MCP server. Imports the `mcp` SDK; declares seven `@server.tool` handlers; thin wrappers over pure-Python functions lifted from PR #4's `mcbrain_ops.py`.
- `paths.py` — **NEW** centralized cross-platform path helper. **Stdlib-only** (no `platformdirs` dependency) — must be importable by `setup_bootstrap.py` *before* the runtime venv exists. Single source of truth for: runtime root, registry path, venv interpreter, Claude Desktop config path, Claude Code MCP config path. Resolution rules:
  - Runtime root: macOS `~/Library/Application Support/mcbrain-engine`, Windows `%LOCALAPPDATA%\mcbrain-engine` (fallback to `Path.home() / "AppData" / "Local"` if env var missing), Linux `~/.local/share/mcbrain-engine` (or `$XDG_DATA_HOME/mcbrain-engine` if set).
  - Registry path: macOS `~/Library/Application Support/mcbrain/vaults.json`, Windows `%APPDATA%\mcbrain\vaults.json`, Linux `~/.config/mcbrain/vaults.json` (or `$XDG_CONFIG_HOME/...`).
  - Venv interpreter: `Scripts\python.exe` on `os.name == "nt"`, else `bin/python`.
  - Claude Desktop config: macOS `~/Library/Application Support/Claude/claude_desktop_config.json`, Windows `%APPDATA%\Claude\claude_desktop_config.json`, Linux `~/.config/Claude/claude_desktop_config.json`.
  - Claude Code MCP config: `Path.home() / ".claude"` on all OSes.
  - Importable by both the engine and `setup_bootstrap.py` — neither needs an extra dep.
- `schema.sql` — unchanged from PR #4. Single SQLite schema (chunks + meta).
- `requirements.txt` — `mcp>=1.0.0`, `fastembed==0.8.0`, `numpy>=1.26,<3`. `mcp` is the only new addition over PR #4's pins. (`platformdirs` is intentionally not used; `paths.py` is stdlib-only — see above.)
- `setup_bootstrap.py` — **NEW** stdlib-only Python script invoked by the setup skill in lieu of Bash. Imports `paths.py` (stdlib-only) so it can run on system Python before the runtime venv exists. Handles: detect Python ≥3.10, detect/document `rg` install hints, create the runtime venv (`sys.executable -m venv`), `pip install` the requirements into the new venv (via `<venv-python> -m pip`), write/merge MCP entries into both Claude Code and Claude Desktop config files, call `migrate` for the new vault. Single code path for macOS, Windows, and Linux. Invoked via Claude Code's Bash tool — same invocation works whether the underlying shell is zsh, Git Bash, or WSL.
- `README.md` — runtime layout per OS, manual launch for debugging (`python -m mcbrain_engine`), where the registry lives, how to nuke and reinstall on each OS.

### NEW: Vault registry

- Path resolved via `paths.registry_path()` (stdlib-only). Concretely:
  - macOS: `~/Library/Application Support/mcbrain/vaults.json`
  - Windows: `%APPDATA%\mcbrain\vaults.json`
  - Linux: `~/.config/mcbrain/vaults.json` (honors `$XDG_CONFIG_HOME`)
- All path resolution goes through `paths.py`; no module hardcodes a literal.
- Schema:
  ```json
  {
    "version": 1,
    "vaults": {
      "mcbrain-ai-science": {
        "path": "/Users/<me>/Documents/mcbrain-ai-science",
        "registered": "2026-05-01T18:00:00Z"
      }
    }
  }
  ```
- Engine reads it on `query`/`index_*`/`list_vaults`. `migrate` writes/updates entries (idempotent). `uninstall` removes the entry.
- A small `registry.py` helper module inside `mcp-server/` handles read/write with atomic-rename semantics (write to `vaults.json.tmp`, fsync, rename) so concurrent invocations don't corrupt the file.

### NEW: `plugins/mcbrain/skills/mcbrain-ops/SKILL.md` (rewritten)

PR #4's `mcbrain-ops` is rewritten end-to-end. The current description ("the only place Python lives in McBrain") no longer applies — Python now lives in the MCP server, not a per-vault script. The skill becomes a thin maintenance frontend that calls MCP tools.

- Description updated: "indexing/maintenance lifecycle for the McBrain query engine. Calls `mcbrain-engine` MCP tools — `index_sync`, `index_rebuild`, `index_status`, `migrate`, `uninstall` — to manage a vault's search index. The `query` tool is called from the `mcbrain` skill via CLAUDE.md, not from here."
- Body documents:
  - Vault resolution: prefer the registry (call `list_vaults`); when the user says "this vault" mid-session, intersect `list_vaults` with the active filesystem MCP's `list_allowed_directories` to disambiguate.
  - Each subcommand mapped to its MCP tool call shape (no Python invocation snippets).
  - Removal of the per-vault venv mention from PR #4's description.
- The `references/` directory's `mcbrain_ops.py`, `schema.sql`, `requirements.txt`, and `gitignore-snippet.md` are deleted (their replacements live under `mcp-server/`).

### `plugins/mcbrain/skills/mcbrain-setup/SKILL.md`

Rework Step 5.5 and Step 8.5 to provision the global engine and register it with both Claude Desktop (Cowork) AND Claude Code, then call `migrate` for the new vault. **All platform branching lives inside `setup_bootstrap.py`; the SKILL.md itself is OS-neutral and just instructs Claude to run the bootstrap script.**

- **Step 5.5 (renamed: "Provision the McBrain query engine runtime, once-per-machine")**:
  1. Run `setup_bootstrap.py --check` from the plugin dir. The script:
     - Detects current Python ≥ 3.10 (uses `sys.version_info`; tolerates `python` vs `python3` since it's already running).
     - Detects `rg`. On Windows, if missing, prints `winget install BurntSushi.ripgrep.MSVC` (or `scoop install ripgrep`); on macOS, `brew install ripgrep`; on Linux, the appropriate package-manager hint. Setup blocks until the user installs it and re-runs.
     - Reports whether the runtime root (per `paths.runtime_root()`) already exists.
  2. If runtime already exists, skip the install (additional McBrain).
  3. Otherwise run `setup_bootstrap.py --install`: create the runtime dir, `python -m venv` (the bootstrap is itself running on a working Python interpreter, so `sys.executable -m venv` is the portable way), copy `mcp-server/{mcbrain_engine.py,schema.sql,registry.py,paths.py}` into the runtime dir, `pip install -r requirements.txt` into the new venv (using the venv's interpreter — no platform branching needed because pip is invoked as `<venv-python> -m pip`). FastEmbed manages its own cache internally on all OSes (we do not resolve or pass a cache path). Print a "first install — ~30 MB model download" notice.
- **Step 5.6 (NEW: "Register the `mcbrain-engine` MCP — both Code and Desktop")**:
  - Run `setup_bootstrap.py --register`. The script computes the venv interpreter path via `paths.venv_python(runtime_dir)`:
    - macOS/Linux: `<runtime>/venv/bin/python`
    - Windows: `<runtime>\venv\Scripts\python.exe`
  - Then merges this entry into both config files:
    ```json
    "mcbrain-engine": {
      "command": "<resolved venv python>",
      "args": ["<resolved engine script>"]
    }
    ```
  - **Claude Desktop / Cowork** config path:
    - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
    - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
    - Linux: `~/.config/Claude/claude_desktop_config.json`
  - **Claude Code** config: prefer `claude mcp add --scope user mcbrain-engine <python> <script>` (the CLI handles the right file on every OS). If `claude` isn't on PATH, fall back to writing the user-scope MCP config under `~/.claude/` directly — the path `Path.home() / ".claude"` works on all three OSes.
  - Both targets are idempotent — if `mcbrain-engine` is already registered with matching command+args, this is a no-op. **Treat this as first-class** — the failure mode where "second McBrain setup silently doesn't register the MCP in the second harness" is the most likely setup regression, and a Windows-vs-macOS path mismatch is the most likely *Windows-specific* setup regression.
- **Step 8.5 (rewritten: "Provision this vault's index via the MCP")**:
  - Call the `migrate` MCP tool with `vault_path=VAULT_PATH` and `vault_name=MCP_NAME`. The tool: creates `<vault>/.mcbrain/index.db`, registers the vault in the platform-resolved registry path, patches `<vault>/CLAUDE.md` with the new MCP-flavored Query operation and the `## Query engine` section (mode: `lexical+semantic (mcp)`), runs an initial `index_sync`, and (if a legacy PR #4 `.mcbrain/{venv,bin}/` exists) collapses it after preserving `index.db` if compatible.
  - Surface the migrate output to the user.
- **Step 3 / `.gitignore`**: drop `.mcbrain/venv/` and `.mcbrain/bin/` lines (they no longer exist post-pivot). Keep `.mcbrain/index.db` and the `*-wal`/`*-shm` lines. Don't track anything under `.mcbrain/` going forward — the engine source travels with the plugin install, not with the vault.

#### Note on `rg` cross-platform

PR #4's lexical layer shells out to `rg` (ripgrep). Ripgrep ships precompiled for Windows, macOS, and Linux, so the binary itself is fine — the only difference is install instructions. The engine resolves `rg` via `shutil.which("rg")`, which works on all three OSes. **No code change needed**; only the setup skill's user-facing install hint branches by `platform.system()`.

### `plugins/mcbrain/skills/mcbrain/SKILL.md`

- **Step 2.5 (Migration check)**: rewrite to detect three states by reading `CLAUDE.md`'s `## Query engine` section:
  1. **No `## Query engine` section** → vault predates the engine. Offer migration; on accept, call `migrate` MCP tool.
  2. **`mode: lexical+semantic` (PR #4 marker, no `(mcp)` suffix)** → vault was migrated to the per-vault layout. Offer "upgrade to MCP-based engine"; on accept, call `migrate` MCP tool — it preserves `index.db` if model+dim match and removes legacy `bin/venv/`.
  3. **`mode: lexical+semantic (mcp)`** → already on the new architecture. No action.
- The skill no longer shells out to Python; it calls MCP tools. Drop the `python3 <plugin_root>/...` snippet entirely.
- The "Architectural principle" footer stays (procedures live in CLAUDE.md, this skill is a router).
- Add a sentence to the migration prompt: "If the `mcbrain-engine` MCP isn't registered in this harness, I'll surface that and you'll need to re-run `mcbrain-setup` Step 5.6." This is the failure mode for "user installed McBrain in Code, then opened Cowork without re-registering."

### `plugins/mcbrain/skills/mcbrain-setup/references/claude-md-template.md`

The template Step 8.5's `migrate` patches into existing vaults, AND that fresh setup writes verbatim. Two changes:

1. **`## Operations → Query`**: rewrite to call the `mcbrain-engine` MCP's `query` tool, passing `vault_name` (or `vault_path` as fallback). Drop any reference to `mcbrain-ops query`. Mention that the tool returns ranked paths + scores as JSON.
2. **`## Query engine`** section: replace the PR #4 paragraph with the MCP-flavored version. The mode line becomes `mode: lexical+semantic (mcp)` so the patcher and skill can detect previously-migrated vaults. Drop "per-vault venv" language.
3. **General sync rule** (the catch-all "always sync after any wiki write" paragraph PR #4 added): keep, but rephrase to call the MCP tool.

### `mcbrain_engine.py` — tool surface

Seven tools. Argument shape favors caller convenience: `vault` accepts either a registry name (`mcbrain-ai-science`) or an absolute path. The engine resolves: name → registry lookup → path; path → canonicalize → registry reverse-lookup for the friendly name (used in log lines).

| Tool | Signature | Returns | Notes |
|---|---|---|---|
| `query` | `(vault: str, text: str, k: int = 8)` | `{query, k, mode, results: [{path, score, excerpt}]}` | Lifted directly from PR #4's `cmd_query`. Lazy-load embedder. |
| `index_sync` | `(vault: str)` | `{added: N, changed: N, removed: N, total: N, last_sync}` | Incremental. Lifted from `cmd_sync`. Returns structured JSON instead of stderr-only. |
| `index_rebuild` | `(vault: str)` | `{indexed: N, last_rebuild}` | Wipes + re-embeds. Lifted from `cmd_rebuild`. |
| `index_status` | `(vault: str)` | `{vault, index_path, provisioned, doc_count, last_sync, last_rebuild, embedding_model, embedding_dim, schema_version, index_size_bytes}` | Lifted from `cmd_status`. Lazy embedder NOT loaded. |
| `migrate` | `(vault_path: str, vault_name: str \| None = None)` | `{vault_name, vault_path, registry_updated: bool, claude_md_patched: bool, legacy_layout_removed: bool, initial_sync: {...}}` | Idempotent. Steps: (1) ensure `<vault>/.mcbrain/` exists, (2) if legacy `bin/venv/` present and `index.db` model+dim match — preserve `index.db`, remove `bin/venv/`; if mismatch — full rebuild, (3) write/update registry entry, (4) patch CLAUDE.md to MCP-mode markers, (5) run `index_sync`. |
| `uninstall` | `(vault: str, force: bool = False)` | `{removed_path, removed_from_registry, fastembed_cache_kept}` | Removes `<vault>/.mcbrain/` (still per-vault — this is the index DB, not a venv). Removes registry entry. Never touches `wiki/` or `raw/`. Dry-run by default. |
| `list_vaults` | `()` | `[{name, path, registered, provisioned}]` | Reads registry; checks each path for `.mcbrain/index.db` existence to populate `provisioned`. |

#### Vault resolution helper

```python
def resolve_vault(arg: str) -> tuple[str, Path]:
    """Returns (canonical_name, absolute_path).
    - If arg matches a registry key, returns its (name, path).
    - If arg is an absolute/expandable path that maps to a registry value,
      returns the canonical (name, path).
    - If arg is a path not in the registry, returns ('<unregistered>', path)
      — only valid for migrate; query/index_* require a registered vault.
    """
```

This isolates the "name vs path" branching so every tool handler is one line of `name, vault = resolve_vault(arg)`.

### Tests

- **Move + adapt PR #4 tests** to point at `mcbrain_engine.py` (same import shape; pure-function side preserved). All of `test_unit.py`, `test_index.py`, `test_search.py`, `test_patcher.py` should keep working with minor import path changes. `test_e2e.py` becomes the dispatcher-shape E2E (CLI mode is gone — replace with direct function calls; or repurpose as the wire test, see below).
- **NEW `test_registry.py`**: read/write/atomic-rename, idempotent put, missing-file creation, schema-version handling.
- **NEW `test_legacy_migrate.py`**: fabricate a fake PR #4 `<vault>/.mcbrain/{bin,venv}/` layout (no need to actually create a real venv — just empty dirs and a stub `index.db` with matching `meta` rows). Assert `migrate` collapses the layout, preserves `index.db`, updates the registry, and re-patches CLAUDE.md from PR #4's marker to the MCP marker.
- **NEW `test_e2e_stdio.py`**: spawn the server with `mcp.client.stdio.stdio_client()`. Round-trip each tool: `migrate` → `index_status` → `query` → `index_sync` (no-op) → `list_vaults` → `uninstall(force=True)`. One test, exercises the wire format. Skipped in CI environments without internet on first run (FastEmbed model download).
- **Tests README** update for the new module + the wire test how-to.

### `.claude-plugin/plugin.json` and `marketplace.json`

- Bump `version` to `2.0.0` (architecture change is breaking for users who customized their per-vault `bin/`).
- Update the description to mention the MCP-server architecture.

### `AGENTS.md` and `.gitignore`

- `.gitignore` (repo-level): add `~/.config/mcbrain/` is a user-home concern, not a repo concern — no change needed at repo level. The vault-level `.gitignore` change is described under `mcbrain-setup` Step 3 above.
- `AGENTS.md`: no functional change needed. (PR #4's AGENTS.md content stays.)

## Critical Files

| File | Status | Purpose |
|---|---|---|
| `plugins/mcbrain/mcp-server/mcbrain_engine.py` | **NEW** | The stdio MCP server entry point + tool handlers. ~600 LoC after lifting PR #4 logic. |
| `plugins/mcbrain/mcp-server/paths.py` | **NEW** | Cross-platform path resolver: runtime root, registry path, venv interpreter, Claude Desktop config path. ~60 LoC. Single source of truth for OS branching. |
| `plugins/mcbrain/mcp-server/registry.py` | **NEW** | Vault-registry I/O with atomic-rename semantics. ~80 LoC. Uses `paths.registry_path()`. |
| `plugins/mcbrain/mcp-server/schema.sql` | NEW (copy of PR #4 schema) | SQLite schema, unchanged. |
| `plugins/mcbrain/mcp-server/requirements.txt` | **NEW** | Adds `mcp>=1.0.0` to the PR #4 deps. (No `platformdirs`; `paths.py` is stdlib-only.) |
| `plugins/mcbrain/mcp-server/setup_bootstrap.py` | **NEW** | Cross-platform setup driver: `--check` (env diagnostics + install hints), `--install` (venv + deps + file copy), `--register` (write MCP entries to both Code and Desktop configs). All OS branching lives here, so the setup SKILL.md stays OS-neutral. ~200 LoC. |
| `plugins/mcbrain/mcp-server/README.md` | **NEW** | Runtime layout per OS (macOS/Windows/Linux), debug-launch instructions. |
| `plugins/mcbrain/skills/mcbrain-ops/SKILL.md` | **REWRITE** | Maintenance frontend that calls MCP tools. |
| `plugins/mcbrain/skills/mcbrain-ops/references/*` | **DELETE** | Replaced by `mcp-server/`. |
| `plugins/mcbrain/skills/mcbrain-setup/SKILL.md` | **MODIFY** | Step 5.5 (runtime install), Step 5.6 (MCP registration in both Code AND Desktop), Step 8.5 (call `migrate` MCP tool), Step 3 `.gitignore` slim-down. |
| `plugins/mcbrain/skills/mcbrain/SKILL.md` | **MODIFY** | Step 2.5 detects three states (no engine / PR #4 / MCP) and offers the right migration path. Drop Python shell-out. |
| `plugins/mcbrain/skills/mcbrain-setup/references/claude-md-template.md` | **MODIFY** | Query operation calls MCP tool. `## Query engine` mode marker becomes `lexical+semantic (mcp)`. |
| `plugins/mcbrain/.claude-plugin/plugin.json` | **MODIFY** | Version bump to 2.0.0; description update. |
| `.claude-plugin/marketplace.json` | **MODIFY** | Version bump; description update. |
| `tests/conftest.py` | **MODIFY** | Point `sys.path` at `mcp-server/` instead of the old skill references dir. |
| `tests/test_unit.py`, `test_index.py`, `test_search.py`, `test_patcher.py` | **MODIFY** | Import `mcbrain_engine` instead of `mcbrain_ops`. Drop CLI-specific tests in `test_e2e.py` (now superseded by stdio E2E). Patcher tests update to assert `(mcp)` marker. |
| `tests/test_registry.py` | **NEW** | Registry I/O coverage. |
| `tests/test_legacy_migrate.py` | **NEW** | PR #4 → MCP migration. |
| `tests/test_paths.py` | **NEW** | `paths.py` cross-platform resolution: mocks `os.name` to `"nt"` and `"posix"` and asserts venv-python, runtime root, registry path, and Claude Desktop config path are correct on both. The only place Windows behavior is exercised when running tests on macOS. |
| `tests/test_e2e_stdio.py` | **NEW** | Wire E2E across all seven tools. |
| `tests/README.md` | **MODIFY** | Document the wire test, the new module, and `mcp` SDK in the test venv. |
| `tests/requirements.txt` | **MODIFY** | Add `mcp` SDK to test deps. |

## Dependencies & Ordering

**Pre-implementation:**

0. **Rebase or merge `origin/main` into the issue branch.** PR #4 (commit `637f11b`) is on main but not on this worktree's base (`db04ca8`). Without this step, none of the "modify"/"rewrite" descriptions in this plan have files to act on. This is a hard prerequisite.

**Implementation order:**

1. **Engine module first** — write `mcbrain_engine.py` and `registry.py` with the tool handlers as plain Python functions. Keep them importable. Lift PR #4's pure logic verbatim where possible (search, RRF, embedding, schema, CLAUDE.md patcher). This is where the bulk of the unit/integration tests run green.
2. **MCP wire layer** — add `mcp` SDK decorators on top of the functions. `if __name__ == "__main__": mcp.run()`. Smoke-test by spawning manually with `python -m mcbrain_engine` and a quick `mcp.client.stdio` round-trip in a scratch file.
3. **Registry + migration** — implement the legacy-cleanup branch in `migrate`. Drive it via `test_legacy_migrate.py` (fabricated layouts, no real venvs).
4. **Setup skill rewrite** — Step 5.5 (runtime install), Step 5.6 (dual-target MCP registration), Step 8.5 (call `migrate`). Test by running `mcbrain-setup` against a scratch vault on the implementer's Mac.
5. **`mcbrain` skill rewrite** — Step 2.5 three-state detection. Drop Python shell-out.
6. **`mcbrain-ops` skill rewrite** — fresh SKILL.md that documents the maintenance frontend.
7. **CLAUDE.md template update** — Query operation, `## Query engine` section, sync rule.
8. **Tests pass** — port PR #4 tests, add new tests, write the wire E2E.
9. **Manual smoke** — both Claude Code and Cowork against a real vault. The Cowork pass is the entire reason this issue exists; do not skip it.
10. **Plugin version bump and README updates.**

Steps 1–2 must precede 3 (migrate uses registry). Steps 1–4 must precede 5–7 (skills assume the engine works). Step 8 can interleave with 1–7 in TDD style.

## Risks & Open Questions

- **MCP registration in two harnesses, on two operating systems.** Claude Code reads `~/.claude/mcp.json` (or `claude mcp add --scope user`); Claude Desktop/Cowork reads a different file *and* a different parent directory on each OS (see Step 5.6 for the three paths). The setup skill must idempotently update both targets on whichever OS it's running on. **Mitigation**: `paths.py` resolves all four file paths (Code-config × Desktop-config × macOS × Windows); `setup_bootstrap.py --register` writes both for the current OS. If a target file is unreadable (e.g., Cowork installed but never launched, so the config doesn't exist), the skill writes a fresh one with just the `mcbrain-engine` entry. The setup-skill prose stays OS-agnostic; only the bootstrap script branches.
- **Windows venv-interpreter path mismatch.** The most likely Windows-specific bug is writing `bin/python` into the MCP config on a Windows machine — Claude Desktop will then fail to launch the engine with no helpful error. **Mitigation**: `paths.venv_python()` is the single source of truth and is unit-tested with `os.name` mocked to `"nt"` and `"posix"`. The wire E2E test (`test_e2e_stdio.py`) implicitly catches this end-to-end on whatever OS CI/dev is running.
- **Registration drift across versions.** If the user re-installs the plugin and the runtime path changes (e.g., they relocate `~/.local/share`), the MCP entries in both harnesses go stale. **Mitigation**: `mcbrain-engine` runtime path is stable by convention; document that re-running `mcbrain-setup` Step 5.6 fixes it; surface this in the skill's failure messages.
- **stdio per-process embedder load on Cowork session start.** First `query` call pays ~500 ms model load. Acceptable. If this proves too slow in practice (e.g., user complaints about first-query latency), revisit the daemon option in a follow-up issue. Not a v1 concern.
- **Vault registry corruption.** Two concurrent `migrate` calls (e.g., user runs setup twice in parallel) could clobber the registry. **Mitigation**: atomic-rename writes (`vaults.json.tmp` → `vaults.json` via `os.replace`). Concurrency is rare enough not to warrant a real lockfile.
- **PR #4 vaults that have an out-of-date model.** If a user upgraded FastEmbed between PR #4 setup and this pivot, the stored embedding dim could mismatch. **Mitigation**: `migrate`'s legacy-cleanup branch checks `meta.embedding_model` and `meta.embedding_dim` against the engine's constants; if mismatch, it forces a `index_rebuild` (and tells the user). Tested via `test_legacy_migrate.py`.
- **Test runner needs `mcp` SDK installed.** The wire test imports `mcp.client.stdio.stdio_client` and the server imports `mcp.server.fastmcp` (or equivalent). `tests/requirements.txt` gets a new pin. **Mitigation**: documented in `tests/README.md`; `mcp` is a small pure-Python dep with no compiled extensions.
- **`AskUserQuestion` calls in skills can't be exercised in tests.** The setup skill is mostly user-facing prompts. **Mitigation**: PR #4 tests already accept this — the tests focus on the dispatcher logic, not the skill body. Same approach here: the skill body changes are reviewed by humans, not exercised by pytest.
- **Windows-specific test coverage.** The implementer's main machine is macOS, so Windows behavior is easy to break invisibly. **Mitigation**: (1) unit-test `paths.py` with `os.name` mocked to both `"nt"` and `"posix"` — covers venv-python, runtime root, registry path, and Claude Desktop config path; (2) the manual smoke checklist below has explicit Windows passes that block "done"; (3) `setup_bootstrap.py --check` prints a one-line OS/Python/`rg` summary so any environment mismatch surfaces immediately.
- **Path-with-spaces and JSON escaping on Windows.** Windows install paths often contain spaces (e.g., `C:\Users\First Last\AppData\...`) and the MCP config is JSON. **Mitigation**: `setup_bootstrap.py` uses `json.dump` (which handles backslash and quote escaping) instead of string-concatenating the config; never shells out to write the config file.
- **Bash tool availability on Windows.** Claude Code's Bash tool on Windows runs in Git Bash or WSL, neither of which is guaranteed. **Mitigation**: the setup skill defers the actual work to `setup_bootstrap.py` (pure Python). The skill's only Bash use is to invoke `python setup_bootstrap.py …`, which works in Git Bash, WSL, and `cmd.exe`. If the skill needs to fall back to `cmd.exe`, the invocation is the same single command.

## Verification

### Automated

- `pytest tests/ -v` passes — including all migrated PR #4 tests, new registry tests, new legacy-migrate tests, and the stdio wire E2E.
- The static test asserts every tool name appears in `mcbrain_engine.py` and `mcbrain-ops/SKILL.md` documents each one.
- Patcher tests assert the `(mcp)` marker is written and that re-running `migrate` on an MCP-migrated vault is a no-op.

### Manual smoke (Claude Code, macOS)

1. Fresh: clone repo, install plugin, run `mcbrain-setup` in a brand-new directory. Confirm `~/Library/Application Support/mcbrain-engine/{venv,mcbrain_engine.py,schema.sql}` are created. Confirm `~/Library/Application Support/mcbrain/vaults.json` has the new vault. Confirm both `~/.claude/` MCP config and `~/Library/Application Support/Claude/claude_desktop_config.json` have the `mcbrain-engine` entry, with `command` pointing at `<runtime>/venv/bin/python`.
2. Restart Claude Code. Run `mcbrain` skill against the new vault, ask a question. Confirm the engine responds via the MCP and lexical-only mode kicks in (vault is empty).
3. Ingest a couple of sources. Confirm `index_sync` is invoked automatically per CLAUDE.md's catch-all rule. Confirm subsequent queries return the new pages.
4. Run `mcbrain-setup` for a **second** McBrain (`mcbrain-finance`). Confirm the runtime install is skipped, the MCP entry is not duplicated, and the second vault appears in `list_vaults`.

### Manual smoke (Claude Code, Windows) — **deferred to follow-up Beads issue**

The Windows manual smoke is *not* required to close issue #11. Windows path resolution is covered by `test_paths.py` (mocking `os.name`) so we don't ship a design that's broken on Windows, but the real-machine pass waits for a Windows test environment to become available. The follow-up issue's checklist:

1. Fresh: install plugin on a Windows machine, run `mcbrain-setup` in a brand-new directory. Confirm `%LOCALAPPDATA%\mcbrain-engine\{venv,mcbrain_engine.py,schema.sql}` are created. Confirm `%APPDATA%\mcbrain\vaults.json` has the new vault. Confirm both the `~/.claude/` MCP config and `%APPDATA%\Claude\claude_desktop_config.json` have the `mcbrain-engine` entry, with `command` pointing at `<runtime>\venv\Scripts\python.exe` (note: `Scripts`, not `bin`; `python.exe`, not `python`).
2. Confirm `rg` was either auto-detected or the user got the `winget install BurntSushi.ripgrep.MSVC` hint.
3. Restart Claude Code. Run `mcbrain` against the new vault, ingest a source, query it — same flow as the macOS smoke.
4. Repeat in Windows Cowork.

### Manual smoke (Claude Cowork) — the load-bearing test

1. Open a Cowork session on macOS. Confirm `mcbrain-engine` MCP loads (visible in MCP debug panel).
2. Ask McBrain a question (`"ask my brain about X"`). Confirm `query` succeeds — this is the test that cannot be made to work in PR #4's design.
3. Ingest a new source via Cowork. Confirm `index_sync` runs and the new page is searchable.
4. With a second McBrain (set up in Code), open a Cowork session in *that* vault's chat. Confirm the same `mcbrain-engine` MCP serves it without any second registration.

(Windows Cowork pass: covered under the deferred Windows manual-smoke checklist above.)

### Migration smoke

1. Take a real PR #4-provisioned vault (one with `<vault>/.mcbrain/{bin,venv,index.db}`). Run the `mcbrain` skill in a Cowork session. Confirm Step 2.5 detects "PR #4 mode" and offers upgrade. On accept, confirm:
   - `<vault>/.mcbrain/bin/` and `venv/` are gone.
   - `<vault>/.mcbrain/index.db` is preserved.
   - `<vault>/CLAUDE.md` `## Query engine` line says `mode: lexical+semantic (mcp)`.
   - The vault appears in `list_vaults`.
   - Subsequent queries work.
2. Re-run `migrate` on the upgraded vault. Confirm it's a no-op (registry untouched, CLAUDE.md unchanged, no rebuild).

## Done When

All criteria in the issue's "Done when" section are met:

- [x] One `mcbrain-engine` MCP entry registered automatically by `mcbrain-setup` (idempotent across additional McBrains, in both Claude Code and Claude Desktop config).
- [x] All seven tools (`query`, `index_sync`, `index_rebuild`, `index_status`, `migrate`, `uninstall`, `list_vaults`) exposed and working end-to-end in Cowork against a real vault.
- [x] CLAUDE.md template updated to call MCP tools (not Bash/Python).
- [x] Existing PR #4 vaults migrate cleanly via `migrate` — idempotent, collapses `bin/venv/`, preserves `index.db` when compatible.
- [x] `mcbrain` and `mcbrain-ops` SKILL.md describe their responsibilities accurately. `mcbrain-ops` is no longer "the only place Python lives."
- [x] Test suite covers the new tool surface — port + new tests + one wire E2E + `test_paths.py` cross-platform unit coverage (Windows path resolution exercised via mocked `os.name`).
- [x] Manual macOS smoke passes for setup, ingest+auto-sync, query (both lexical-winner and pure-semantic-winner phrasings), second-McBrain reuse, and Cowork.
- [x] `setup_bootstrap.py` runs end-to-end on macOS; runtime is installed at the platform-specific data dir; both Claude Code and Claude Desktop configs receive the correct macOS venv-python path.

**Out of scope for v1 "done" (tracked as follow-up Beads issue):**

- [ ] Manual Windows smoke pass for Claude Code and Cowork. Windows code paths are present and unit-tested via mocked `os.name`, but a real-machine smoke pass is deferred to a follow-up issue once a Windows test environment is available.
