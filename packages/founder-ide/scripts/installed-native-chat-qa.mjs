import fs from 'node:fs';
import path from 'node:path';

const endpoint = process.env.FOUNDER_IDE_CDP || 'http://127.0.0.1:9452';
const outputDir = path.resolve(process.env.FOUNDER_IDE_QA_DIR || 'artifacts/installed-visual-qa');
const nonce = process.env.FOUNDER_IDE_QA_NONCE || `QA-${Date.now()}`;
const requestedMode = process.env.FOUNDER_IDE_QA_MODE?.trim().toLowerCase() || '';
const requestedTitle = process.env.FOUNDER_IDE_QA_TITLE?.trim().toLowerCase() || '';
const evidenceId = nonce.replace(/[^a-z0-9._-]+/gi, '-').slice(0, 96);
fs.mkdirSync(outputDir, { recursive: true });

const targets = await fetch(`${endpoint}/json/list`).then((response) => response.json());
const workbenchTargets = targets.filter((candidate) =>
  candidate.type === 'page'
  && candidate.url?.includes('/workbench/workbench.html'),
);
const target = workbenchTargets.find((candidate) => requestedTitle && candidate.title?.toLowerCase().includes(requestedTitle))
  || workbenchTargets[0];
if (!target?.webSocketDebuggerUrl) throw new Error('Clean Founder IDE workspace window was not found.');

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
let composer = await evaluate(`(() => {
  const element = [...document.querySelectorAll('textarea')]
    .find((candidate) => /Enter instructions/i.test(candidate.placeholder || ''));
  if (!element) return null;
  element.focus();
  const rect = element.getBoundingClientRect();
  return rect.toJSON();
})()`);
if (!composer) {
  await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'l', code: 'KeyL', modifiers: 2 });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'l', code: 'KeyL', modifiers: 2 });
  await wait(1_500);
  composer = await evaluate(`(() => {
    const element = [...document.querySelectorAll('textarea')]
      .find((candidate) => /Enter instructions/i.test(candidate.placeholder || ''));
    if (!element) return null;
    element.focus();
    return element.getBoundingClientRect().toJSON();
  })()`);
}
if (!composer) throw new Error('Founder Chat composer was not found.');

if (requestedMode) {
  const modeNames = { normal: 'Chat', gather: 'Gather', agent: 'Agent' };
  const modeDetails = {
    normal: 'Normal chat',
    gather: "Reads files, but can't edit",
    agent: 'Edits files and uses tools',
  };
  if (!modeNames[requestedMode]) throw new Error(`Unknown Founder chat mode: ${requestedMode}`);
  const currentMode = await evaluate(`(() => {
    const composer = [...document.querySelectorAll('textarea')]
      .find((candidate) => /Enter instructions/i.test(candidate.placeholder || ''));
    if (!composer) return null;
    const composerRect = composer.getBoundingClientRect();
    const button = [...document.querySelectorAll('button')]
      .filter((candidate) => ['Chat', 'Gather', 'Agent'].includes(candidate.textContent?.trim() || ''))
      .filter((candidate) => {
        const rect = candidate.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0
          && rect.left >= composerRect.left
          && rect.top >= composerRect.bottom - 90
          && rect.top <= composerRect.bottom + 40;
      })[0];
    button?.click();
    return button?.textContent?.trim() || null;
  })()`);
  if (!currentMode) throw new Error('Founder Chat mode control was not found.');
  if (currentMode !== modeNames[requestedMode]) {
    await wait(300);
    const selected = await evaluate(`(() => {
      const wanted = ${JSON.stringify(modeNames[requestedMode] + modeDetails[requestedMode])};
      const option = [...document.querySelectorAll('div')]
        .filter((candidate) => candidate.textContent?.replace(/\\s+/g, '').trim() === wanted.replace(/\\s+/g, ''))
        .find((candidate) => {
          const rect = candidate.getBoundingClientRect();
          const style = getComputedStyle(candidate);
          return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
        });
      option?.click();
      return Boolean(option);
    })()`);
    if (!selected) throw new Error(`Founder Chat ${requestedMode} option was not found.`);
    await wait(500);
  }
}

