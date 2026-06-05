'use strict';
/**
 * Integration tests for the mcbrain MCP server.
 *
 * Zero deps: node:test + node:assert. Each test spawns server.js as a child
 * process with MCBRAIN_DIR (and, for migration tests, MCBRAIN_DESKTOP_CONFIG)
 * pointed at fresh temp dirs, and speaks real newline-delimited JSON-RPC over
 * stdio. The real ~/.mcbrain and Desktop config are never touched.
 *
 * Run: node --test plugins/mcbrain/mcp-server/test/
 */

const { test } = require('node:test');
const assert = require('node:assert');
const { spawn } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const SERVER = path.join(__dirname, '..', 'server.js');

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

class McbrainClient {
  constructor(env = {}) {
    assert.ok(env.MCBRAIN_DIR, 'tests must always set MCBRAIN_DIR');
    this.proc = spawn(process.execPath, [SERVER], {
      env: {
        ...process.env,
        MCBRAIN_DESKTOP_CONFIG: '/nonexistent/claude_desktop_config.json', // never the real one
        ...env,
      },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    this.nextId = 1;
    this.pending = new Map();
    this.buffer = '';
    this.proc.stdout.on('data', (chunk) => {
      this.buffer += chunk.toString('utf8');
      let nl;
      while ((nl = this.buffer.indexOf('\n')) !== -1) {
        const line = this.buffer.slice(0, nl);
        this.buffer = this.buffer.slice(nl + 1);
        if (!line.trim()) continue;
        const msg = JSON.parse(line);
        const resolver = this.pending.get(msg.id);
        if (resolver) {
          this.pending.delete(msg.id);
          resolver(msg);
        }
      }
    });
  }

  request(method, params) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`timeout waiting for ${method}`)), 5000);
      this.pending.set(id, (msg) => {
        clearTimeout(timer);
        resolve(msg);
      });
      this.proc.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
    });
  }

  notify(method, params) {
    this.proc.stdin.write(JSON.stringify({ jsonrpc: '2.0', method, params }) + '\n');
  }

  async initialize() {
    const res = await this.request('initialize', {
      protocolVersion: '2025-03-26',
      capabilities: {},
      clientInfo: { name: 'mcbrain-tests', version: '0.0.0' },
    });
    this.notify('notifications/initialized');
    return res;
  }

  /** Call a tool; returns {text, isError} from the result content. */
  async call(name, args = {}) {
    const res = await this.request('tools/call', { name, arguments: args });
    assert.ok(res.result, `tools/call ${name} returned no result: ${JSON.stringify(res)}`);
    const text = (res.result.content || []).map((c) => c.text).join('\n');
    return { text, isError: res.result.isError === true };
  }

  close() {
    this.proc.stdin.end();
    this.proc.kill();
  }
}

/** Spin up a client with a fresh registry dir; auto-cleanup via t.after. */
function freshClient(t, extraEnv = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mcbrain-test-'));
  const client = new McbrainClient({ MCBRAIN_DIR: dir, ...extraEnv });
  t.after(() => {
    client.close();
    fs.rmSync(dir, { recursive: true, force: true });
  });
  return { client, dir };
}

function makeVaultDir(t, name = 'vault') {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `mcbrain-${name}-`));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return fs.realpathSync(dir); // macOS /var -> /private/var
}

// ---------------------------------------------------------------------------
// Protocol
// ---------------------------------------------------------------------------

test('initialize echoes protocolVersion and names the server', async (t) => {
  const { client } = freshClient(t);
  const res = await client.initialize();
  assert.equal(res.result.protocolVersion, '2025-03-26');
  assert.equal(res.result.serverInfo.name, 'mcbrain');
  assert.deepEqual(res.result.capabilities, { tools: {} });
});

test('tools/list returns exactly 9 tools, each with an inputSchema', async (t) => {
  const { client } = freshClient(t);
  await client.initialize();
  const res = await client.request('tools/list');
  const tools = res.result.tools;
  assert.equal(tools.length, 9);
  const names = tools.map((x) => x.name).sort();
  assert.deepEqual(names, [
    'edit_file', 'get_vault', 'list_dir', 'list_vaults', 'migrate_config',
    'read_file', 'register_vault', 'unregister_vault', 'write_file',
  ]);
  for (const tool of tools) {
    assert.equal(tool.inputSchema.type, 'object', `${tool.name} missing inputSchema`);
  }
});

test('unknown method gets -32601; unknown tool gets isError', async (t) => {
  const { client } = freshClient(t);
  await client.initialize();
  const res = await client.request('bogus/method');
  assert.equal(res.error.code, -32601);
  const call = await client.call('not_a_tool');
  assert.ok(call.isError);
});

// ---------------------------------------------------------------------------
// Registry CRUD
// ---------------------------------------------------------------------------

