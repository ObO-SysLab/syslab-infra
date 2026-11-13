'use strict';

const CONTAINER_ID = /^[a-f0-9]{64}$/;

// Browser resize control: a binary frame "\0DVRS" + uint16 cols + uint16 rows (big-endian).
// xterm's own binary input starts with ESC, so it never collides with this prefix.
const RESIZE_PREFIX = Buffer.from('\0DVRS', 'latin1');
const IGNORED = Symbol('ignored control frame');
function resizeOf(data, isBinary) {
  if (!isBinary || !Buffer.isBuffer(data) || data.length < RESIZE_PREFIX.length
      || !data.subarray(0, RESIZE_PREFIX.length).equals(RESIZE_PREFIX)) return null;
  if (data.length !== RESIZE_PREFIX.length + 4) return IGNORED;
  const cols = data.readUInt16BE(RESIZE_PREFIX.length);
  const rows = data.readUInt16BE(RESIZE_PREFIX.length + 2);
  return cols >= 20 && cols <= 500 && rows >= 5 && rows <= 200 ? { cols, rows } : IGNORED;
}

// No listeners or processes are started at import time. Dependencies are injectable
// so authorization failures and PTY lifecycle can be tested without Docker.
function attachTerminal(ws, grant, { pty, check, now = Date.now,
  setTimer = setTimeout, clearTimer = clearTimeout }) {
  let shell;
  let closed = false;
  let poll;
  let expiry;
  const stop = () => {
    if (closed) return;
    closed = true;
    clearTimer(poll);
    clearTimer(expiry);
    try { shell?.kill(); } catch { /* Already exited. */ }
    // terminate does not wait for a potentially hostile client's close reply.
    ws.terminate();
  };
  ws.on('close', stop);
  ws.on('error', stop);
  if (!CONTAINER_ID.test(grant.containerId) || !Number.isSafeInteger(grant.expiresAt)
      || grant.expiresAt <= now() || ws.readyState !== 1) {
    stop();
    return;
  }
  try {
    shell = pty.spawn('docker', ['exec', '-it', grant.containerId, '/bin/bash'], {
      name: 'xterm-color', cols: 80, rows: 24,
      // Never pass the terminal service credential to the PTY child process.
      env: { PATH: process.env.PATH, TERM: 'xterm-color', LANG: 'C.UTF-8' },
    });
    shell.onData(data => {
      if (closed) return;
      if (now() >= grant.expiresAt || ws.bufferedAmount > 1024 * 1024) return stop();
      if (ws.readyState === 1) ws.send(data, error => { if (error) stop(); });
    });
    shell.onExit(stop);
    ws.on('message', (data, isBinary) => {
      if (closed) return;
      if (now() >= grant.expiresAt) return stop();
      const size = resizeOf(data, isBinary);
      if (size === IGNORED) return;
      try { if (size) shell.resize(size.cols, size.rows); else shell.write(data.toString()); } catch { stop(); }
    });
  } catch {
    stop();
    return;
  }
  expiry = setTimer(stop, Math.min(grant.expiresAt - now(), 2147483647));
  const recheck = async () => {
    const startedAt = now();
    try {
      const current = await check(grant.lease);
      if (closed) return;
      if (current.containerId !== grant.containerId || current.expiresAt !== grant.expiresAt
          || now() >= grant.expiresAt) return stop();
      poll = setTimer(recheck, Math.max(0, 5000 - (now() - startedAt)));
    } catch { stop(); }
  };
  poll = setTimer(recheck, 5000);
  return stop;
}

module.exports = { attachTerminal, CONTAINER_ID };
