#!/usr/bin/env node
/**
 * mcbrain — single MCP server for all McBrain vaults.
 *
 * Vault registry + path-scoped file gateway, backed by ~/.mcbrain/registry.json.
 * Zero dependencies: hand-rolled MCP stdio protocol (JSON-RPC 2.0, newline-delimited).
 * Requires Node >= 18.
 *
 * Env overrides (used by tests; harmless otherwise):
 *   MCBRAIN_DIR            — registry directory (default ~/.mcbrain)
 *   MCBRAIN_DESKTOP_CONFIG — claude_desktop_config.json path for migrate_config
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const readline = require('readline');

const SERVER_NAME = 'mcbrain';
const SERVER_VERSION = '5.0.0';
const VAULT_NAME_RE = /^mcbrain(-[a-z0-9]+)*$/;

// ---------------------------------------------------------------------------
// Registry persistence
// ---------------------------------------------------------------------------

function registryDir() {
  return process.env.MCBRAIN_DIR || path.join(os.homedir(), '.mcbrain');
}

function registryPath() {
  return path.join(registryDir(), 'registry.json');
}

function loadRegistry() {
  const file = registryPath();
  let raw;
  try {
    raw = fs.readFileSync(file, 'utf8');
  } catch (err) {
    if (err.code === 'ENOENT') return { vaults: [] };
    throw err;
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error(
      `Registry file at ${file} is not valid JSON. ` +
      `Fix or delete it (deleting loses vault registrations, not vault files).`
    );
  }
  if (!parsed || !Array.isArray(parsed.vaults)) {
    throw new Error(`Registry file at ${file} is malformed: expected {"vaults": [...]}.`);
  }
  return parsed;
}

function saveRegistry(registry) {
  const dir = registryDir();
  fs.mkdirSync(dir, { recursive: true });
  const file = registryPath();
  const tmp = path.join(dir, `.registry-${process.pid}.tmp`);
  fs.writeFileSync(tmp, JSON.stringify(registry, null, 2) + '\n', 'utf8');
  fs.renameSync(tmp, file); // atomic on the same filesystem
}

// ---------------------------------------------------------------------------
// Tool result helpers
// ---------------------------------------------------------------------------

function textResult(text) {
  return { content: [{ type: 'text', text }] };
}

function errorResult(text) {
  return { content: [{ type: 'text', text }], isError: true };
}

function expandHome(p) {
  if (p === '~') return os.homedir();
  if (p.startsWith('~/') || p.startsWith('~\\')) {
    return path.join(os.homedir(), p.slice(2));
  }
  return p;
}

// ---------------------------------------------------------------------------
// Path scoping — the security core. Every gateway tool funnels through this.
// ---------------------------------------------------------------------------

/**
 * Resolve a vault-relative path to an absolute host path, or throw ScopeError.
 * Refusal messages name only the vault root — never the resolved outside path,
 * and never a raw fs error that leaks whether the outside target exists.
 */
class ScopeError extends Error {}

function resolveVaultPath(vaultName, relPath) {
  const registry = loadRegistry();
  const vault = registry.vaults.find((v) => v.name === vaultName);
  if (!vault) {
    const names = registry.vaults.map((v) => v.name).join(', ') || '(none)';
    throw new ScopeError(`Unknown vault "${vaultName}". Registered vaults: ${names}`);
  }

  const refusal = () =>
    new ScopeError(
      `Path refused: must stay inside the vault root ${vault.path} ` +
      `(no absolute paths, no ".." segments, no symlinks escaping the vault).`
    );

  if (typeof relPath !== 'string' || relPath.length === 0) relPath = '.';
  // Reject absolute paths and any ".." segment before touching the filesystem.
  if (path.isAbsolute(relPath) || /^[a-zA-Z]:[\\/]/.test(relPath)) throw refusal();
  if (relPath.split(/[\\/]+/).includes('..')) throw refusal();

  let vaultReal;
  try {
    vaultReal = fs.realpathSync(vault.path);
  } catch {
    throw new ScopeError(
      `Vault "${vaultName}" root ${vault.path} does not exist or is unreadable. ` +
      `Re-check the registry entry.`
    );
  }

  const resolved = path.resolve(vaultReal, relPath);

  // Realpath the deepest existing ancestor so symlinks inside the vault that
  // point outside are caught even when the final component doesn't exist yet.
  let probe = resolved;
  while (!fs.existsSync(probe)) {
    const parent = path.dirname(probe);
    if (parent === probe) break;
    probe = parent;
  }
  let probeReal;
  try {
    probeReal = fs.realpathSync(probe);
  } catch {
    throw refusal();
  }
  if (probeReal !== vaultReal && !probeReal.startsWith(vaultReal + path.sep)) {
    throw refusal();
  }

  return { absPath: resolved, vaultRoot: vaultReal };
}