test('empty registry: list_vaults is friendly, no file created until first save', async (t) => {
  const { client, dir } = freshClient(t);
  await client.initialize();
  const res = await client.call('list_vaults');
  assert.ok(!res.isError);
  assert.match(res.text, /No vaults registered/i);
  assert.ok(!fs.existsSync(path.join(dir, 'registry.json')), 'registry.json should not exist yet');
});

test('register -> list/get round-trip with ISO created timestamp; valid JSON on disk; no tmp leftovers', async (t) => {
  const { client, dir } = freshClient(t);
  await client.initialize();
  const vault = makeVaultDir(t);

  const reg = await client.call('register_vault', { name: 'mcbrain-finance', path: vault });
  assert.ok(!reg.isError, reg.text);

  const list = await client.call('list_vaults');
  const parsed = JSON.parse(list.text);
  assert.equal(parsed.vaults.length, 1);
  assert.equal(parsed.vaults[0].name, 'mcbrain-finance');
  assert.equal(parsed.vaults[0].path, vault);
  assert.match(parsed.vaults[0].created, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);

  const get = await client.call('get_vault', { name: 'mcbrain-finance' });
  assert.ok(!get.isError);
  assert.equal(JSON.parse(get.text).path, vault);

  // On-disk: valid JSON, atomic write left no temp files.
  const onDisk = JSON.parse(fs.readFileSync(path.join(dir, 'registry.json'), 'utf8'));
  assert.equal(onDisk.vaults[0].name, 'mcbrain-finance');
  assert.ok(!fs.readdirSync(dir).some((f) => f.includes('.tmp')), 'no *.tmp leftovers');
});

test('register_vault rejections: duplicate name, relative path, nonexistent path, bad name', async (t) => {
  const { client } = freshClient(t);
  await client.initialize();
  const vault = makeVaultDir(t);

  assert.ok(!(await client.call('register_vault', { name: 'mcbrain-x', path: vault })).isError);
  const dup = await client.call('register_vault', { name: 'mcbrain-x', path: vault });
  assert.ok(dup.isError, 'duplicate name must be rejected');

  const rel = await client.call('register_vault', { name: 'mcbrain-rel', path: 'relative/path' });
  assert.ok(rel.isError, 'relative path must be rejected');

  const gone = await client.call('register_vault', { name: 'mcbrain-gone', path: path.join(vault, 'nope') });
  assert.ok(gone.isError, 'nonexistent path must be rejected');

  for (const bad of ['Finance', 'mcbrain_finance', 'brain-x', 'mcbrain-']) {
    const res = await client.call('register_vault', { name: bad, path: vault });
    assert.ok(res.isError, `bad name "${bad}" must be rejected`);
  }
});

test('register_vault warns on duplicate path (different name)', async (t) => {
  const { client } = freshClient(t);
  await client.initialize();
  const vault = makeVaultDir(t);
  await client.call('register_vault', { name: 'mcbrain-a', path: vault });
  const second = await client.call('register_vault', { name: 'mcbrain-b', path: vault });
  assert.ok(!second.isError);
  assert.match(second.text, /[Ww]arning.*mcbrain-a/s);
});

test('unregister removes the entry but never touches vault files; unknown name is isError', async (t) => {
  const { client } = freshClient(t);
  await client.initialize();
  const vault = makeVaultDir(t);
  fs.writeFileSync(path.join(vault, 'precious.md'), 'do not delete');

  await client.call('register_vault', { name: 'mcbrain-x', path: vault });
  const un = await client.call('unregister_vault', { name: 'mcbrain-x' });
  assert.ok(!un.isError);
  assert.match((await client.call('list_vaults')).text, /No vaults registered/i);
  assert.equal(fs.readFileSync(path.join(vault, 'precious.md'), 'utf8'), 'do not delete');

  const unknown = await client.call('unregister_vault', { name: 'mcbrain-x' });
  assert.ok(unknown.isError);
  assert.ok(!(await client.call('get_vault', { name: 'mcbrain-x' })).text.includes('precious'));
});

test('get_vault on unknown name returns isError listing registered names', async (t) => {
  const { client } = freshClient(t);
  await client.initialize();
  const vault = makeVaultDir(t);
  await client.call('register_vault', { name: 'mcbrain-known', path: vault });
  const res = await client.call('get_vault', { name: 'mcbrain-nope' });
  assert.ok(res.isError);
  assert.match(res.text, /mcbrain-known/);
});

test('corrupt registry.json surfaces a clear error naming the path', async (t) => {
  const { client, dir } = freshClient(t);
  await client.initialize();
  fs.writeFileSync(path.join(dir, 'registry.json'), '{not json');
  const res = await client.call('list_vaults');
  assert.ok(res.isError);
  assert.ok(res.text.includes(path.join(dir, 'registry.json')), `error should name the file: ${res.text}`);
});

