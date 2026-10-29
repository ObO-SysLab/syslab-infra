const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { attachTerminal } = require('./terminal');

function fixture(overrides = {}) {
  const ws = new EventEmitter();
  Object.assign(ws, { readyState: 1, bufferedAmount: 0, sent: [],
    send(data) { this.sent.push(data); }, terminate() { this.readyState = 3; } });
  const shell = { writes: [], killed: 0, write(data) { this.writes.push(data); },
    kill() { this.killed++; }, onData(cb) { this.data = cb; }, onExit(cb) { this.exit = cb; } };
  const timers = new Map();
  const calls = [];
  const grant = { containerId: 'a'.repeat(64), expiresAt: 20000, lease: 'lease' };
  const deps = { pty: { spawn(...args) { calls.push(args); return shell; } },
    check: async () => grant, now: () => 1000,
    setTimer(fn, delay) { timers.set(fn, delay); return fn; }, clearTimer(fn) { timers.delete(fn); },
    ...overrides };
  return { ws, shell, timers, calls, grant, deps,
    start() { return attachTerminal(ws, grant, deps); } };
}

test('authorized PTY preserves docker exec, size, interactive input and output', () => {
  const f = fixture(); f.start();
  assert.deepEqual(f.calls[0].slice(0, 2), ['docker', ['exec', '-it', f.grant.containerId, '/bin/bash']]);
  assert.equal(f.calls[0][2].cols, 80); assert.equal(f.calls[0][2].rows, 24);
  assert.equal(f.calls[0][2].env.TERMINAL_SERVICE_KEY, undefined);
  f.ws.emit('message', Buffer.from('echo hi\r')); f.shell.data('hi\r\n');
  assert.deepEqual(f.shell.writes, ['echo hi\r']); assert.deepEqual(f.ws.sent, ['hi\r\n']);
  f.ws.emit('close'); f.shell.exit();
  assert.equal(f.shell.killed, 1); assert.equal(f.timers.size, 0);
});
for (const bad of ['--privileged', 'x;id', '', 'z'.repeat(64)]) {
  test(`invalid container never spawns: ${bad}`, () => {
    const f = fixture(); f.grant.containerId = bad; f.start(); assert.equal(f.calls.length, 0);
  });
}
test('expired or already disconnected grant never spawns', () => {
  for (const modify of [f => f.grant.expiresAt = 1000, f => f.ws.readyState = 3]) {
    const f = fixture(); modify(f); f.start(); assert.equal(f.calls.length, 0);
  }
});
test('expiry stops existing PTY and prevents further input', () => {
  const f = fixture(); f.start();
  [...f.timers].find(([, delay]) => delay === 19000)[0]();
  f.ws.emit('message', Buffer.from('id\r'));
  assert.equal(f.shell.killed, 1); assert.equal(f.shell.writes.length, 0);
});
test('stopped session or verification outage kills open PTY', async () => {
  const f = fixture({ check: async () => { throw new Error('unavailable'); } }); f.start();
  await [...f.timers].find(([, delay]) => delay === 5000)[0]();
  assert.equal(f.shell.killed, 1); assert.equal(f.ws.readyState, 3);
});
test('changed session target kills open PTY', async () => {
  const f = fixture({ check: async () => ({ containerId: 'b'.repeat(64), expiresAt: 20000 }) }); f.start();
  await [...f.timers].find(([, delay]) => delay === 5000)[0](); assert.equal(f.shell.killed, 1);
});
test('spawn failure and socket error close without leaking a process', () => {
  const f = fixture({ pty: { spawn() { throw new Error('failed'); } } }); f.start();
  assert.equal(f.ws.readyState, 3);
  const g = fixture(); g.start(); g.ws.emit('error', new Error('socket failed'));
  assert.equal(g.shell.killed, 1);
});