// ---------------------------------------------------------------------------
// Tool implementations
// ---------------------------------------------------------------------------

function toolListVaults() {
  const registry = loadRegistry();
  if (registry.vaults.length === 0) {
    return textResult(
      'No vaults registered. Use register_vault to add one, or migrate_config ' +
      'to import legacy per-vault filesystem MCP entries.'
    );
  }
  return textResult(JSON.stringify({ vaults: registry.vaults }, null, 2));
}

function toolGetVault({ name }) {
  const registry = loadRegistry();
  const vault = registry.vaults.find((v) => v.name === name);
  if (!vault) {
    const names = registry.vaults.map((v) => v.name).join(', ') || '(none)';
    return errorResult(`No vault named "${name}". Registered vaults: ${names}`);
  }
  return textResult(JSON.stringify(vault, null, 2));
}

function toolRegisterVault({ name, path: vaultPath }) {
  if (typeof name !== 'string' || !VAULT_NAME_RE.test(name)) {
    return errorResult(
      `Invalid vault name "${name}". Names must be lowercase slugs starting with ` +
      `"mcbrain", e.g. "mcbrain" or "mcbrain-finance".`
    );
  }
  if (typeof vaultPath !== 'string' || vaultPath.length === 0) {
    return errorResult('A vault path is required.');
  }
  const expanded = expandHome(vaultPath);
  if (!path.isAbsolute(expanded)) {
    return errorResult(`Vault path must be absolute (got "${vaultPath}").`);
  }
  let stat;
  try {
    stat = fs.statSync(expanded);
  } catch {
    return errorResult(`Vault path ${expanded} does not exist.`);
  }
  if (!stat.isDirectory()) {
    return errorResult(`Vault path ${expanded} is not a directory.`);
  }

  const registry = loadRegistry();
  if (registry.vaults.some((v) => v.name === name)) {
    return errorResult(
      `A vault named "${name}" is already registered. ` +
      `Use unregister_vault first if you want to re-point it.`
    );
  }
  const samePath = registry.vaults.filter((v) => path.resolve(v.path) === path.resolve(expanded));
  registry.vaults.push({ name, path: expanded, created: new Date().toISOString() });
  saveRegistry(registry);

  let msg = `Registered vault "${name}" at ${expanded}.`;
  if (samePath.length > 0) {
    msg += ` Warning: this path is already used by vault(s) ${samePath.map((v) => `"${v.name}"`).join(', ')}.`;
  }
  return textResult(msg);
}

function toolUnregisterVault({ name }) {
  const registry = loadRegistry();
  const idx = registry.vaults.findIndex((v) => v.name === name);
  if (idx === -1) {
    const names = registry.vaults.map((v) => v.name).join(', ') || '(none)';
    return errorResult(`No vault named "${name}". Registered vaults: ${names}`);
  }
  const [removed] = registry.vaults.splice(idx, 1);
  saveRegistry(registry);
  return textResult(
    `Unregistered vault "${name}" (was ${removed.path}). ` +
    `The vault's files were not touched.`
  );
}