// ---------------------------------------------------------------------------
// File gateway: happy paths
// ---------------------------------------------------------------------------

async function gatewaySetup(t) {
  const { client } = freshClient(t);
  await client.initialize();
  const vault = makeVaultDir(t);
  const reg = await client.call('register_vault', { name: 'mcbrain-gw', path: vault });
  assert.ok(!reg.isError, reg.text);
  return { client, vault };
}

test('write -> read round-trip returns identical content', async (t) => {
  const { client } = await gatewaySetup(t);
  const content = '# Hello\n\nwiki content with unicode: éü漢字\n';
  const w = await client.call('write_file', { vault: 'mcbrain-gw', path: 'wiki/index.md', content });
  assert.ok(!w.isError, w.text);
  const r = await client.call('read_file', { vault: 'mcbrain-gw', path: 'wiki/index.md' });
  assert.ok(!r.isError);
  assert.equal(r.text, content);
});

test('write_file creates nested parent dirs inside the vault', async (t) => {
  const { client, vault } = await gatewaySetup(t);
  const w = await client.call('write_file', { vault: 'mcbrain-gw', path: 'new/nested/file.txt', content: 'deep' });
  assert.ok(!w.isError, w.text);
  assert.equal(fs.readFileSync(path.join(vault, 'new', 'nested', 'file.txt'), 'utf8'), 'deep');
});

test('edit_file replaces exactly-once match; list_dir marks dirs with /', async (t) => {
  const { client } = await gatewaySetup(t);
  await client.call('write_file', { vault: 'mcbrain-gw', path: 'note.md', content: 'alpha beta gamma' });
  const e = await client.call('edit_file', { vault: 'mcbrain-gw', path: 'note.md', old: 'beta', new: 'BETA' });
  assert.ok(!e.isError, e.text);
  assert.equal((await client.call('read_file', { vault: 'mcbrain-gw', path: 'note.md' })).text, 'alpha BETA gamma');

  await client.call('write_file', { vault: 'mcbrain-gw', path: 'raw/x.md', content: 'x' });
  const ls = await client.call('list_dir', { vault: 'mcbrain-gw' });
  assert.ok(!ls.isError);
  assert.match(ls.text, /raw\//);
  assert.match(ls.text, /note\.md/);
});

test('edit_file: zero matches and multiple matches are isError with count', async (t) => {
  const { client } = await gatewaySetup(t);
  await client.call('write_file', { vault: 'mcbrain-gw', path: 'dup.md', content: 'aa aa' });
  const zero = await client.call('edit_file', { vault: 'mcbrain-gw', path: 'dup.md', old: 'zz', new: 'q' });
  assert.ok(zero.isError);
  assert.match(zero.text, /0 matches/);
  const multi = await client.call('edit_file', { vault: 'mcbrain-gw', path: 'dup.md', old: 'aa', new: 'q' });
  assert.ok(multi.isError);
  assert.match(multi.text, /2 times/);
});

test('read_file on missing file and list_dir on missing dir are isError, not crashes', async (t) => {
  const { client } = await gatewaySetup(t);
  assert.ok((await client.call('read_file', { vault: 'mcbrain-gw', path: 'nope.md' })).isError);
  assert.ok((await client.call('list_dir', { vault: 'mcbrain-gw', path: 'nodir' })).isError);
});

// ---------------------------------------------------------------------------
// File gateway: scoping / traversal
// ---------------------------------------------------------------------------

test('all four gateway tools reject traversal, absolute paths, and unknown vaults', async (t) => {
  const { client, vault } = await gatewaySetup(t);
  const calls = [
    (p, v = 'mcbrain-gw') => client.call('read_file', { vault: v, path: p }),
    (p, v = 'mcbrain-gw') => client.call('write_file', { vault: v, path: p, content: 'x' }),
    (p, v = 'mcbrain-gw') => client.call('edit_file', { vault: v, path: p, old: 'a', new: 'b' }),
    (p, v = 'mcbrain-gw') => client.call('list_dir', { vault: v, path: p }),
  ];
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'mcbrain-outside-'));
  t.after(() => fs.rmSync(outside, { recursive: true, force: true }));

  for (const call of calls) {
    for (const bad of ['../escape', 'a/../../escape', path.join(outside, 'f.txt'), '/etc/hosts']) {
      const res = await call(bad);
      assert.ok(res.isError, `path "${bad}" must be refused`);
      // Error names the vault root but not the resolved outside path.
      assert.ok(res.text.includes(vault), `refusal should name vault root: ${res.text}`);
      assert.ok(!res.text.includes(outside), `refusal must not leak outside path: ${res.text}`);
      assert.ok(!res.text.includes('/etc/hosts'), `refusal must not echo absolute target: ${res.text}`);
    }
    const unknown = await call('file.md', 'mcbrain-ghost');
    assert.ok(unknown.isError, 'unknown vault must be refused');
  }
});

