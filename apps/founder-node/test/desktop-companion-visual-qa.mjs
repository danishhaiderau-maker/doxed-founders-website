import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const endpoint = process.env.FOUNDER_COMPANION_CDP || 'http://127.0.0.1:9451';
const outputDir = path.resolve(process.env.FOUNDER_COMPANION_QA_DIR || 'artifacts/dragon-visual-qa');
const skipNativeDrag = process.env.FOUNDER_COMPANION_SKIP_NATIVE_DRAG === '1';
fs.mkdirSync(outputDir, { recursive: true });

const targets = await fetch(`${endpoint}/json/list`).then((response) => response.json());
const target = targets.find((candidate) => candidate.url?.endsWith('/companion.html'));
if (!target?.webSocketDebuggerUrl) throw new Error('Founder Dragon companion page was not found.');

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
    }, 15_000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression, awaitPromise = false) {
  const result = await send('Runtime.evaluate', {
    expression,
    awaitPromise,
    returnByValue: true,
  });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return result.result?.value;
}

async function screenshot(name) {
  const result = await send('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false,
  });
  fs.writeFileSync(path.join(outputDir, name), Buffer.from(result.data, 'base64'));
}

await send('Runtime.enable');
await send('Page.enable');
await evaluate(`new Promise((resolve) => {
  const dragon = document.querySelector('[data-dragon]');
  if (dragon?.complete) resolve(true);
  else dragon?.addEventListener('load', () => resolve(true), { once: true });
})`, true);

const baseline = await evaluate(`(() => {
  const body = getComputedStyle(document.body);
  const root = document.querySelector('[data-companion]');
  const dragon = document.querySelector('[data-dragon]');
  const stage = document.querySelector('[data-dragon-stage]');
  return {
    bodyBackground: body.backgroundColor,
    rootClass: root?.className,
    imageSource: dragon?.getAttribute('src'),
    imageComplete: dragon?.complete,
    imageNaturalWidth: dragon?.naturalWidth,
    imageNaturalHeight: dragon?.naturalHeight,
    animationName: stage ? getComputedStyle(stage).animationName : null,
    screen: { x: window.screenX, y: window.screenY },
    available: {
      x: window.screen.availLeft,
      y: window.screen.availTop,
      width: window.screen.availWidth,
      height: window.screen.availHeight,
    },
    outer: { width: window.outerWidth, height: window.outerHeight },
  };
})()`);
await screenshot('dragon-idle.png');

let dragEvidence = {
  tested: false,
  reason: process.platform === 'win32' && skipNativeDrag
    ? 'skipped-by-environment'
    : 'unsupported-platform',
};
if (process.platform === 'win32' && !skipNativeDrag) {
  const moveCursor = (x, y) => execFileSync(
    'powershell.exe',
    [
      '-NoProfile',
      '-NonInteractive',
      '-Command',
      `Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point(${x}, ${y})`,
    ],
    { windowsHide: true },
  );
  const startCursor = {
    x: baseline.screen.x + Math.round(baseline.outer.width * 0.65),
    y: baseline.screen.y + Math.round(baseline.outer.height * 0.7),
  };
  const windowCenter = {
    x: baseline.screen.x + Math.round(baseline.outer.width / 2),
    y: baseline.screen.y + Math.round(baseline.outer.height / 2),
  };
  const displayCenter = {
    x: baseline.available.x + Math.round(baseline.available.width / 2),
    y: baseline.available.y + Math.round(baseline.available.height / 2),
  };
  const expectedDelta = {
    x: windowCenter.x < displayCenter.x ? 48 : -48,
    y: windowCenter.y < displayCenter.y ? 32 : -32,
  };
  moveCursor(startCursor.x, startCursor.y);
  await evaluate(`window.founderCompanion?.beginDrag()`);
  moveCursor(startCursor.x + expectedDelta.x, startCursor.y + expectedDelta.y);
  await evaluate(`window.founderCompanion?.dragToPointer()`);
  await evaluate(`window.founderCompanion?.endDrag()`);
  await new Promise((resolve) => setTimeout(resolve, 300));
  const moved = await evaluate(`({ x: window.screenX, y: window.screenY })`);
  dragEvidence = {
    tested: true,
    before: baseline.screen,
    after: moved,
    delta: { x: moved.x - baseline.screen.x, y: moved.y - baseline.screen.y },
  };
  const movedInExpectedDirection = (
    Math.sign(dragEvidence.delta.x) === Math.sign(expectedDelta.x)
    && Math.sign(dragEvidence.delta.y) === Math.sign(expectedDelta.y)
  );
  if (
    !movedInExpectedDirection
    || Math.abs(dragEvidence.delta.x) < Math.abs(expectedDelta.x) / 2
    || Math.abs(dragEvidence.delta.y) < Math.abs(expectedDelta.y) / 2
  ) {
    throw new Error(`Founder Dragon drag failed: ${JSON.stringify(dragEvidence)}`);
  }
}

