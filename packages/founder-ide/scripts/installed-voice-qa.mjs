import fs from 'node:fs';
import path from 'node:path';

const endpoint = process.env.FOUNDER_IDE_CDP || 'http://127.0.0.1:9452';
const outputDir = path.resolve(
  process.env.FOUNDER_IDE_QA_DIR || 'artifacts/installed-visual-qa',
);
const expectedWords = (
  process.env.FOUNDER_IDE_VOICE_EXPECT || 'founder voice input'
).toLowerCase().split(/\s+/).filter(Boolean);
fs.mkdirSync(outputDir, { recursive: true });

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
const prepared = await evaluate(`(() => {
  const textarea = [...document.querySelectorAll('textarea')]
    .find((candidate) => /Enter instructions/i.test(candidate.placeholder || ''));
  const button = document.querySelector('button[aria-label="Start voice input"]');
  if (!textarea || !button) return false;
  textarea.focus();
  textarea.value = '';
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
  button.click();
  return true;
})()`);
if (!prepared) throw new Error('Founder voice control or composer was not found.');

let listening = false;
const listeningDeadline = Date.now() + 15_000;
while (!listening && Date.now() < listeningDeadline) {
  await wait(250);
  listening = await evaluate(
    `Boolean(document.querySelector('button[aria-label="Stop voice input"]'))`,
  );
}
if (listening) {
  await wait(7_000);
  await evaluate(`(() => {
    const button = document.querySelector('button[aria-label="Stop voice input"]');
    button?.click();
    return Boolean(button);
  })()`);
}

let transcript = '';
let voiceError = '';
const transcriptDeadline = Date.now() + 90_000;
while (Date.now() < transcriptDeadline) {
  await wait(500);
  const state = await evaluate(`(() => {
    const textarea = [...document.querySelectorAll('textarea')]
      .find((candidate) => /Enter instructions/i.test(candidate.placeholder || ''));
    const body = document.body.innerText;
    const error = body.match(/Founder managed voice returned[^\\r\\n]+|Voice transcription[^\\r\\n]+|Microphone access[^\\r\\n]+|Voice input[^\\r\\n]+(?:blocked|failed|unavailable)[^\\r\\n]*/i)?.[0] || '';
    return { transcript: textarea?.value || '', error };
  })()`);
  transcript = state.transcript;
  voiceError = state.error;
  const normalized = transcript.toLowerCase();
  if (expectedWords.filter((word) => normalized.includes(word)).length >= 2 || voiceError) break;
}

const screenshot = await send('Page.captureScreenshot', {
  format: 'png',
  fromSurface: true,
  captureBeyondViewport: false,
});
const screenshotPath = path.join(outputDir, 'installed-voice-final.png');
fs.writeFileSync(screenshotPath, Buffer.from(screenshot.data, 'base64'));

const ignoredConsoleErrors = consoleErrors.filter((message) =>
  /Timed out getting tasks from\s+(?:typescript|npm)/i.test(message),
);
const criticalConsoleErrors = consoleErrors.filter((message) =>
  !/Timed out getting tasks from\s+(?:typescript|npm)/i.test(message),
);
const matchedWords = expectedWords.filter((word) => transcript.toLowerCase().includes(word));
const evidence = {
  listening,
  transcript,
  expectedWords,
  matchedWords,
  voiceError,
  screenshotPath,
  ignoredConsoleErrors,
  criticalConsoleErrors,
  pageErrors,
};
const evidencePath = path.join(outputDir, 'installed-voice-final.json');
fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
process.stdout.write(`${JSON.stringify({ ...evidence, evidencePath }, null, 2)}\n`);
socket.close();

if (
  !listening
  || matchedWords.length < 2
  || voiceError
  || criticalConsoleErrors.length > 0
  || pageErrors.length > 0
) process.exitCode = 1;
