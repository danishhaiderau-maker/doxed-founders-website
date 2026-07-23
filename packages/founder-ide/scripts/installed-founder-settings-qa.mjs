import fs from 'node:fs';
import path from 'node:path';

const endpoint = process.env.FOUNDER_IDE_CDP || 'http://127.0.0.1:9452';
const outputDir = path.resolve(process.env.FOUNDER_IDE_QA_DIR || 'artifacts/installed-visual-qa');
fs.mkdirSync(outputDir, { recursive: true });

async function connect(target) {
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
  const send = (method, params = {}) => {
    const id = ++sequence;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`${method} timed out`)), 20_000);
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params }));
    });
  };
  const evaluate = async (expression) => {
    const response = await send('Runtime.evaluate', { expression, returnByValue: true });
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.text);
    return response.result?.value;
  };
  return { socket, send, evaluate };
}

async function inspectWebview(target) {
  const client = await connect(target);
  const result = await client.evaluate(`(() => {
    const root = document.querySelector('iframe')?.contentDocument || document;
    return {
      text: root.body?.innerText?.replace(/\\n{3,}/g, '\\n\\n').slice(0, 30000) || '',
      hasUsageAction: Boolean(root.querySelector('[data-action="showUsage"]')),
      isFounderSettings: /Founder Settings/.test(root.body?.innerText || ''),
    };
  })()`);
  return { ...client, ...result };
}

const initialTargets = await fetch(`${endpoint}/json/list`).then((response) => response.json());
let usageSource;
for (const target of initialTargets.filter((candidate) => candidate.type === 'iframe')) {
  const candidate = await inspectWebview(target);
  if (candidate.hasUsageAction) {
    usageSource = candidate;
    break;
  }
  candidate.socket.close();
}
if (!usageSource) throw new Error('Founder Usage action was not found.');
await usageSource.evaluate(`(() => {
  const root = document.querySelector('iframe')?.contentDocument || document;
  root.querySelector('[data-action="showUsage"]')?.click();
  return true;
})()`);
usageSource.socket.close();
await new Promise((resolve) => setTimeout(resolve, 2_000));

const currentTargets = await fetch(`${endpoint}/json/list`).then((response) => response.json());
let settingsText = '';
let settingsClient;
for (const target of currentTargets.filter((candidate) => candidate.type === 'iframe')) {
  const candidate = await inspectWebview(target);
  if (candidate.isFounderSettings) {
    settingsText = candidate.text;
    settingsClient = candidate;
  } else {
    candidate.socket.close();
  }
  if (settingsText) break;
}
if (!settingsText || !settingsClient) throw new Error('Founder Settings webview did not open from Usage.');

const workbenchTarget = currentTargets.find((candidate) => candidate.type === 'page' && candidate.url?.includes('/workbench/workbench.html'));
if (!workbenchTarget) throw new Error('Founder IDE workbench page was not found.');
const workbench = await connect(workbenchTarget);
await workbench.send('Page.enable');
const screenshot = await workbench.send('Page.captureScreenshot', {
  format: 'png',
  fromSurface: true,
  captureBeyondViewport: false,
});
fs.writeFileSync(path.join(outputDir, 'installed-founder-settings-usage-final.png'), Buffer.from(screenshot.data, 'base64'));

await settingsClient.evaluate(`(() => {
  const root = document.querySelector('iframe')?.contentDocument || document;
  root.querySelector('[data-tab="ai"]')?.click();
  return true;
})()`);
await new Promise((resolve) => setTimeout(resolve, 700));
const aiText = await settingsClient.evaluate(`(() => {
  const root = document.querySelector('iframe')?.contentDocument || document;
  return root.body?.innerText?.replace(/\\n{3,}/g, '\\n\\n').slice(0, 30000) || '';
})()`);
const aiScreenshot = await workbench.send('Page.captureScreenshot', {
  format: 'png',
  fromSurface: true,
  captureBeyondViewport: false,
});
fs.writeFileSync(path.join(outputDir, 'installed-founder-settings-ai-final.png'), Buffer.from(aiScreenshot.data, 'base64'));
settingsClient.socket.close();
workbench.socket.close();

const evidence = {
  checks: {
    founderSettings: /Founder Settings/i.test(settingsText),
    freePlan: /Founder Free/i.test(settingsText),
    freeWeeklyQuota: /200(?:,000|K) weighted tokens\/week/i.test(settingsText),
    planUsage: /Plan and usage/i.test(settingsText),
    byok: /Bring your own key/i.test(aiText),
    account: /Identity and Node/i.test(settingsText),
  },
  settingsText,
  aiText,
};
fs.writeFileSync(path.join(outputDir, 'installed-founder-settings-evidence-final.json'), `${JSON.stringify(evidence, null, 2)}\n`);
process.stdout.write(`${JSON.stringify(evidence.checks, null, 2)}\n`);
if (Object.values(evidence.checks).some((value) => !value)) process.exitCode = 1;