const states = {
  working: ['dragon-working.png', 'In flight', 'Building your workspace', 'Founder Dragon is flying toward the next result.'],
  success: ['dragon-success-v3.png', 'Delivered', 'Delivered', 'The requested work has reached its destination.'],
  attention: ['dragon-attention.png', 'Needs you', 'Founder decision needed', 'Review the pending approval to continue.'],
  error: ['dragon-attention.png', 'Blocked', 'Blocked', 'Founder Dragon needs help reaching the destination.'],
};
const stateEvidence = {};
for (const [state, [image, label, title, detail]] of Object.entries(states)) {
  await evaluate(`(() => {
    const root = document.querySelector('[data-companion]');
    const bubble = document.querySelector('[data-bubble]');
    const dragon = document.querySelector('[data-dragon]');
    root.className = ${JSON.stringify(`companion ${state}`)};
    bubble.classList.add('visible');
    dragon.src = ${JSON.stringify(image)};
    document.querySelector('[data-state]').textContent = ${JSON.stringify(label)};
    document.querySelector('[data-title]').textContent = ${JSON.stringify(title)};
    document.querySelector('[data-detail]').textContent = ${JSON.stringify(detail)};
  })()`);
  await evaluate(`document.querySelector('[data-dragon]').decode().then(() => true)`, true);
  await new Promise((resolve) => setTimeout(resolve, 250));
  stateEvidence[state] = await evaluate(`(() => {
    const root = document.querySelector('[data-companion]');
    const stage = document.querySelector('[data-dragon-stage]');
    const bubble = document.querySelector('[data-bubble]');
    const dragon = document.querySelector('[data-dragon]');
    return {
      rootClass: root?.className,
      bubbleOpacity: bubble ? getComputedStyle(bubble).opacity : null,
      animationName: stage ? getComputedStyle(stage).animationName : null,
      imageSource: dragon?.getAttribute('src'),
      imageNaturalWidth: dragon?.naturalWidth,
      imageNaturalHeight: dragon?.naturalHeight,
    };
  })()`);
  await screenshot(`dragon-${state}.png`);
}

await evaluate(`(() => {
  document.querySelector('[data-companion]').className = 'companion idle';
  document.querySelector('[data-bubble]').classList.remove('visible');
  document.querySelector('[data-dragon]').src = 'dragon-idle.png';
  document.querySelector('[data-state]').textContent = 'Resting';
})()`);

const evidence = {
  endpoint,
  url: target.url,
  baseline,
  drag: dragEvidence,
  states: stateEvidence,
  consoleErrors,
  pageErrors,
};
fs.writeFileSync(path.join(outputDir, 'evidence.json'), `${JSON.stringify(evidence, null, 2)}\n`);
process.stdout.write(`${JSON.stringify(evidence, null, 2)}\n`);
socket.close();
