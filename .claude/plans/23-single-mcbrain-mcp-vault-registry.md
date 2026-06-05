# Plan: Single McBrain MCP — vault registry + file gateway (issue #23)

Worktree: `/Users/johndamask/code/23-single-mcbrain-mcp-vault-registry` (branch `23-single-mcbrain-mcp-vault-registry`)

## Context

Today every vault gets its own `@modelcontextprotocol/server-filesystem` entry in
`claude_desktop_config.json`, and the skills discover vaults by enumerating MCP server
names matching `mcbrain-*`. This couples discovery to MCP registration, forces a JSON
hand-edit on every vault creation, and the per-vault filesystem MCPs hold open handles
that race with git (the documented `.git/index.lock` rule that forbids Claude from
running git against the vault).

Issue #23 replaces all of that with **one `mcbrain` stdio MCP server** that is a vault
registry + path-scoped file gateway, backed by `~/.mcbrain/registry.json`.

## Recommended Approach

### Key design decisions

1. **Zero-dependency, single-file Node server** (`plugins/mcbrain/mcp-server/server.js`).
   Implements the MCP stdio protocol (JSON-RPC 2.0, newline-delimited: `initialize`,
   `notifications/initialized`, `ping`, `tools/list`, `tools/call`) by hand instead of
   using `@modelcontextprotocol/sdk`. Rationale: the plugin is distributed as a git
   marketplace checkout to non-technical Cowork users; requiring `npm install` inside
   the plugin cache is a setup landmine. Node.js is already the one hard requirement
   (it launched the filesystem MCPs via `npx`), so `node server.js` keeps requirements
   identical. A single file is also trivially copyable to the host (see #3).

2. **Registry file is the source of truth**, exactly as the issue specifies:
   ```json
   { "vaults": [ { "name": "mcbrain-finance", "path": "/Users/you/Documents/mcbrain-finance", "created": "2026-06-05T00:00:00Z" } ] }
   ```
   Default location `~/.mcbrain/registry.json`; overridable via `MCBRAIN_DIR` env var
   (needed for tests, harmless otherwise). The server writes it atomically
   (temp file in same dir + `fs.renameSync`). Claude Code skills may also read the file
   directly — it's plain JSON on purpose.

3. **Server is installed to `~/.mcbrain/mcp-server/server.js` for Claude Desktop.**
   The setup skill (running in Cowork) cannot know the host's plugin-cache path, but it
   *can* read its own plugin files and Write to a granted folder. So Step 5 of setup
   becomes: copy `server.js` from the plugin into `~/.mcbrain/mcp-server/` via the
   Write tool, then hand the user **one static JSON snippet** (identical for every
   vault, needed only once ever):
   ```json
   "mcbrain": { "command": "node", "args": ["<HOME>/.mcbrain/mcp-server/server.js"] }
   ```
   Re-running setup re-copies the file, which doubles as the upgrade path.

4. **Claude Code gets the MCP via the plugin itself**: new `plugins/mcbrain/.mcp.json`
   declaring `{"mcbrain": {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/mcp-server/server.js"]}}`
   so Claude Code users who install the plugin need no manual config at all.

5. **Migration runs host-side, inside the server.** The setup skill is (correctly)
   forbidden from reading `claude_desktop_config.json`, but the MCP server runs
   natively on the host. Add a `migrate_config` tool: scans the platform-appropriate
   `claude_desktop_config.json` for `mcbrain-*` entries whose args contain
   `@modelcontextprotocol/server-filesystem`, imports `{name, path}` pairs into the
   registry (skipping names already registered), and **returns** the list of legacy
   entries the user can now delete. It never edits the config file itself — the user
   deletes entries manually, same trust model as today.

6. **Setup registers vaults by writing the registry file directly** (Read existing →
   merge → Write via granted folder), not by calling `register_vault`. This avoids an
   ordering deadlock: the `mcbrain` MCP only loads after a Claude Desktop restart,
   which kills the setup conversation. The `register_vault` / `unregister_vault` tools
   still exist for chat-driven management afterwards.

7. **Git restriction relaxed, per surface.** The `.git/index.lock` rationale dies with
   the per-vault filesystem MCPs. New rule written into the setup template and the
   `mcbrain` skill:
   - **Claude Code**: Claude may run git directly against the vault (commit/push after
     operations, with user-visible messages).
   - **Cowork/Desktop**: still present copy-paste blocks — not because of lock races,
     but because Cowork's Bash sandbox cannot run processes on the host at all.
   Existing vaults keep their old CLAUDE.md text until the user updates it; the README
   migration section mentions the relaxation.

8. **Research-runner atomicity unchanged.** The file gateway deliberately does not grow
   lock/rename primitives. `local-research-runner`'s atomic JSONL protocol continues to
   require host-reachable file semantics (granted mount in Cowork, native FS in Claude
   Code). Only vault *discovery* changes in that skill.

### MCP server tool surface

Registry tools (all operate on `~/.mcbrain/registry.json`):

| Tool | Params | Behavior |
|---|---|---|
| `list_vaults` | — | Returns all vaults (name, path, created) |
| `get_vault` | `name` | Returns one vault or a not-found error |
| `register_vault` | `name`, `path` | Validates: name matches `^mcbrain(-[a-z0-9]+)*$`-ish slug, path is absolute, exists, is a directory; expands `~`; rejects duplicate names (and warns on duplicate paths); appends with ISO `created` timestamp |
| `unregister_vault` | `name` | Removes the entry (registry only — never touches files) |
| `migrate_config` | — | Scans `claude_desktop_config.json` (macOS `~/Library/Application Support/Claude/`, Windows `%APPDATA%\Claude\`), imports `mcbrain-*` filesystem-MCP entries, returns what was imported/skipped and which config entries are now deletable |

File-gateway tools (all take a `vault` name + vault-relative `path`):

| Tool | Params | Behavior |
|---|---|---|
| `read_file` | `vault`, `path` | Returns file contents (UTF-8) |
| `write_file` | `vault`, `path`, `content` | Creates parent dirs, writes whole file |
| `edit_file` | `vault`, `path`, `old`, `new` | Exact-match replace; errors if `old` is absent or matches more than once |
| `list_dir` | `vault`, `path` (default `"."`) | Lists entries with file/dir markers |

**Path-scoping (the security core)**, applied identically by every gateway tool:
1. Resolve the vault name via the registry; unknown vault → error.
2. Reject absolute incoming paths and any path containing a `..` segment up front.
3. `path.resolve(vaultRoot, relPath)`, then `fs.realpathSync` the deepest existing
   ancestor (so symlinks inside the vault pointing outside are caught), and require the
   result to equal the vault's realpath or start with it + `path.sep`.
4. Any violation returns an MCP tool error (`isError: true`) naming the vault root —
   never a filesystem error that leaks whether the outside path exists.

Tool errors generally return `isError: true` content rather than JSON-RPC protocol
errors, so the model can read and react to them.

## Changes

### 1. `plugins/mcbrain/mcp-server/server.js` (NEW — the bulk of the code)
- Zero-dep Node ≥ 18, single file, stdio JSON-RPC loop (readline over stdin,
  `console.log` JSON lines out; all logging to stderr).
- `initialize` echoes the client's requested `protocolVersion`, advertises
  `capabilities: { tools: {} }`, `serverInfo: { name: "mcbrain", version: "5.0.0" }`.
- Registry helpers: `loadRegistry()` (missing file → `{vaults: []}`; corrupt JSON →
  explicit error telling the user the path), `saveRegistry()` (mkdir -p `~/.mcbrain`,
  temp-file + rename).
- `resolveVaultPath(vaultName, relPath)` implementing the scoping algorithm above —
  one function, used by all four gateway tools, unit-tested directly via tool calls.
- The 9 tools above with full JSON Schema `inputSchema`s.

### 2. `plugins/mcbrain/mcp-server/test/server.test.js` (NEW)
- `node:test` + `node:assert`, zero deps, run with `node --test plugins/mcbrain/mcp-server/test/`.
- Test harness: spawn `server.js` with `MCBRAIN_DIR` pointed at a temp dir, speak
  JSON-RPC over stdio (initialize → tools/call), assert on results.
- **Registry CRUD**: empty-state list; register → list/get round-trip; duplicate-name
  rejection; relative/nonexistent path rejection; unregister; unregister unknown name;
  registry file survives valid-JSON round-trip; corrupt registry surfaces a clear error.
- **Gateway scoping**: read/write/edit/list happy paths inside a temp vault;
  reject `../escape`, nested `a/../../escape`, absolute paths, unknown vault name,
  symlink-inside-vault pointing outside (skipped on Windows); `edit_file` zero-match
  and multi-match errors; `write_file` creates parent dirs but still can't create
  parents outside the vault.
- **migrate_config**: point the scanner at a fixture config via an env override
  (`MCBRAIN_DESKTOP_CONFIG` for tests) containing two `mcbrain-*` filesystem servers
  and one unrelated server; assert both imported, unrelated ignored, second run is a
  no-op.

### 3. `plugins/mcbrain/.mcp.json` (NEW)
- Registers the server for Claude Code plugin installs via `${CLAUDE_PLUGIN_ROOT}`.

### 4. `plugins/mcbrain/skills/mcbrain-setup/SKILL.md` (MOD — biggest skill edit)
- Frontmatter description: "filesystem MCP config block" → "the single mcbrain
  registry MCP".
- "What this skill does" item 4 → "Installs the single mcbrain MCP server (once) and
  registers the vault in `~/.mcbrain/registry.json`".
- **Step 5 rewritten**: (a) request a grant for `~/` (or `~/.mcbrain/`), copy the
  plugin's `mcp-server/server.js` to `~/.mcbrain/mcp-server/server.js` with the Write
  tool; (b) Read/merge/Write `~/.mcbrain/registry.json` to add
  `{name: MCP_NAME, path: VAULT_PATH, created: <now>}` (create file if missing, never
  drop existing entries); (c) ask the user (AskUserQuestion) whether `claude_desktop_config.json`
  already has a `"mcbrain"` entry — if not, hand them the one static snippet
  (Version A/B pattern preserved, but the block is now vault-independent and this
  sub-step is skipped entirely on every subsequent vault); keep the "⛔ DO NOT edit
  this file yourself" rule.
- **New Step 5.5 (migration, first install only)**: tell the user that after the
  restart they can say "migrate my old McBrain vaults" → Claude calls `migrate_config`,
  reports imported vaults and which `mcbrain-*` config entries are safe to delete.
- **Step 7 (verify)** → new conversation prompt: *"Using the mcbrain MCP, list my
  vaults and read CLAUDE.md from MCP_NAME"* (exercises `list_vaults` + `read_file`).
- **Step 3 Backup/git section of the appended CLAUDE.md text**: replace the
  "Claude must not run git" block with the surface-dependent rule (decision #7);
  the recovery notes about `.git/index.lock` shrink to a one-line legacy mention.
- Step 9 wording: vault routing now goes through the registry, not MCP-name matching.

### 5. `plugins/mcbrain/skills/mcbrain/SKILL.md` (MOD)
- Step 1: resolve vaults via `list_vaults` (Desktop/Cowork) or by reading
  `~/.mcbrain/registry.json` (Claude Code); fuzzy-match the user's vault mention against
  registered names; ambiguity → ask, listing registered vault names.
- Step 2: read `CLAUDE.md` via `read_file(vault, "CLAUDE.md")` (Desktop), native Read
  at `<registry path>/CLAUDE.md` (Claude Code), or granted mount (Cowork). Drop the
  "MCP root is the vault" framing.
- "Backup and version control": surface-dependent git rule per decision #7.

### 6. `plugins/mcbrain/skills/local-research-db/SKILL.md` (MOD)
- Prerequisites + "Identifying the McBrain vault": replace MCP-name enumeration with
  `list_vaults` / registry read; keep the zero/one/many disambiguation logic verbatim,
  just sourced from the registry. Operate on the vault's absolute registry path
  (via gateway tools in Desktop, native/mount tools elsewhere) instead of "MCP root".

### 7. `plugins/mcbrain/skills/local-research-runner/SKILL.md` (MOD)
- Prerequisites: "registered in `~/.mcbrain/registry.json` (visible via `list_vaults`)"
  replaces "exposed as a filesystem MCP server named mcbrain-<topic>".
- `<vault>` paths now mean the absolute path from the registry. Explicit note: the
  atomic JSONL protocol still requires host file semantics (granted mount / native FS);
  the mcbrain gateway tools are not used for the lock-and-replace cycle.

### 8. `plugins/mcbrain/skills/mcbrain-setup/references/claude-md-template.md` (MOD, light)
- "What lives where" / plumbing mentions: "the MCP path" → "the registry entry".
  No structural changes.

### 9. `README.md` (MOD)
- Requirements: Node.js now runs the bundled `mcbrain` MCP server (no `npx` package
  download at launch).
- New section "One MCP server for all vaults": registry concept, the 9 tools in a
  sentence, `~/.mcbrain/` layout.
- **Upgrading to 5.0.0** callout (matching the existing 4.0.0 callout style): install
  the mcbrain MCP via re-running setup or the snippet, run `migrate_config`, delete
  per-vault `mcbrain-*` entries; git-restriction relaxation note.
- Repo layout tree: add `mcp-server/`, `commands/`, `.mcp.json`, `tests/`.
- Running tests: add the `node --test` command.

### 10. Version bumps + test docs (MOD)
- `plugins/mcbrain/.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`:
  `4.0.0` → `5.0.0` (breaking change to vault discovery).
- `tests/README.md`: add the Node test suite row and run command.
- Existing `tests/test_local_research.py` must keep passing — its SKILL.md
  field-mention assertions are unaffected by the discovery rewording (verify after
  editing the two research SKILL.md files).

## Critical Files

| File | Action |
|---|---|
| `plugins/mcbrain/mcp-server/server.js` | NEW — registry + gateway MCP server |
| `plugins/mcbrain/mcp-server/test/server.test.js` | NEW — node:test suite |
| `plugins/mcbrain/.mcp.json` | NEW — Claude Code plugin MCP registration |
| `plugins/mcbrain/skills/mcbrain-setup/SKILL.md` | MOD — Steps 5/5.5/7, git rule, frontmatter |
| `plugins/mcbrain/skills/mcbrain/SKILL.md` | MOD — registry discovery, git rule |
| `plugins/mcbrain/skills/local-research-db/SKILL.md` | MOD — registry discovery |
| `plugins/mcbrain/skills/local-research-runner/SKILL.md` | MOD — registry discovery, path note |
| `plugins/mcbrain/skills/mcbrain-setup/references/claude-md-template.md` | MOD — light wording |
| `README.md` | MOD — requirements, 5.0.0 upgrade callout, layout, tests |
| `plugins/mcbrain/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` | MOD — 5.0.0 |
| `tests/README.md` | MOD — node test instructions |

## Dependencies & Ordering

1. `server.js` first (everything references its tool names/semantics).
2. Server tests — lock the contract before touching docs/skills.
3. `.mcp.json`, then the four SKILL.md edits (setup first; the other three reference
   the discovery pattern it establishes).
4. Template wording, README, version bumps, tests/README.
5. Run both suites: `node --test plugins/mcbrain/mcp-server/test/` and the existing
   pytest suite.

## Risks & Open Questions

- **Hand-rolled MCP protocol**: small risk of protocol drift vs. the official SDK.
  Mitigated by the narrow surface (tools only, stdio only) and by integration tests
  that speak real JSON-RPC to the spawned server. Alternative (rejected): depend on
  `@modelcontextprotocol/sdk`, which would force an `npm install` step on
  non-technical users or vendored `node_modules` in the repo.
- **Cowork ↔ host divergence of `server.js`**: the copy in `~/.mcbrain/mcp-server/`
  can go stale relative to the plugin. Mitigation: setup always re-copies; server
  reports its version in `serverInfo` and `list_vaults` output can include it.
- **Registry write races** (setup's Read-merge-Write vs. server's atomic write):
  acceptable for a single-user tool; documented. The server side is atomic.
- **`node` on PATH for GUI-launched Claude Desktop**: same constraint as the current
  `npx` approach — no regression, but worth keeping the Step 7 troubleshooting note.
- **Cowork plugin MCP support**: `.mcp.json` is a Claude Code mechanism; Desktop still
  needs the `claude_desktop_config.json` entry. The plan treats Desktop as the primary
  documented path. Verify `${CLAUDE_PLUGIN_ROOT}` behavior in Claude Code during
  implementation.
- **Old vaults' CLAUDE.md** still contain the "Claude must not run git" rule; the
  README upgrade note tells users they can ask Claude to refresh that section, but no
  automated rewrite is attempted (vault files are user data).

## Verification

1. `node --test plugins/mcbrain/mcp-server/test/` — all registry CRUD, path-scoping,
   and migration tests green.
2. `.test-venv/bin/python -m pytest tests/ -v` — existing suite still green.
3. Manual smoke: `echo` an `initialize` + `tools/list` JSON-RPC pair into
   `node plugins/mcbrain/mcp-server/server.js` with `MCBRAIN_DIR=$(mktemp -d)` and
   confirm 9 tools listed.
4. Grep check: no remaining instances of `server-filesystem`, "enumerate connected
   MCPs", or `mcbrain-<topic>` MCP-name discovery in the four SKILL.md files (except
   intentional migration/legacy mentions).
5. (Post-merge, human) End-to-end: fresh `mcbrain-setup` run in Cowork → one config
   entry → restart → `list_vaults` shows the vault → `read_file` returns CLAUDE.md →
   `migrate_config` imports a legacy vault.
