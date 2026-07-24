import fs from 'node:fs';
import path from 'node:path';

const endpoint = process.env.FOUNDER_IDE_CDP || 'http://127.0.0.1:9452';
const outputDir = path.resolve(
  process.env.FOUNDER_IDE_QA_DIR || 'artifacts/installed-visual-qa',
);
const marker = process.env.FOUNDER_IDE_TERMINAL_MARKER
  || `FOUNDER_TERMINAL_${Date.now()}`;
fs.mkdirSync(outputDir, { recursive: true });

const targets = await fetch(`${endpoint}/json/list`).then((response) =>
  response.json()
);
const target = targets.find(
  (candidate) =>
    candidate.type === 'page'
    && candidate.url?.includes('/workbench/workbench.html'),
);
if (!target?.webSocketDebuggerUrl) {
  throw new Error('Founder IDE workbench page was not found.');
}

const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true });
  socket.addEventListener('error', reject, { once: true });
});

let sequence = 0;
const pending = new Map();
socket.addEventListener('message', ({ data }) => {
  const message = JSON.parse(data.toString());
  if (!message.id || !pending.has(message.id)) return;
  const { resolve, reject, timer } = pending.get(message.id);
  clearTimeout(timer);
  pending.delete(message.id);
  if (message.error) reject(new Error(message.error.message));
  else resolve(message.result);
});

function send(method, params = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`${method} timed out`)),
      30_000,
    );
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const response = await send('Runtime.evaluate', {
    expression,
    returnByValue: true,
  });
  if (response.exceptionDetails) {
    throw new Error(response.exceptionDetails.text);
  }
  return response.result?.value;
}

const wait = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

async function runCommandPalette(command) {
  await send('Input.dispatchKeyEvent', {
    type: 'rawKeyDown',
    key: 'P',
    code: 'KeyP',
    modifiers: 10,
    windowsVirtualKeyCode: 80,
    nativeVirtualKeyCode: 80,
  });
  await send('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key: 'P',
    code: 'KeyP',
    modifiers: 10,
  });
  await wait(400);
  await send('Input.insertText', { text: command });
  await wait(400);
  await send('Input.dispatchKeyEvent', {
    type: 'rawKeyDown',
    key: 'Enter',
    code: 'Enter',
    windowsVirtualKeyCode: 13,
    nativeVirtualKeyCode: 13,
  });
  await send('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key: 'Enter',
    code: 'Enter',
  });
  await wait(1_000);
}

await send('Runtime.enable');
await send('Page.enable');
await send('Accessibility.enable');
await runCommandPalette('Terminal: Kill All Terminals');
await runCommandPalette('Terminal: Create New Terminal');
await wait(8_000);

let terminalReady = false;
const deadline = Date.now() + 20_000;
while (!terminalReady && Date.now() < deadline) {
  terminalReady = await evaluate(`(() => {
    const input = document.querySelector('textarea.xterm-helper-textarea');
    if (!input) return false;
    input.focus();
    return document.activeElement === input;
  })()`);
  if (!terminalReady) await wait(500);
}
if (!terminalReady) throw new Error('Founder terminal was not ready.');

await send('Input.insertText', {
  text: `Write-Output ${marker}`,
});
await send('Input.dispatchKeyEvent', {
  type: 'rawKeyDown',
  key: 'Enter',
  code: 'Enter',
  windowsVirtualKeyCode: 13,
  nativeVirtualKeyCode: 13,
});
await send('Input.dispatchKeyEvent', {
  type: 'keyUp',
  key: 'Enter',
  code: 'Enter',
  windowsVirtualKeyCode: 13,
  nativeVirtualKeyCode: 13,
});

let terminalText = '';
let accessibilityText = '';
let accessibleBufferText = '';
const outputDeadline = Date.now() + 20_000;
while (
  !terminalText.includes(marker)
  && !accessibilityText.includes(marker)
  && Date.now() < outputDeadline
) {
  await wait(500);
  terminalText = await evaluate(`(() => {
    const roots = [
      ...document.querySelectorAll('.xterm-rows > div'),
      ...document.querySelectorAll('.xterm-accessibility-tree > div'),
    ];
    return roots.map((row) => row.textContent || '').join('\\n');
  })()`);
  const accessibility = await send('Accessibility.getFullAXTree');
  accessibilityText = (accessibility.nodes || [])
    .map((node) => String(node.name?.value || ''))
    .filter(Boolean)
    .join('\n');
}
if (!terminalText.includes(marker) && !accessibilityText.includes(marker)) {
  await runCommandPalette('Terminal: Focus Accessible Buffer');
  accessibleBufferText = await evaluate(
    `document.body.innerText.slice(-30000)`,
  );
}

const screenshot = await send('Page.captureScreenshot', {
  format: 'png',
  fromSurface: true,
  captureBeyondViewport: false,
});
const screenshotPath = path.join(outputDir, 'installed-terminal-final.png');
fs.writeFileSync(screenshotPath, Buffer.from(screenshot.data, 'base64'));

const evidence = {
  marker,
  terminalReady,
  commandIssued: true,
  markerVisible:
    terminalText.includes(marker)
    || accessibilityText.includes(marker)
    || accessibleBufferText.includes(marker),
  terminalText,
  accessibilityText,
  accessibleBufferText,
  screenshotPath,
};
fs.writeFileSync(
  path.join(outputDir, 'installed-terminal-final.json'),
  `${JSON.stringify(evidence, null, 2)}\n`,
);
process.stdout.write(`${JSON.stringify(evidence, null, 2)}\n`);
socket.close();
if (!evidence.terminalReady || !evidence.commandIssued) process.exitCode = 1;
