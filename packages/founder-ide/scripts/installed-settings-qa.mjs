import fs from 'node:fs';
import path from 'node:path';

const endpoint = process.env.FOUNDER_IDE_CDP || 'http://127.0.0.1:9452';
const outputDir = path.resolve(process.env.FOUNDER_IDE_QA_DIR || 'artifacts/installed-visual-qa');
const requestedTitle = process.env.FOUNDER_IDE_QA_TITLE?.trim().toLowerCase() || '';
fs.mkdirSync(outputDir, { recursive: true });

const targets = await fetch(`${endpoint}/json/list`).then((response) => response.json());
const workbenchTargets = targets.filter((candidate) => candidate.url?.includes('/workbench/workbench.html'));
const target = workbenchTargets.find((candidate) => requestedTitle && candidate.title?.toLowerCase().includes(requestedTitle))
  || workbenchTargets[0];
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

async function connectFounderSettingsWebview(required = true) {
  const currentTargets = await fetch(`${endpoint}/json/list`).then((response) => response.json());
  for (const candidate of currentTargets.filter((entry) => entry.type === 'iframe' && entry.webSocketDebuggerUrl)) {
    const client = new WebSocket(candidate.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      client.addEventListener('open', resolve, { once: true });
      client.addEventListener('error', reject, { once: true });
    });
    let webviewSequence = 0;
    const webviewPending = new Map();
    client.addEventListener('message', ({ data }) => {
      const message = JSON.parse(data.toString());
      if (!message.id || !webviewPending.has(message.id)) return;
      const { resolve, reject, timer } = webviewPending.get(message.id);
      clearTimeout(timer);
      webviewPending.delete(message.id);
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result);
    });
    const webviewEvaluate = (expression) => {
      const id = ++webviewSequence;
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Webview evaluation timed out')), 20_000);
        webviewPending.set(id, { resolve, reject, timer });
        client.send(JSON.stringify({
          id,
          method: 'Runtime.evaluate',
          params: { expression, returnByValue: true },
        }));
      }).then((response) => response.result?.value);
    };
    const isFounderSettings = await webviewEvaluate(`(() => {
      const root = document.querySelector('iframe')?.contentDocument || document;
      return /Founder Settings/i.test(root.body?.innerText || '');
    })()`);
    if (isFounderSettings) return { client, evaluate: webviewEvaluate };
    client.close();
  }
  if (required) throw new Error('Founder Settings webview was not found.');
  return null;
}

async function waitForFounderSettingsWebview(timeoutMs = 12_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const settings = await connectFounderSettingsWebview(false);
    if (settings) return settings;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Founder Settings webview was not ready within ${timeoutMs}ms.`);
}

await send('Runtime.enable');
let settings = await connectFounderSettingsWebview(false);
let clicked = false;
if (!settings) {
  clicked = await evaluate(`(() => {
    const action = [...document.querySelectorAll('[aria-label]')]
      .find((element) => element.getAttribute('aria-label') === 'Founder: Toggle Personal AI Settings');
    action?.click();
    return Boolean(action);
  })()`);
  if (!clicked) throw new Error('Personal AI settings action was not found.');
  settings = await waitForFounderSettingsWebview();
}
await capture('installed-personal-ai-settings-final.png');

const bodyText = await settings.evaluate(`(() => {
  const root = document.querySelector('iframe')?.contentDocument || document;
  return root.body.innerText.replace(/\\n{3,}/g, '\\n\\n').slice(0, 50000);
})()`);
const providerTabClicked = await settings.evaluate(`(() => {
  const root = document.querySelector('iframe')?.contentDocument || document;
  const target = [...root.querySelectorAll('button')]
    .find((element) => element.textContent?.trim() === 'AI');
  target?.click();
  return Boolean(target);
})()`);
await new Promise((resolve) => setTimeout(resolve, 1_200));
await capture('installed-personal-ai-providers-final.png');
const providerText = await settings.evaluate(`(() => {
  const root = document.querySelector('iframe')?.contentDocument || document;
  return root.body.innerText.replace(/\\n{3,}/g, '\\n\\n').slice(0, 50000);
})()`);
const evidence = {
  clicked,
  providerTabClicked,
  checks: {
    founderSettings: /Founder Settings|Personal AI(?: Settings)?/i.test(bodyText),
    voidSettings: /\bVoid\b/i.test(bodyText),
    personalAi: /Personal AI|Quick switch/i.test(providerText),
    bringYourOwnKey: /Bring your own|API key|provider key|Base URL/i.test(providerText),
    managedAliases: /Founder OS Auto/i.test(providerText),
    customModel: /Name[\s\S]*Base URL[\s\S]*Model ID[\s\S]*API key/i.test(providerText),
  },
  bodyText,
  providerText,
};
fs.writeFileSync(path.join(outputDir, 'settings-evidence-final.json'), `${JSON.stringify(evidence, null, 2)}\n`);
process.stdout.write(`${JSON.stringify({ clicked, checks: evidence.checks }, null, 2)}\n`);
socket.close();
settings.client.close();
if (
  evidence.checks.voidSettings
  || Object.entries(evidence.checks).some(([key, value]) => key !== 'voidSettings' && !value)
) {
  process.exitCode = 1;
}
