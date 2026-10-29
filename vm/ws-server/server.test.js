const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { createServer } = require('./server');

test('upgrade rejects before handing off to websocket server; no listener is started', () => {
  let upgrades = 0;
  class Wss {
    constructor(options) {
      assert.equal(options.maxPayload, 16384); assert.equal(options.noServer, true);
    }
    handleUpgrade() { upgrades++; }
  }
  const { server } = createServer({ api: {}, pty: {}, allowedOrigin: 'https://diveon.net', WebSocketServer: Wss });
  assert.equal(server.listening, false);
  const socket = new EventEmitter(); let response;
  socket.end = value => { response = value; }; socket.destroy = () => {};
  server.emit('upgrade', { url: '/ws/terminal?token=secret', headers: { origin: 'https://diveon.net' } }, socket, Buffer.alloc(0));
  assert.match(response, /^HTTP\/1.1 403/); assert.equal(upgrades, 0);
  assert.ok(!response.includes('secret'));
  server.emit('upgrade', { url: `/ws/terminal?container_id=${'a'.repeat(64)}`, headers: { origin: 'https://diveon.net' } }, socket, Buffer.alloc(0));
  assert.equal(upgrades, 1);
});

test('invalid origin configuration fails before creating any server', () => {
  for (const allowedOrigin of ['', 'http://diveon.net', 'https://diveon.net/path']) {
    assert.throws(() => createServer({ allowedOrigin }));
  }
});
