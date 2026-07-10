#!/usr/bin/env node
// Fetches Railway deployment logs via the graphql-transport-ws WebSocket.
// Usage: node scripts/fetch-railway-logs.mjs <deployment-id> [build|deploy]
import WebSocket from 'ws';

const token = process.env.RAILWAY_TOKEN || '529467fa-6b10-4d60-837e-97c6cad395df';
const deploymentId = process.argv[2];
const stream = process.argv[3] === 'build' ? 'buildLogs' : 'deploymentLogs';
if (!deploymentId) {
  console.error('Usage: node scripts/fetch-railway-logs.mjs <deployment-id> [build|deploy]');
  process.exit(1);
}

const ws = new WebSocket(
  'wss://backboard.railway.com/graphql/v2',
  'graphql-transport-ws',
  { headers: { Authorization: `Bearer ${token}` } },
);

let count = 0;
const timeout = setTimeout(() => {
  console.error(`[timeout] got ${count} log lines`);
  ws.close();
  process.exit(0);
}, 15000);

ws.on('open', () => {
  ws.send(JSON.stringify({ type: 'connection_init', payload: {} }));
});

ws.on('message', (data) => {
  const msg = JSON.parse(data.toString());
  if (msg.type === 'connection_ack') {
    ws.send(
      JSON.stringify({
        id: '1',
        type: 'subscribe',
        payload: {
          query: `subscription { ${stream}(deploymentId: "${deploymentId}") { message severity timestamp } }`,
        },
      }),
    );
  } else if (msg.type === 'next' && msg.id === '1') {
    const payload = msg.payload?.data || {};
    const line = payload[stream];
    if (line) {
      count++;
      const sev = (line.severity || '?').padEnd(5);
      console.log(`${sev} ${(line.message || '').replace(/\n/g, '\n     ')}`);
    }
  } else if (msg.type === 'error') {
    console.error('[graphql error]', JSON.stringify(msg.payload).slice(0, 600));
    clearTimeout(timeout);
    ws.close();
    process.exit(1);
  }
});

ws.on('error', (err) => {
  console.error('[ws error]', err.message);
  process.exit(1);
});