test('write_file cannot create parent dirs outside the vault via traversal', async (t) => {
  const { client } = await gatewaySetup(t);
  const res = await client.call('write_file', {
    vault: 'mcbrain-gw', path: '../escaped-dir/file.txt', content: 'x',
  });
  assert.ok(res.isError);
});

test('symlink inside vault pointing outside is refused', { skip: process.platform === 'win32' }, async (t) => {
  const { client, vault } = await gatewaySetup(t);
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'mcbrain-secret-'));
  t.after(() => fs.rmSync(outside, { recursive: true, force: true }));
  fs.writeFileSync(path.join(outside, 'secret.txt'), 'classified');
  fs.symlinkSync(outside, path.join(vault, 'sneaky'));

  const viaDir = await client.call('read_file', { vault: 'mcbrain-gw', path: 'sneaky/secret.txt' });
  assert.ok(viaDir.isError, 'read through escaping dir symlink must be refused');
  assert.ok(!viaDir.text.includes('classified'));

  fs.symlinkSync(path.join(outside, 'secret.txt'), path.join(vault, 'sneaky-file'));
  const viaFile = await client.call('read_file', { vault: 'mcbrain-gw', path: 'sneaky-file' });
  assert.ok(viaFile.isError, 'read through escaping file symlink must be refused');

  const write = await client.call('write_file', { vault: 'mcbrain-gw', path: 'sneaky/planted.txt', content: 'x' });
  assert.ok(write.isError, 'write through escaping symlink must be refused');
  assert.ok(!fs.existsSync(path.join(outside, 'planted.txt')));
});

// ---------------------------------------------------------------------------
// migrate_config
// ---------------------------------------------------------------------------

function writeFixtureConfig(t, vaultA, vaultB) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mcbrain-desktop-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'claude_desktop_config.json');
  fs.writeFileSync(file, JSON.stringify({
    mcpServers: {
      'mcbrain-finance': {
        command: 'npx',
        args: ['-y', '@modelcontextprotocol/server-filesystem', vaultA],
      },
      'mcbrain-house': {
        command: 'npx',
        args: ['-y', '@modelcontextprotocol/server-filesystem', vaultB],
      },
      'unrelated-server': {
        command: 'npx',
        args: ['-y', 'some-other-package', '/some/path'],
      },
    },
  }, null, 2));
  return file;
}

test('migrate_config imports mcbrain-* filesystem entries, ignores unrelated, is idempotent, never edits the config', async (t) => {
  const vaultA = makeVaultDir(t, 'finance');
  const vaultB = makeVaultDir(t, 'house');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mcbrain-test-'));
  const fixture = writeFixtureConfig(t, vaultA, vaultB);
  const before = crypto.createHash('sha256').update(fs.readFileSync(fixture)).digest('hex');

  const client = new McbrainClient({ MCBRAIN_DIR: dir, MCBRAIN_DESKTOP_CONFIG: fixture });
  t.after(() => {
    client.close();
    fs.rmSync(dir, { recursive: true, force: true });
  });
  await client.initialize();

  const first = await client.call('migrate_config');
  assert.ok(!first.isError, first.text);
  assert.match(first.text, /mcbrain-finance/);
  assert.match(first.text, /mcbrain-house/);
  assert.ok(!first.text.includes('unrelated-server'), 'unrelated server must not appear in the report');

  const vaults = JSON.parse((await client.call('list_vaults')).text).vaults;
  assert.deepEqual(vaults.map((v) => v.name).sort(), ['mcbrain-finance', 'mcbrain-house']);
  assert.equal(vaults.find((v) => v.name === 'mcbrain-finance').path, vaultA);
  assert.equal(vaults.find((v) => v.name === 'mcbrain-house').path, vaultB);

  // Idempotent: second run imports nothing.
  const second = await client.call('migrate_config');
  assert.ok(!second.isError);
  assert.match(second.text, /Nothing new to import/i);
  assert.equal(JSON.parse((await client.call('list_vaults')).text).vaults.length, 2);

  // Config bytes untouched.
  const after = crypto.createHash('sha256').update(fs.readFileSync(fixture)).digest('hex');
  assert.equal(before, after, 'claude_desktop_config.json must never be modified');
});

test('migrate_config with no config file is a friendly no-op, not an error', async (t) => {
  const { client } = freshClient(t); // harness points MCBRAIN_DESKTOP_CONFIG at /nonexistent
  await client.initialize();
  const res = await client.call('migrate_config');
  assert.ok(!res.isError);
  assert.match(res.text, /[Nn]o Claude Desktop config found/);
});
