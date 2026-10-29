'use strict';

const CONTAINER_ID = /^[a-f0-9]{64}$/;

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
    ws.on('message', data => {
      if (closed) return;
      if (now() >= grant.expiresAt) return stop();
      try { shell.write(data.toString()); } catch { stop(); }
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
