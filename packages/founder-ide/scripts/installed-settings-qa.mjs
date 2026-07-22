import fs from 'node:fs';
import path from 'node:path';

const endpoint = process.env.FOUNDER_IDE_CDP || 'http://127.0.0.1:9452';
const outputDir = path.resolve(process.env.FOUNDER_IDE_QA_DIR || 'artifacts/installed-visual-qa');
fs.mkdirSync(outputDir, { recursive: true });

const targets = await fetch(`${endpoint}/json/list`).then((response) => response.json());
const target = targets.find((candidate) => candidate.url?.includes('/workbench/workbench.html'));
if (!target?.webSocketDebuggerUrl) throw new Error('Founder IDE workbench page was not found.');

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
    const timer = setTimeout(() => reject(new Error(`${method} timed out`)), 20_000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression, awaitPromise = false) {
  const response = await send('Runtime.evaluate', {
    expression,
    awaitPromise,
    returnByValue: true,
  });
  if (response.exceptionDetails) throw new Error(response.exceptionDetails.text);
  return response.result?.value;
}

async function capture(name) {
  const screenshot = await send('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false,
  });
  fs.writeFileSync(path.join(outputDir, name), Buffer.from(screenshot.data, 'base64'));
}

await send('Runtime.enable');
const clicked = await evaluate(`(() => {
  const action = [...document.querySelectorAll('[aria-label]')]
    .find((element) => element.getAttribute('aria-label') === 'Founder: Toggle Personal AI Settings');
  action?.click();
  return Boolean(action);
})()`);
if (!clicked) throw new Error('Personal AI settings action was not found.');
await new Promise((resolve) => setTimeout(resolve, 2_000));
await capture('installed-personal-ai-settings-final.png');

const bodyText = await evaluate(`document.body.innerText.replace(/\\n{3,}/g, '\\n\\n').slice(0, 50000)`);
const providerTabClicked = await evaluate(`(() => {
  const target = [...document.querySelectorAll('button, a, div')]
    .find((element) => element.textContent?.trim() === 'Main Providers');
  target?.click();
  return Boolean(target);
})()`);
await new Promise((resolve) => setTimeout(resolve, 1_200));
await capture('installed-personal-ai-providers-final.png');
const providerText = await evaluate(`document.body.innerText.replace(/\\n{3,}/g, '\\n\\n').slice(0, 50000)`);
const evidence = {
  clicked,
  providerTabClicked,
  checks: {
    founderSettings: /Founder Settings|Personal AI(?: Settings)?/i.test(bodyText),
    voidSettings: /\bVoid\b/i.test(bodyText),
    personalAi: /Personal AI/i.test(bodyText),
    bringYourOwnKey: /Bring your own|API key|provider key/i.test(providerText),
    managedAliases: /founder-os-auto/i.test(bodyText),
    customModel: /Custom model|Add (?:a )?model/i.test(bodyText),
  },
  bodyText,
  providerText,
};
fs.writeFileSync(path.join(outputDir, 'settings-evidence-final.json'), `${JSON.stringify(evidence, null, 2)}\n`);
process.stdout.write(`${JSON.stringify({ clicked, checks: evidence.checks }, null, 2)}\n`);
socket.close();
if (
  evidence.checks.voidSettings
  || Object.entries(evidence.checks).some(([key, value]) => key !== 'voidSettings' && !value)
) {
  process.exitCode = 1;
}
