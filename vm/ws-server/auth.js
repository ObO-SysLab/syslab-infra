'use strict';
const { CONTAINER_ID, attachTerminal } = require('./terminal');
const TOKEN = /^[A-Za-z0-9_-]{43}$/;

function parseRequest(req, allowedOrigin) {
  const url = new URL(req.url, 'http://terminal.invalid');
  const keys = [...url.searchParams.keys()];
  const containerId = url.searchParams.get('container_id');
  if (req.headers.origin !== allowedOrigin || url.pathname !== '/ws/terminal'
      || keys.length !== 1 || keys[0] !== 'container_id' || !CONTAINER_ID.test(containerId || '')) {
    throw new Error('Terminal access denied');
  }
  return containerId;
}

function apiClient({ apiUrl, serviceKey, fetchImpl = fetch, timeoutMs = 2000 }) {
  const base = new URL(apiUrl);
  if (base.username || base.password || base.search || base.hash || base.pathname !== '/'
      || (base.protocol !== 'https:' && !(base.protocol === 'http:'
          && ['127.0.0.1', '[::1]'].includes(base.hostname)))
      || !serviceKey || serviceKey.length < 32 || serviceKey.length > 256) {
    throw new Error('Invalid terminal service configuration');
  }
  async function request(path, body) {
    const controller = new AbortController();
    let timer;
    try {
      return await Promise.race([
        (async () => {
          const response = await fetchImpl(new URL(path, base), {
            method: 'POST', redirect: 'error', signal: controller.signal,
            headers: { 'Content-Type': 'application/json', 'X-Terminal-Service-Key': serviceKey },
            body: JSON.stringify(body),
          });
          if (!response.ok) throw new Error('Terminal verification failed');
          const result = await response.json();
          if (!CONTAINER_ID.test(result.containerId || '') || !Number.isSafeInteger(result.expiresAt)) {
            throw new Error('Invalid terminal verification response');
          }
          return result;
        })(),
        new Promise((_, reject) => { timer = setTimeout(() => {
          controller.abort(); reject(new Error('Terminal verification timed out'));
        }, timeoutMs); }),
      ]);
    } finally { clearTimeout(timer); }
  }
  return {
    async connect(ticket, containerId) {
      const grant = await request('/internal/terminal/connect', { ticket, containerId });
      if (grant.containerId !== containerId || !TOKEN.test(grant.lease || '')) throw new Error('Invalid terminal grant');
      return grant;
    },
    check: lease => request('/internal/terminal/check', { lease }),
  };
}

function authenticate(ws, containerId, { api, pty, now = Date.now,
  setTimer = setTimeout, clearTimer = clearTimeout, attach = attachTerminal }) {
  let finished = false;
  let checking = false;
  const cleanup = () => {
    clearTimer(timer); ws.off('message', onMessage); ws.off('close', fail); ws.off('error', fail);
  };
  const fail = () => {
    if (finished) return;
    finished = true; cleanup(); ws.terminate();
  };
  const onMessage = async (data, isBinary) => {
    if (finished) return;
    if (checking) return fail();
    checking = true;
    try {
      if (isBinary || data.length > 256) throw new Error('Invalid authentication');
      const message = JSON.parse(data.toString());
      if (message.type !== 'auth' || !TOKEN.test(message.ticket || '')
          || Object.keys(message).length !== 2) throw new Error('Invalid authentication');
      const grant = await api.connect(message.ticket, containerId);
      if (finished || ws.readyState !== 1) return;
      if (grant.containerId !== containerId || !TOKEN.test(grant.lease || '')
          || !Number.isSafeInteger(grant.expiresAt) || grant.expiresAt <= now()) return fail();
      // Queue ready before any PTY output; raw terminal traffic starts afterward.
      ws.send(JSON.stringify({ type: 'ready' }));
      finished = true; cleanup();
      attach(ws, grant, { pty, check: api.check, now, setTimer, clearTimer });
    } catch { fail(); }
  };
  const timer = setTimer(fail, 5000);
  ws.on('message', onMessage); ws.on('close', fail); ws.on('error', fail);
}

module.exports = { parseRequest, apiClient, authenticate };