function toolReadFile({ vault, path: relPath }) {
  const { absPath } = resolveVaultPath(vault, relPath);
  let content;
  try {
    content = fs.readFileSync(absPath, 'utf8');
  } catch (err) {
    if (err.code === 'ENOENT') return errorResult(`File not found in vault "${vault}": ${relPath}`);
    if (err.code === 'EISDIR') return errorResult(`"${relPath}" is a directory — use list_dir.`);
    return errorResult(`Could not read "${relPath}" in vault "${vault}": ${err.code || err.message}`);
  }
  return textResult(content);
}

function toolWriteFile({ vault, path: relPath, content }) {
  if (typeof content !== 'string') return errorResult('write_file requires string content.');
  const { absPath } = resolveVaultPath(vault, relPath);
  // Parent creation is safe: resolveVaultPath already proved the deepest
  // existing ancestor realpaths inside the vault, so every dir created here
  // is inside it too.
  fs.mkdirSync(path.dirname(absPath), { recursive: true });
  fs.writeFileSync(absPath, content, 'utf8');
  return textResult(`Wrote ${Buffer.byteLength(content, 'utf8')} bytes to ${relPath} in vault "${vault}".`);
}

function toolEditFile({ vault, path: relPath, old, new: replacement }) {
  if (typeof old !== 'string' || old.length === 0) return errorResult('edit_file requires a non-empty "old" string.');
  if (typeof replacement !== 'string') return errorResult('edit_file requires a "new" string.');
  const { absPath } = resolveVaultPath(vault, relPath);
  let content;
  try {
    content = fs.readFileSync(absPath, 'utf8');
  } catch (err) {
    if (err.code === 'ENOENT') return errorResult(`File not found in vault "${vault}": ${relPath}`);
    return errorResult(`Could not read "${relPath}" in vault "${vault}": ${err.code || err.message}`);
  }
  const count = content.split(old).length - 1;
  if (count === 0) {
    return errorResult(`edit_file: "old" string not found in ${relPath} (0 matches).`);
  }
  if (count > 1) {
    return errorResult(
      `edit_file: "old" string matches ${count} times in ${relPath}; ` +
      `it must match exactly once. Add surrounding context to disambiguate.`
    );
  }
  fs.writeFileSync(absPath, content.replace(old, replacement), 'utf8');
  return textResult(`Edited ${relPath} in vault "${vault}" (1 replacement).`);
}

function toolListDir({ vault, path: relPath = '.' }) {
  const { absPath } = resolveVaultPath(vault, relPath);
  let entries;
  try {
    entries = fs.readdirSync(absPath, { withFileTypes: true });
  } catch (err) {
    if (err.code === 'ENOENT') return errorResult(`Directory not found in vault "${vault}": ${relPath}`);
    if (err.code === 'ENOTDIR') return errorResult(`"${relPath}" is a file — use read_file.`);
    return errorResult(`Could not list "${relPath}" in vault "${vault}": ${err.code || err.message}`);
  }
  const lines = entries
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((e) => (e.isDirectory() ? `${e.name}/` : e.name));
  return textResult(lines.length ? lines.join('\n') : '(empty directory)');
}

// --- migrate_config ---------------------------------------------------------