await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'a', code: 'KeyA', modifiers: 2 });
await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', modifiers: 2 });
await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Backspace', code: 'Backspace' });
await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Backspace', code: 'Backspace' });
const prompt = `Reply exactly: Founder AI is online. ${nonce}`;
await send('Input.insertText', { text: prompt });
await wait(250);

const submitted = await evaluate(`(() => {
  const textarea = [...document.querySelectorAll('textarea')]
    .find((candidate) => /Enter instructions/i.test(candidate.placeholder || ''));
  if (!textarea) return false;
  const composerRect = textarea.getBoundingClientRect();
  const button = [...document.querySelectorAll('button')]
    .filter((candidate) => {
      const rect = candidate.getBoundingClientRect();
      const style = getComputedStyle(candidate);
      return rect.width >= 18 && rect.width <= 32
        && rect.height >= 18 && rect.height <= 32
        && rect.right > composerRect.right - 40
        && rect.top >= composerRect.bottom
        && rect.top < composerRect.bottom + 80
        && style.display !== 'none'
        && style.visibility !== 'hidden';
    })
    .sort((left, right) => right.getBoundingClientRect().right - left.getBoundingClientRect().right)[0];
  button?.click();
  return Boolean(button);
})()`);
if (!submitted) throw new Error('Founder Chat send control was not found.');

const startedAt = Date.now();
let chatText = '';
let responseCount = 0;
while (Date.now() - startedAt < 90_000) {
  await wait(700);
  chatText = await evaluate(`document.body.innerText.slice(-24000)`);
  responseCount = chatText.split(`Founder AI is online. ${nonce}`).length - 1;
  if (responseCount >= 2 && /Founder route/i.test(chatText)) break;
}
const latencyMs = Date.now() - startedAt;
const screenshot = await send('Page.captureScreenshot', {
  format: 'png',
  fromSurface: true,
  captureBeyondViewport: false,
});
const screenshotPath = path.join(outputDir, `installed-founder-chat-native-${evidenceId}.png`);
fs.writeFileSync(screenshotPath, Buffer.from(screenshot.data, 'base64'));

const route = chatText.match(/Founder route\s*[·.]\s*([^\r\n]+(?:\r?\n[^\r\n]+){0,2})/i)?.[1]?.replace(/\s+/g, ' ').trim() || null;
const ignoredConsoleErrors = consoleErrors.filter((message) =>
  /Timed out getting tasks from\s+(?:typescript|npm)/i.test(message),
);
const criticalConsoleErrors = consoleErrors.filter((message) =>
  !/Timed out getting tasks from\s+(?:typescript|npm)/i.test(message),
);
const evidence = {
  nonce,
  latencyMs,
  route,
  checks: {
    submitted,
    responseVisible: responseCount >= 2,
    founderRoute: /Founder route/i.test(chatText),
    deepSeekV4: /deepseek\/deepseek-v4-(?:pro|flash)/i.test(chatText),
    errorVisible: /Founder OS gateway returned|Authentication Fails|Bad gateway|invalid_request_error|Founder could not (?:send|reach)|Founder AI is temporarily unavailable|Founder session needs to be renewed/i.test(chatText),
  },
  chatTail: chatText,
  consoleErrors,
  ignoredConsoleErrors,
  criticalConsoleErrors,
  pageErrors,
};
const evidencePath = path.join(outputDir, `installed-founder-chat-native-${evidenceId}.json`);
fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
process.stdout.write(`${JSON.stringify({ nonce, latencyMs, route, checks: evidence.checks, screenshotPath, evidencePath, ignoredConsoleErrors, criticalConsoleErrors, pageErrors }, null, 2)}\n`);
socket.close();
if (
  !evidence.checks.responseVisible
  || !evidence.checks.founderRoute
  || !evidence.checks.deepSeekV4
  || evidence.checks.errorVisible
  || criticalConsoleErrors.length > 0
  || pageErrors.length > 0
) process.exitCode = 1;
