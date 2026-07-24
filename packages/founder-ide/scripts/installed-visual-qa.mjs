import fs from 'node:fs';
import path from 'node:path';

const endpoint = process.env.FOUNDER_IDE_CDP || 'http://127.0.0.1:9452';
const outputDir = path.resolve(process.env.FOUNDER_IDE_QA_DIR || 'artifacts/installed-visual-qa');
const requestedTitle = process.env.FOUNDER_IDE_QA_TITLE?.trim().toLowerCase() || '';
const requestedWidth = Number.parseInt(process.env.FOUNDER_IDE_QA_WIDTH || '', 10);
const requestedHeight = Number.parseInt(process.env.FOUNDER_IDE_QA_HEIGHT || '', 10);
const hasRequestedViewport = Number.isFinite(requestedWidth)
  && Number.isFinite(requestedHeight)
  && requestedWidth >= 360
  && requestedHeight >= 480;
const viewportSuffix = hasRequestedViewport ? `-${requestedWidth}x${requestedHeight}` : '';
fs.mkdirSync(outputDir, { recursive: true });

const initialTargets = await fetch(`${endpoint}/json/list`).then((response) => response.json());
const workbenchTargets = initialTargets.filter((candidate) => candidate.url?.includes('/workbench/workbench.html'));
const target = workbenchTargets.find((candidate) => requestedTitle && candidate.title?.toLowerCase().includes(requestedTitle))
  || workbenchTargets.find((candidate) => !/^Founder Settings\b/i.test(candidate.title || ''))
  || workbenchTargets[0];
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
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`${method} timed out`));
    }, 20_000);
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

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function runCommandPalette(command) {
  await send('Input.dispatchKeyEvent', {
    type: 'rawKeyDown', key: 'P', code: 'KeyP', modifiers: 10,
    windowsVirtualKeyCode: 80, nativeVirtualKeyCode: 80,
  });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'P', code: 'KeyP', modifiers: 10 });
  await wait(350);
  await send('Input.insertText', { text: command });
  await wait(500);
  await send('Input.dispatchKeyEvent', {
    type: 'rawKeyDown', key: 'Enter', code: 'Enter',
    windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13,
  });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter' });
  await wait(4_000);
}

await send('Runtime.enable');
await send('Page.enable');
if (hasRequestedViewport) {
  await send('Emulation.setDeviceMetricsOverride', {
    width: requestedWidth,
    height: requestedHeight,
    screenWidth: requestedWidth,
    screenHeight: requestedHeight,
    deviceScaleFactor: 1,
    mobile: false,
  });
}
await send('Page.bringToFront');
if (process.env.FOUNDER_IDE_QA_TRUST === '1') {
  const trusted = await evaluate(`(() => {
    const button = [...document.querySelectorAll('button, a, [role="button"]')]
      .find((candidate) => /Yes, I trust the authors/i.test(candidate.textContent?.trim() || ''));
    button?.click();
    return Boolean(button);
  })()`);
  if (trusted) await wait(2_500);
}
if (process.env.FOUNDER_IDE_QA_UNDO === '1') {
  await send('Input.dispatchKeyEvent', {
    type: 'rawKeyDown', key: 'z', code: 'KeyZ', modifiers: 2,
    windowsVirtualKeyCode: 90, nativeVirtualKeyCode: 90,
  });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'z', code: 'KeyZ', modifiers: 2 });
  await wait(300);
}
// Close any settings webview opened by a prior QA pass so the workbench owns
// keyboard focus, then reveal the product's labeled navigation explicitly.
await send('Input.dispatchKeyEvent', {
  type: 'rawKeyDown', key: 'Escape', code: 'Escape',
  windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27,
});
await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape' });
await wait(250);
await send('Input.dispatchKeyEvent', {
  type: 'rawKeyDown', key: 'w', code: 'KeyW', modifiers: 2,
  windowsVirtualKeyCode: 87, nativeVirtualKeyCode: 87,
});
await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'w', code: 'KeyW', modifiers: 2 });
await wait(500);
await runCommandPalette('Founder: Open control center');

