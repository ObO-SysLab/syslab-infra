const WebSocket = require('ws');
const { spawn } = require('child_process');

const wss = new WebSocket.Server({ port: 8081, path: '/ws/terminal' });
console.log('WebSocket server running on port 8081');

wss.on('connection', (ws, req) => {
  console.log('Client connected:', req.socket.remoteAddress);

  const url = new URL(req.url, 'http://localhost');
  const containerId = url.searchParams.get('containerId');

  if (!containerId) {
    ws.send('\r\n[ERROR] containerId가 없습니다.\r\n');
    ws.close();
    return;
  }

  const shell = spawn('docker', ['exec', '-it', containerId, '/bin/bash'], {
    env: { ...process.env, TERM: 'xterm' }
  });

  shell.stdout.on('data', (data) => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(data.toString());
    }
  });

  shell.stderr.on('data', (data) => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(data.toString());
    }
  });

  ws.on('message', (data) => {
    shell.stdin.write(data);
  });

  ws.on('close', () => {
    console.log('Client disconnected');
    shell.kill();
  });

  shell.on('exit', () => {
    console.log('Shell exited');
    if (ws.readyState === WebSocket.OPEN) {
      ws.close();
    }
  });
});
