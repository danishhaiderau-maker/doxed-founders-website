import fs from 'node:fs';
import path from 'node:path';

const endpoint = process.env.FOUNDER_IDE_CDP || 'http://127.0.0.1:9452';
const outputDir = path.resolve(
  process.env.FOUNDER_IDE_QA_DIR || 'artifacts/installed-visual-qa',
);
const imagePath = path.resolve(
  process.env.FOUNDER_IDE_QA_IMAGE
    || path.join(outputDir, 'installed-workbench-1440x900.png'),
);
fs.mkdirSync(outputDir, { recursive: true });
if (!fs.existsSync(imagePath)) throw new Error(`QA image does not exist: ${imagePath}`);

const targets = await fetch(`${endpoint}/json/list`).then((response) => response.json());
const target = targets.find((candidate) =>
  candidate.type === 'page'
  && candidate.url?.includes('/workbench/workbench.html'),
);
if (!target?.webSocketDebuggerUrl) throw new Error('Founder IDE workbench page was not found.');

const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true });
  socket.addEventListener('error', reject, { once: true });
});

let sequence = 0;
const pending = new Map();
const consoleErrors = [];
const pageErrors = [];
socket.addEventListener('message', ({ data }) => {
  const message = JSON.parse(data.toString());
  if (message.id && pending.has(message.id)) {
    const { resolve, reject, timer } = pending.get(message.id);
    clearTimeout(timer);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message));
    else resolve(message.result);
    return;
  }
  if (message.method === 'Runtime.consoleAPICalled' && message.params?.type === 'error') {
    consoleErrors.push(message.params.args?.map((arg) => arg.value ?? arg.description).join(' '));
  }
  if (message.method === 'Runtime.exceptionThrown') {
    pageErrors.push(message.params.exceptionDetails?.text ?? 'Unknown page exception');
  }
});

function send(method, params = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`${method} timed out`)), 30_000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const response = await send('Runtime.evaluate', { expression, returnByValue: true });
  if (response.exceptionDetails) throw new Error(response.exceptionDetails.text);
  return response.result?.value;
}

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

await send('Runtime.enable');
await send('Page.enable');
await send('DOM.enable');
const documentNode = await send('DOM.getDocument', { depth: -1, pierce: true });
const input = await send('DOM.querySelector', {
  nodeId: documentNode.root.nodeId,
  selector: 'input[type="file"][accept*="image/png"]',
});
if (!input.nodeId) throw new Error('Founder screenshot input was not found.');

await send('DOM.setFileInputFiles', {
  nodeId: input.nodeId,
  files: [imagePath],
});

let preview = null;
const deadline = Date.now() + 15_000;
while (!preview && Date.now() < deadline) {
  await wait(300);
  preview = await evaluate(`(() => {
    const region = document.querySelector('[aria-label="Attached screenshots"]');
    const image = region?.querySelector('img');
    const remove = region?.querySelector('button[aria-label^="Remove "]');
    if (!region || !image || !remove) return null;
    return {
      imageAlt: image.getAttribute('alt'),
      imageSource: image.getAttribute('src')?.slice(0, 32),
      removeLabel: remove.getAttribute('aria-label'),
      rect: region.getBoundingClientRect().toJSON(),
    };
  })()`);
}

const screenshot = await send('Page.captureScreenshot', {
  format: 'png',
  fromSurface: true,
  captureBeyondViewport: false,
});
const screenshotPath = path.join(outputDir, 'installed-attachment-final.png');
fs.writeFileSync(screenshotPath, Buffer.from(screenshot.data, 'base64'));

const removed = await evaluate(`(() => {
  const remove = document.querySelector(
    '[aria-label="Attached screenshots"] button[aria-label^="Remove "]',
  );
  remove?.click();
  return Boolean(remove);
})()`);
await wait(300);
const cleared = await evaluate(
  `!document.querySelector('[aria-label="Attached screenshots"]')`,
);

const ignoredConsoleErrors = consoleErrors.filter((message) =>
  /Timed out getting tasks from\s+(?:typescript|npm)/i.test(message),
);
const criticalConsoleErrors = consoleErrors.filter((message) =>
  !/Timed out getting tasks from\s+(?:typescript|npm)/i.test(message),
);
const evidence = {
  imagePath,
  preview,
  removed,
  cleared,
  screenshotPath,
  ignoredConsoleErrors,
  criticalConsoleErrors,
  pageErrors,
};
const evidencePath = path.join(outputDir, 'installed-attachment-final.json');
fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
process.stdout.write(`${JSON.stringify({ ...evidence, evidencePath }, null, 2)}\n`);
socket.close();

if (
  !preview
  || preview.imageAlt !== path.basename(imagePath)
  || !preview.imageSource?.startsWith('data:image/')
  || !removed
  || !cleared
  || criticalConsoleErrors.length > 0
  || pageErrors.length > 0
) process.exitCode = 1;