const screenshot = await send('Page.captureScreenshot', {
  format: 'png',
  fromSurface: true,
  captureBeyondViewport: false,
});
fs.writeFileSync(
  path.join(outputDir, `installed-workbench${viewportSuffix}.png`),
  Buffer.from(screenshot.data, 'base64'),
);

const ui = await evaluate(`(() => {
  const visible = (element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const labels = [...document.querySelectorAll('[aria-label], [title]')]
    .filter(visible)
    .map((element) => ({
      tag: element.tagName.toLowerCase(),
      aria: element.getAttribute('aria-label') || '',
      title: element.getAttribute('title') || '',
      text: (element.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 180),
    }))
    .filter((entry, index, all) => {
      const key = JSON.stringify(entry);
      return all.findIndex((candidate) => JSON.stringify(candidate) === key) === index;
    })
    .slice(0, 1_000);
  const bodyText = document.body.innerText.replace(/\\n{3,}/g, '\\n\\n').slice(0, 40_000);
  return {
    title: document.title,
    bodyText,
    labels,
    viewport: {
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
      devicePixelRatio: window.devicePixelRatio,
    },
  };
})()`);

async function readWebviewText(candidate) {
  const webview = new WebSocket(candidate.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    webview.addEventListener('open', resolve, { once: true });
    webview.addEventListener('error', reject, { once: true });
  });
  const text = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Webview text probe timed out')), 10_000);
    webview.addEventListener('message', ({ data }) => {
      const message = JSON.parse(data.toString());
      if (message.id !== 1) return;
      clearTimeout(timer);
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result?.result?.value || '');
    });
    webview.send(JSON.stringify({
      id: 1,
      method: 'Runtime.evaluate',
      params: {
        expression: `(document.querySelector('iframe')?.contentDocument || document).body?.innerText || ''`,
        returnByValue: true,
      },
    }));
  });
  webview.close();
  return text;
}

const currentTargets = await fetch(`${endpoint}/json/list`).then((response) => response.json());
const webviewTexts = await Promise.all(
  currentTargets
    .filter((candidate) => candidate.type === 'iframe' && candidate.webSocketDebuggerUrl)
    .map((candidate) => readWebviewText(candidate).catch(() => '')),
);
const visibleProductText = `${ui.bodyText}\n${webviewTexts.join('\n')}`;
const ignoredConsoleErrors = consoleErrors.filter((message) =>
  /Timed out getting tasks from\s+(?:typescript|npm)/i.test(message),
);
const criticalConsoleErrors = consoleErrors.filter((message) =>
  !/Timed out getting tasks from\s+(?:typescript|npm)/i.test(message),
);

const evidence = {
  endpoint,
  url: target.url,
  ...ui,
  checks: {
    founderVisible: /Founder/i.test(visibleProductText),
    voidSettingsVisible: /Void's Settings/i.test(visibleProductText),
    newChatVisible: /New chat/i.test(visibleProductText),
    projectsVisible: /Projects/i.test(visibleProductText),
    chatsVisible: /Chats/i.test(visibleProductText),
    agentsVisible: /Agents/i.test(visibleProductText),
    graphVisible: /Graph/i.test(visibleProductText),
  },
  consoleErrors,
  ignoredConsoleErrors,
  criticalConsoleErrors,
  pageErrors,
};
fs.writeFileSync(
  path.join(outputDir, `evidence${viewportSuffix}.json`),
  `${JSON.stringify(evidence, null, 2)}\n`,
);
process.stdout.write(`${JSON.stringify({
  title: evidence.title,
  viewport: evidence.viewport,
  checks: evidence.checks,
  labelCount: evidence.labels.length,
  ignoredConsoleErrors,
  criticalConsoleErrors,
  pageErrors,
}, null, 2)}\n`);
socket.close();
if (
  evidence.checks.voidSettingsVisible
  || Object.entries(evidence.checks).some(([key, value]) => key !== 'voidSettingsVisible' && !value)
  || criticalConsoleErrors.length > 0
  || pageErrors.length > 0
) {
  process.exitCode = 1;
}
