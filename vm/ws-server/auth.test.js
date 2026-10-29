const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { parseRequest, apiClient, authenticate } = require('./auth');
const cid = 'a'.repeat(64), ticket = 't'.repeat(43), lease = 'l'.repeat(43);
const origin = 'https://diveon.net';
const flush = () => new Promise(resolve => setImmediate(resolve));

test('only exact origin, route and one canonical container_id are accepted', () => {
  const req = { url: `/ws/terminal?container_id=${cid}`, headers: { origin } };
  assert.equal(parseRequest(req, origin), cid);
  for (const url of ['/other', '/ws/terminal', `/ws/terminal?containerId=${cid}`,
    `/ws/terminal?container_id=${cid}&token=login`, `/ws/terminal?container_id=${cid}&container_id=${cid}`,
    '/ws/terminal?container_id=--help', `/ws/terminal?container_id=${cid.slice(0, 12)}`]) {
    assert.throws(() => parseRequest({ ...req, url }, origin));
  }
  for (const bad of [undefined, 'null', 'https://evil.example', 'https://diveon.net.evil.example']) {
    assert.throws(() => parseRequest({ ...req, headers: { origin: bad } }, origin));
  }
});

function fixture(connect = async () => ({ containerId: cid, lease, expiresAt: 10000 })) {
  const ws = new EventEmitter(); const timers = new Map(); const spawns = [];
  Object.assign(ws, { readyState: 1, sent: [], send(x) { this.sent.push(x); }, terminate() { this.readyState = 3; } });
  authenticate(ws, cid, { api: { connect, check: async () => ({ containerId: cid, expiresAt: 10000 }) },
    pty: { spawn(...args) { spawns.push(args); return { onData() {}, onExit() {}, kill() {}, write() {} }; } },
    now: () => 1000, setTimer(fn, ms) { timers.set(fn, ms); return fn; }, clearTimer(fn) { timers.delete(fn); } });
  return { ws, spawns, timers, auth: () => ws.emit('message', Buffer.from(JSON.stringify({ type: 'auth', ticket })), false) };
}
test('unauthenticated idle client never spawns and is terminated', () => {
  const f = fixture(); assert.equal(f.spawns.length, 0);
  [...f.timers.keys()][0](); assert.equal(f.ws.readyState, 3); assert.equal(f.spawns.length, 0);
});
test('authorized handshake sends ready before creating PTY', async () => {
  const f = fixture(); f.auth(); await flush();
  assert.deepEqual(f.ws.sent, ['{"type":"ready"}']); assert.equal(f.spawns.length, 1);
});
for (const reason of ['missing', 'forged', 'other-user', 'expired', 'stopped', 'replayed', 'DB down', 'Redis down']) {
  test(`backend rejection (${reason}) never starts shell`, async () => {
    const f = fixture(async () => { throw new Error(reason); }); f.auth(); await flush();
    assert.equal(f.spawns.length, 0); assert.equal(f.ws.readyState, 3);
  });
}
test('malformed, binary and shell input before auth are rejected', async () => {
  for (const [data, binary] of [['ls\r', false], ['{}', false], ['null', false],
    [JSON.stringify({ type: 'auth', ticket }), true], [JSON.stringify({ type: 'auth', ticket, userId: 7 }), false]]) {
    const f = fixture(); f.ws.emit('message', Buffer.from(data), binary); await flush();
    assert.equal(f.spawns.length, 0); assert.equal(f.ws.readyState, 3);
  }
});
test('disconnect or extra data during validation cannot create a late PTY', async () => {
  for (const action of [f => f.ws.emit('close'), f => f.ws.emit('message', Buffer.from('id\r'), false)]) {
    let resolve; const f = fixture(() => new Promise(r => { resolve = r; })); f.auth(); action(f);
    resolve({ containerId: cid, lease, expiresAt: 10000 }); await flush(); assert.equal(f.spawns.length, 0);
  }
});
test('wrong target or expired backend grant never starts PTY', async () => {
  for (const grant of [{ containerId: 'b'.repeat(64), lease, expiresAt: 10000 },
    { containerId: cid, lease, expiresAt: 1000 }]) {
    const f = fixture(async () => grant); f.auth(); await flush(); assert.equal(f.spawns.length, 0);
  }
});

const config = { apiUrl: 'http://127.0.0.1:18080', serviceKey: 'test-only-service-key-0123456789abcdef' };
test('service call uses credential header and body, not URL; redirects forbidden', async () => {
  let call;
  const client = apiClient({ ...config, fetchImpl: async (...args) => {
    call = args; return { ok: true, json: async () => ({ containerId: cid, lease, expiresAt: 99999 }) };
  } });
  assert.equal((await client.connect(ticket, cid)).lease, lease);
  assert.equal(call[0].pathname, '/internal/terminal/connect'); assert.equal(call[0].search, '');
  assert.equal(call[1].headers['X-Terminal-Service-Key'], config.serviceKey);
  assert.equal(call[1].redirect, 'error'); assert.deepEqual(JSON.parse(call[1].body), { ticket, containerId: cid });
});
test('plaintext non-loopback API and missing service key fail closed', () => {
  for (const apiUrl of ['http://10.0.1.10:8080', 'http://localhost:8080', 'https://api.test/?token=x', 'https://u:p@api.test']) {
    assert.throws(() => apiClient({ ...config, apiUrl }));
  }
  assert.throws(() => apiClient({ ...config, serviceKey: '' }));
});
test('failed, malformed, mismatched and stalled verification fails closed', async () => {
  for (const response of [{ ok: false }, { ok: true, json: async () => ({}) },
    { ok: true, json: async () => ({ containerId: 'b'.repeat(64), lease, expiresAt: 9999 }) }]) {
    await assert.rejects(apiClient({ ...config, fetchImpl: async () => response }).connect(ticket, cid));
  }
  for (const fetchImpl of [() => new Promise(() => {}), async () => ({ ok: true, json: () => new Promise(() => {}) })]) {
    await assert.rejects(apiClient({ ...config, fetchImpl, timeoutMs: 10 }).connect(ticket, cid), /timed out/);
  }
});