function desktopConfigPath() {
  if (process.env.MCBRAIN_DESKTOP_CONFIG) return process.env.MCBRAIN_DESKTOP_CONFIG;
  switch (process.platform) {
    case 'darwin':
      return path.join(os.homedir(), 'Library', 'Application Support', 'Claude', 'claude_desktop_config.json');
    case 'win32':
      return path.join(process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming'), 'Claude', 'claude_desktop_config.json');
    default:
      return path.join(os.homedir(), '.config', 'Claude', 'claude_desktop_config.json');
  }
}

function toolMigrateConfig() {
  const configFile = desktopConfigPath();
  let raw;
  try {
    raw = fs.readFileSync(configFile, 'utf8'); // read-only — never written
  } catch {
    return textResult(`No Claude Desktop config found at ${configFile} — nothing to migrate.`);
  }
  let config;
  try {
    config = JSON.parse(raw);
  } catch {
    return errorResult(`Claude Desktop config at ${configFile} is not valid JSON; cannot scan it.`);
  }

  const servers = (config && config.mcpServers) || {};
  const legacy = [];
  for (const [name, entry] of Object.entries(servers)) {
    if (!/^mcbrain-/.test(name)) continue;
    const args = (entry && Array.isArray(entry.args)) ? entry.args : [];
    if (!args.some((a) => typeof a === 'string' && a.includes('@modelcontextprotocol/server-filesystem'))) continue;
    // The vault path is the last filesystem-path-looking argument.
    const pathsInArgs = args.filter(
      (a) => typeof a === 'string' && (path.isAbsolute(a) || /^[a-zA-Z]:[\\/]/.test(a))
    );
    if (pathsInArgs.length === 0) continue;
    legacy.push({ name, path: pathsInArgs[pathsInArgs.length - 1] });
  }

  if (legacy.length === 0) {
    return textResult(`No legacy mcbrain-* filesystem MCP entries found in ${configFile}.`);
  }

  const registry = loadRegistry();
  const imported = [];
  const skipped = [];
  for (const { name, path: vaultPath } of legacy) {
    if (registry.vaults.some((v) => v.name === name)) {
      skipped.push(name);
      continue;
    }
    registry.vaults.push({ name, path: vaultPath, created: new Date().toISOString() });
    imported.push(name);
  }
  if (imported.length > 0) saveRegistry(registry);

  const lines = [];
  lines.push(
    imported.length > 0
      ? `Imported ${imported.length} vault(s) into the registry: ${imported.join(', ')}.`
      : 'Nothing new to import — all legacy vaults are already registered.'
  );
  if (skipped.length > 0) {
    lines.push(`Skipped (already registered): ${skipped.join(', ')}.`);
  }
  lines.push('');
  lines.push(`This tool never edits ${configFile}. You can now delete these "mcpServers" entries by hand:`);
  for (const { name } of legacy) lines.push(`  - "${name}"`);
  lines.push('Keep the single "mcbrain" entry — that is this server.');
  return textResult(lines.join('\n'));
}

// ---------------------------------------------------------------------------
// Tool registry (name -> { description, inputSchema, handler })
// ---------------------------------------------------------------------------

const vaultPathProps = {
  vault: { type: 'string', description: 'Registered vault name, e.g. "mcbrain-finance"' },
  path: { type: 'string', description: 'Vault-relative path (no absolute paths, no "..")' },
};

const TOOLS = {
  list_vaults: {
    description: 'List all registered McBrain vaults (name, path, created).',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: toolListVaults,
  },
  get_vault: {
    description: 'Get one registered vault by name.',
    inputSchema: {
      type: 'object',
      properties: { name: { type: 'string', description: 'Vault name' } },
      required: ['name'],
      additionalProperties: false,
    },
    handler: toolGetVault,
  },
  register_vault: {
    description: 'Register a McBrain vault (name + absolute path) in ~/.mcbrain/registry.json.',
    inputSchema: {
      type: 'object',
      properties: {
        name: { type: 'string', description: 'Slug like "mcbrain-finance"' },
        path: { type: 'string', description: 'Absolute path to the vault directory (~ allowed)' },
      },
      required: ['name', 'path'],
      additionalProperties: false,
    },
    handler: toolRegisterVault,
  },
  unregister_vault: {
    description: 'Remove a vault from the registry. Never touches the vault\'s files.',
    inputSchema: {
      type: 'object',
      properties: { name: { type: 'string', description: 'Vault name' } },
      required: ['name'],
      additionalProperties: false,
    },
    handler: toolUnregisterVault,
  },
  migrate_config: {
    description:
      'Import legacy per-vault mcbrain-* filesystem MCP entries from claude_desktop_config.json ' +
      'into the registry. Read-only on the config; reports which entries you can delete.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: toolMigrateConfig,
  },
  read_file: {
    description: 'Read a UTF-8 file from a registered vault.',
    inputSchema: {
      type: 'object',
      properties: vaultPathProps,
      required: ['vault', 'path'],
      additionalProperties: false,
    },
    handler: toolReadFile,
  },
  write_file: {
    description: 'Write a whole UTF-8 file into a registered vault (creates parent dirs).',
    inputSchema: {
      type: 'object',
      properties: { ...vaultPathProps, content: { type: 'string', description: 'Full file content' } },
      required: ['vault', 'path', 'content'],
      additionalProperties: false,
    },
    handler: toolWriteFile,
  },
  edit_file: {
    description: 'Exact-match string replace in a vault file. "old" must match exactly once.',
    inputSchema: {
      type: 'object',
      properties: {
        ...vaultPathProps,
        old: { type: 'string', description: 'Exact string to replace (must occur exactly once)' },
        new: { type: 'string', description: 'Replacement string' },
      },
      required: ['vault', 'path', 'old', 'new'],
      additionalProperties: false,
    },
    handler: toolEditFile,
  },
  list_dir: {
    description: 'List a directory in a registered vault. Directories end with "/".',
    inputSchema: {
      type: 'object',
      properties: { ...vaultPathProps },
      required: ['vault'],
      additionalProperties: false,
    },
    handler: toolListDir,
  },
};

// ---------------------------------------------------------------------------
// JSON-RPC 2.0 stdio loop (newline-delimited)
// ---------------------------------------------------------------------------

function send(msg) {
  process.stdout.write(JSON.stringify(msg) + '\n');
}

function sendResult(id, result) {
  send({ jsonrpc: '2.0', id, result });
}

function sendError(id, code, message) {
  send({ jsonrpc: '2.0', id, error: { code, message } });
}

function handleRequest(req) {
  const { id, method, params } = req;
  const isNotification = id === undefined || id === null;

  switch (method) {
    case 'initialize':
      sendResult(id, {
        protocolVersion: (params && params.protocolVersion) || '2024-11-05',
        capabilities: { tools: {} },
        serverInfo: { name: SERVER_NAME, version: SERVER_VERSION },
      });
      return;
    case 'notifications/initialized':
    case 'notifications/cancelled':
      return; // notifications get no response
    case 'ping':
      sendResult(id, {});
      return;
    case 'tools/list':
      sendResult(id, {
        tools: Object.entries(TOOLS).map(([name, t]) => ({
          name,
          description: t.description,
          inputSchema: t.inputSchema,
        })),
      });
      return;
    case 'tools/call': {
      const toolName = params && params.name;
      const tool = TOOLS[toolName];
      if (!tool) {
        sendResult(id, errorResult(`Unknown tool "${toolName}".`));
        return;
      }
      let result;
      try {
        result = tool.handler(params.arguments || {});
      } catch (err) {
        result = errorResult(err instanceof ScopeError ? err.message : `${toolName} failed: ${err.message}`);
      }
      sendResult(id, result);
      return;
    }
    default:
      if (!isNotification) sendError(id, -32601, `Method not found: ${method}`);
  }
}

function main() {
  const rl = readline.createInterface({ input: process.stdin, terminal: false });
  rl.on('line', (line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    let req;
    try {
      req = JSON.parse(trimmed);
    } catch {
      sendError(null, -32700, 'Parse error');
      return;
    }
    try {
      handleRequest(req);
    } catch (err) {
      process.stderr.write(`mcbrain: unhandled error: ${err.stack || err}\n`);
      if (req.id !== undefined && req.id !== null) {
        sendError(req.id, -32603, 'Internal error');
      }
    }
  });
  rl.on('close', () => process.exit(0));
  process.stderr.write(`mcbrain MCP server v${SERVER_VERSION} listening on stdio (registry: ${registryPath()})\n`);
}

main();
