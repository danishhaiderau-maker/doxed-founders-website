import fs from 'node:fs';
import path from 'node:path';

const endpoint = process.env.FOUNDER_IDE_CDP || 'http://127.0.0.1:9452';
const outputDir = path.resolve(process.env.FOUNDER_IDE_QA_DIR || 'artifacts/installed-visual-qa');
const requestedTitle = process.env.FOUNDER_IDE_QA_TITLE?.trim().toLowerCase() || '';
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
const initialWorkbenchTarget = initialTargets.find((candidate) =>
  candidate.type === 'page'
  && candidate.url?.includes('/workbench/workbench.html')
  && (!requestedTitle || candidate.title?.toLowerCase().includes(requestedTitle)),
) || initialTargets.find((candidate) => candidate.type === 'page' && candidate.url?.includes('/workbench/workbench.html'));
if (!initialWorkbenchTarget) throw new Error('Founder IDE workbench page was not found.');
let usageSource;
for (const target of initialTargets.filter((candidate) =>
  candidate.type === 'iframe' && candidate.parentId === initialWorkbenchTarget.id,
)) {
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
let settingsText = '';
let settingsClient;
let currentTargets = [];
const settingsDeadline = Date.now() + 12_000;
while (!settingsClient && Date.now() < settingsDeadline) {
  currentTargets = await fetch(`${endpoint}/json/list`).then((response) => response.json());
  for (const target of currentTargets.filter((candidate) =>
    candidate.type === 'iframe' && candidate.parentId === initialWorkbenchTarget.id,
  )) {
    const candidate = await inspectWebview(target);
    if (
      candidate.isFounderSettings
      && /Plan and usage|Identity and Node|Founder Free/i.test(candidate.text)
    ) {
      settingsText = candidate.text;
      settingsClient = candidate;
      break;
    }
    candidate.socket.close();
  }
  if (!settingsClient) await new Promise((resolve) => setTimeout(resolve, 500));
}
if (!settingsText || !settingsClient) throw new Error('Founder Settings webview did not open from Usage.');

const workbenchTarget = currentTargets.find((candidate) => candidate.id === initialWorkbenchTarget.id);
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

const initialComposerRoute = await workbench.evaluate(`(() => {
  const workModes = new Set(['Ask', 'Plan', 'Build', 'Debug', 'Team']);
  return [...document.querySelectorAll('button')]
    .map(button => button.innerText.trim())
    .find(label =>
      label
      && !workModes.has(label)
      && buttonLikeRouteLabel(label)
    ) || null;

  function buttonLikeRouteLabel(label) {
    if (label.startsWith('founder-os-')) return true;
    const matchingButton = [...document.querySelectorAll('button')]
      .find(button => button.innerText.trim() === label);
    return Boolean(
      matchingButton
      && String(matchingButton.className).includes('void-whitespace-nowrap')
      && String(matchingButton.className).includes('void-w-full')
    );
  }
})()`);
if (!initialComposerRoute) throw new Error('Founder composer route picker was not found.');

const pickerOpened = await workbench.evaluate(`(() => {
  const button = [...document.querySelectorAll('button')]
    .find(candidate => candidate.innerText.trim() === ${JSON.stringify(initialComposerRoute)});
  button?.click();
  return Boolean(button);
})()`);
await new Promise((resolve) => setTimeout(resolve, 400));
const pickerRoutes = await workbench.evaluate(`(() => {
  const rows = [...document.querySelectorAll('div')]
    .filter(element => String(element.className).includes('void-cursor-pointer'));
  return [...new Set(rows.map(row => row.innerText.trim().split('\\n')[0]).filter(Boolean))];
})()`);
const openPickerScreenshot = await workbench.send('Page.captureScreenshot', {
  format: 'png',
  fromSurface: true,
  captureBeyondViewport: false,
});
fs.writeFileSync(
  path.join(outputDir, 'installed-founder-route-picker-final.png'),
  Buffer.from(openPickerScreenshot.data, 'base64'),
);
const temporaryRoute =
  initialComposerRoute === 'founder-os-auto' ? 'founder-os-fast' : 'founder-os-auto';
const temporarySelected = await workbench.evaluate(`(() => {
  const row = [...document.querySelectorAll('div')]
    .find(element =>
      String(element.className).includes('void-cursor-pointer')
      && element.innerText.trim().split('\\n')[0] === ${JSON.stringify(temporaryRoute)}
    );
  row?.click();
  return Boolean(row);
})()`);
await new Promise((resolve) => setTimeout(resolve, 700));
const routeAfterSwitch = await workbench.evaluate(`(() => {
  const workModes = new Set(['Ask', 'Plan', 'Build', 'Debug', 'Team']);
  return [...document.querySelectorAll('button')]
    .map(button => button.innerText.trim())
    .find(label => label && !workModes.has(label) && (
      label.startsWith('founder-os-')
      || [...document.querySelectorAll('button')].some(button =>
        button.innerText.trim() === label
        && String(button.className).includes('void-whitespace-nowrap')
        && String(button.className).includes('void-w-full')
      )
    )) || null;
})()`);

const restorePickerOpened = await workbench.evaluate(`(() => {
  const button = [...document.querySelectorAll('button')]
    .find(candidate => candidate.innerText.trim() === ${JSON.stringify(routeAfterSwitch)});
  button?.click();
  return Boolean(button);
})()`);
await new Promise((resolve) => setTimeout(resolve, 300));
const initialRouteRestored = await workbench.evaluate(`(() => {
  const row = [...document.querySelectorAll('div')]
    .find(element =>
      String(element.className).includes('void-cursor-pointer')
      && element.innerText.trim().split('\\n')[0] === ${JSON.stringify(initialComposerRoute)}
    );
  row?.click();
  return Boolean(row);
})()`);
await new Promise((resolve) => setTimeout(resolve, 700));
const finalComposerRoute = await workbench.evaluate(`(() => {
  const workModes = new Set(['Ask', 'Plan', 'Build', 'Debug', 'Team']);
  return [...document.querySelectorAll('button')]
    .map(button => button.innerText.trim())
    .find(label => label && !workModes.has(label) && (
      label.startsWith('founder-os-')
      || [...document.querySelectorAll('button')].some(button =>
        button.innerText.trim() === label
        && String(button.className).includes('void-whitespace-nowrap')
        && String(button.className).includes('void-w-full')
      )
    )) || null;
})()`);

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
    pickerOpened,
    pickerHasManagedRoutes: [
      'founder-os-auto',
      'founder-os-fast',
      'founder-os-reasoning',
      'founder-os-code',
    ].every(route => pickerRoutes.includes(route)),
    pickerHasInitialRoute: pickerRoutes.includes(initialComposerRoute),
    temporarySelected,
    temporaryRouteApplied: routeAfterSwitch === temporaryRoute,
    restorePickerOpened,
    initialRouteRestored,
    initialRoutePreserved: finalComposerRoute === initialComposerRoute,
  },
  routePicker: {
    initialComposerRoute,
    pickerRoutes,
    temporaryRoute,
    routeAfterSwitch,
    finalComposerRoute,
  },
  settingsText,
  aiText,
};
fs.writeFileSync(path.join(outputDir, 'installed-founder-settings-evidence-final.json'), `${JSON.stringify(evidence, null, 2)}\n`);
process.stdout.write(`${JSON.stringify(evidence.checks, null, 2)}\n`);
if (Object.values(evidence.checks).some((value) => !value)) process.exitCode = 1;
