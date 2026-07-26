import fs from 'node:fs';
import path from 'node:path';

const endpoint = process.env.FOUNDER_IDE_CDP || 'http://127.0.0.1:9453';
const outputDir = path.resolve(
  process.env.FOUNDER_IDE_QA_DIR || 'artifacts/installed-projects-chats-qa',
);
fs.mkdirSync(outputDir, { recursive: true });

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function targets() {
  return fetch(`${endpoint}/json/list`).then((response) => response.json());
}

async function withTarget(target, action) {
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
    const request = pending.get(message.id);
    clearTimeout(request.timer);
    pending.delete(message.id);
    if (message.error) request.reject(new Error(message.error.message));
    else request.resolve(message.result);
  });
  const send = (method, params = {}) => {
    const id = ++sequence;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`${method} timed out`));
      }, 20_000);
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params }));
    });
  };
  const evaluate = async (expression) => {
    const response = await send('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.text);
    return response.result?.value;
  };
  try {
    await send('Runtime.enable');
    return await action({ evaluate, send });
  } finally {
    socket.close();
  }
}

async function workbenchTarget() {
  const target = (await targets()).find(
    (candidate) =>
      candidate.type === 'page'
      && candidate.url?.includes('/workbench/workbench.html'),
  );
  if (!target) throw new Error('Founder IDE workbench target not found');
  return target;
}

async function clickFounderAction(action) {
  const candidates = (await targets()).filter(
    (target) =>
      target.webSocketDebuggerUrl
      && (target.type === 'iframe' || target.type === 'page'),
  );
  for (const candidate of candidates) {
    const clicked = await withTarget(candidate, ({ evaluate }) => evaluate(`(() => {
      const root = document.querySelector('iframe')?.contentDocument || document;
      const element = root.querySelector(${JSON.stringify(`[data-action="${action}"]`)});
      element?.click();
      return Boolean(element);
    })()`)).catch(() => false);
    if (clicked) return true;
  }
  return false;
}

async function inspectWorkbench() {
  return withTarget(await workbenchTarget(), ({ evaluate }) => evaluate(`(() => {
    const bodyText = document.body?.innerText || '';
    const labels = [...document.querySelectorAll('[aria-label], [title], [placeholder]')]
      .flatMap((element) => [
        element.getAttribute('aria-label') || '',
        element.getAttribute('title') || '',
        element.getAttribute('placeholder') || '',
      ])
      .filter(Boolean);
    return { bodyText, labels };
  })()`));
}

async function capture(name) {
  await withTarget(await workbenchTarget(), async ({ send }) => {
    const screenshot = await send('Page.captureScreenshot', {
      format: 'png',
      fromSurface: true,
      captureBeyondViewport: false,
    });
    fs.writeFileSync(
      path.join(outputDir, `${name}.png`),
      Buffer.from(screenshot.data, 'base64'),
    );
  });
}

async function pressEscape() {
  await withTarget(await workbenchTarget(), async ({ send }) => {
    const key = {
      key: 'Escape',
      code: 'Escape',
      windowsVirtualKeyCode: 27,
      nativeVirtualKeyCode: 27,
    };
    await send('Input.dispatchKeyEvent', { ...key, type: 'keyDown' });
    await send('Input.dispatchKeyEvent', { ...key, type: 'keyUp' });
  });
}

const evidence = {
  endpoint,
  projects: {
    clicked: false,
    titleVisible: false,
    searchVisible: false,
    openFolderVisible: false,
  },
  chats: {
    clicked: false,
    nativeComposerVisible: false,
    secondBrainVisible: false,
    attachmentVisible: false,
    microphoneVisible: false,
  },
};

evidence.projects.clicked = await clickFounderAction('openProjects');
await wait(1_500);
const projects = await inspectWorkbench();
evidence.projects.titleVisible = /Founder Projects/i.test(projects.bodyText);
evidence.projects.searchVisible = /Search projects by name or location/i.test(
  projects.bodyText,
) || projects.labels.some((label) =>
  /Search projects by name or location/i.test(label));
evidence.projects.openFolderVisible = projects.labels.some((label) =>
  /Open another folder|Open project/i.test(label));
await capture('installed-founder-projects');
await pressEscape();

evidence.chats.clicked = await clickFounderAction('openChats');
await wait(1_500);
const chats = await inspectWorkbench();
evidence.chats.nativeComposerVisible = /Enter instructions|founder-os-auto/i.test(
  chats.bodyText,
);
evidence.chats.secondBrainVisible = /Second brain/i.test(chats.bodyText);
evidence.chats.attachmentVisible = chats.labels.some((label) =>
  /attach|image/i.test(label));
evidence.chats.microphoneVisible = chats.labels.some((label) =>
  /microphone|voice/i.test(label));
await capture('installed-founder-chats');

fs.writeFileSync(
  path.join(outputDir, 'installed-projects-chats-evidence.json'),
  `${JSON.stringify(evidence, null, 2)}\n`,
);
process.stdout.write(`${JSON.stringify(evidence, null, 2)}\n`);

if (
  !Object.values(evidence.projects).every(Boolean)
  || !Object.values(evidence.chats).every(Boolean)
) {
  process.exitCode = 1;
}
