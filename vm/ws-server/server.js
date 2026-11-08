'use strict';
const http = require('node:http');
const { parseRequest, apiClient, authenticate } = require('./auth');

function createServer({ api, pty, allowedOrigin, WebSocketServer }) {
  const origin = new URL(allowedOrigin);
  if (origin.origin !== allowedOrigin || origin.protocol !== 'https:') {
    throw new Error('TERMINAL_ALLOWED_ORIGIN must be an exact HTTPS origin');
  }
  const wss = new WebSocketServer({ noServer: true, maxPayload: 16384, perMessageDeflate: false });
  const server = http.createServer((_req, res) => { res.writeHead(404); res.end(); });
  server.on('upgrade', (req, socket, head) => {
    // Never log URLs, credentials, request headers, or PTY content.
    socket.on('error', () => socket.destroy());
    try {
      const containerId = parseRequest(req, allowedOrigin);
      wss.handleUpgrade(req, socket, head, ws => authenticate(ws, containerId, { api, pty }));
    } catch {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
    }
  });
  return { server, wss };
}

if (require.main === module) {
  const api = apiClient({ apiUrl: process.env.TERMINAL_API_URL, serviceKey: process.env.TERMINAL_SERVICE_KEY });
  const { server } = createServer({ api, pty: require('node-pty'),
    WebSocketServer: require('ws').WebSocketServer,
    allowedOrigin: process.env.TERMINAL_ALLOWED_ORIGIN });
  server.listen(8081, process.env.TERMINAL_BIND_HOST || '127.0.0.1', () => {
    console.log('Authenticated terminal listening on port 8081');
  });
}

module.exports = { createServer };
