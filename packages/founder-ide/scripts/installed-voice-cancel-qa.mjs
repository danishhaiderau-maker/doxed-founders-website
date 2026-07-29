import fs from 'node:fs';
import path from 'node:path';

const endpoint = process.env.FOUNDER_IDE_CDP || 'http://127.0.0.1:9452';
const outputDir = path.resolve(
  process.env.FOUNDER_IDE_QA_DIR || 'artifacts/installed-voice-cancel-qa',
);
const typedMarker = `Keep this typed text ${Date.now()}`;
fs.mkdirSync(outputDir, { recursive: true });

const targets = await fetch(`${endpoint}/json/list`).then((response) => response.json());
const target = targets.find((candidate) =>
  candidate.type === 'page'
  && /\/workbench\/workbench(?:-dev)?\.html/i.test(candidate.url || ''),
);
if (!target?.webSocketDebuggerUrl) {
  throw new Error('Founder IDE workbench page was not found.');
}
const isDevWorkbench = /\/workbench\/workbench-dev\.html/i.test(target.url || '');

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

async function waitFor(expression, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return true;
    await wait(200);
  }
  return false;
}

await send('Runtime.enable');
await send('Page.enable');
await evaluate(`(() => {
  const root = document.querySelector('.notifications-toasts, .notifications-center');
  if (!root) return 0;
  const dismissers = [...root.querySelectorAll('button')].filter(button =>
    /clear notification|close notification/i.test(
      button.getAttribute('aria-label') || button.getAttribute('title') || '',
    )
  );
  const icons = [...root.querySelectorAll('.codicon-close, .codicon-notifications-clear')]
    .map(icon => icon.closest('button') || icon);
  [...new Set([...dismissers, ...icons])].forEach(control => control.click());
  return dismissers.length + icons.length;
})()`);

const composerReady = await waitFor(`Boolean([...document.querySelectorAll('textarea')]
  .find(candidate => /Enter instructions/i.test(candidate.placeholder || '')))`, 60_000);
if (!composerReady) throw new Error('Founder composer did not become ready.');

const syntheticAudioInstalled = await evaluate(`(() => {
  if (!navigator.mediaDevices) return false;
  window.__founderVoiceQaStreams = [];
  window.__founderVoiceQaContexts = [];
  Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
    configurable: true,
    value: async () => {
      const context = new AudioContext();
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      const destination = context.createMediaStreamDestination();
      gain.gain.value = 0.002;
      oscillator.connect(gain);
      gain.connect(destination);
      oscillator.start();
      window.__founderVoiceQaStreams.push(destination.stream);
      window.__founderVoiceQaContexts.push({ context, oscillator });
      return destination.stream;
    },
  });
  return true;
})()`);
if (!syntheticAudioInstalled) throw new Error('Synthetic audio boundary could not be installed.');

const focused = await evaluate(`(() => {
  const textarea = [...document.querySelectorAll('textarea')]
    .find(candidate => /Enter instructions/i.test(candidate.placeholder || ''));
  if (!textarea) return false;
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    'value',
  )?.set;
  setter?.call(textarea, '');
  textarea.dispatchEvent(new InputEvent('input', {
    bubbles: true,
    inputType: 'deleteContentBackward',
    data: null,
  }));
  textarea.focus();
  return Boolean(textarea);
})()`);
if (!focused) throw new Error('Founder composer could not be focused.');
await wait(100);
await send('Input.insertText', { text: typedMarker });
const composerTextBefore = await evaluate(`([...document.querySelectorAll('textarea')]
  .find(candidate => /Enter instructions/i.test(candidate.placeholder || '')))?.value || ''`);

async function startSyntheticRecording() {
  const clicked = await evaluate(`(() => {
    const button = document.querySelector('button[aria-label="Start voice input"]');
    button?.click();
    return Boolean(button);
  })()`);
  if (!clicked) return false;
  return waitFor(`Boolean(
    document.querySelector('button[aria-label="Stop voice input"]')
    && document.querySelector('button[aria-label="Cancel voice input and keep typed text"]')
  )`);
}

const explicitListening = await startSyntheticRecording();
if (!explicitListening) throw new Error('Synthetic recording did not enter listening state.');
const listeningScreenshot = await send('Page.captureScreenshot', {
  format: 'png',
  fromSurface: true,
  captureBeyondViewport: false,
});
const listeningScreenshotPath = path.join(outputDir, 'installed-voice-listening.png');
fs.writeFileSync(listeningScreenshotPath, Buffer.from(listeningScreenshot.data, 'base64'));

const explicitCancelClicked = await evaluate(`(() => {
  const button = document.querySelector(
    'button[aria-label="Cancel voice input and keep typed text"]',
  );
  button?.click();
  return Boolean(button);
})()`);
const explicitCancelled = explicitCancelClicked && await waitFor(`Boolean(
  document.querySelector('button[aria-label="Start voice input"]')
  && !document.querySelector('button[aria-label="Cancel voice input and keep typed text"]')
)`);

const escapeListening = await startSyntheticRecording();
if (!escapeListening) throw new Error('Escape cancellation recording did not start.');
await evaluate(`([...document.querySelectorAll('textarea')]
  .find(candidate => /Enter instructions/i.test(candidate.placeholder || '')))?.focus()`);
await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape' });
await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape' });
const escapeCancelled = await waitFor(`Boolean(
  document.querySelector('button[aria-label="Start voice input"]')
  && !document.querySelector('button[aria-label="Cancel voice input and keep typed text"]')
)`);

const finalState = await evaluate(`(() => {
  const textarea = [...document.querySelectorAll('textarea')]
    .find(candidate => /Enter instructions/i.test(candidate.placeholder || ''));
  const streams = window.__founderVoiceQaStreams || [];
  const trackStates = streams.flatMap(stream =>
    stream.getTracks().map(track => track.readyState)
  );
  const body = document.body.innerText;
  return {
    composerText: textarea?.value || '',
    trackStates,
    errorVisible: /Voice input[^\\r\\n]+(?:failed|blocked)|Voice transcription[^\\r\\n]+failed/i.test(body),
  };
})()`);

await evaluate(`Promise.all((window.__founderVoiceQaContexts || []).map(async entry => {
  try { entry.oscillator.stop(); } catch {}
  try { await entry.context.close(); } catch {}
}))`);

const finalScreenshot = await send('Page.captureScreenshot', {
  format: 'png',
  fromSurface: true,
  captureBeyondViewport: false,
});
const finalScreenshotPath = path.join(outputDir, 'installed-voice-cancel-final.png');
fs.writeFileSync(finalScreenshotPath, Buffer.from(finalScreenshot.data, 'base64'));

const ignoredConsoleErrors = consoleErrors.filter((message) =>
  /Timed out getting tasks from\s+(?:typescript|npm)/i.test(message)
  || (
    isDevWorkbench
    && (
      /LocalProcessExtensionHost.*did not start in 10 seconds/i.test(message)
      || /Aborted onWillSaveTextDocument-event after 1750ms/i.test(message)
    )
  ),
);
const criticalConsoleErrors = consoleErrors.filter((message) =>
  !ignoredConsoleErrors.includes(message),
);
const evidence = {
  checks: {
    explicitListening,
    explicitCancelled,
    escapeListening,
    escapeCancelled,
    typedTextPreserved: finalState.composerText === composerTextBefore,
    tracksStopped:
      finalState.trackStates.length === 2
      && finalState.trackStates.every((state) => state === 'ended'),
    errorVisible: finalState.errorVisible,
  },
  listeningScreenshotPath,
  finalScreenshotPath,
  ignoredConsoleErrors,
  criticalConsoleErrors,
  pageErrors,
};
const evidencePath = path.join(outputDir, 'installed-voice-cancel-final.json');
fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
process.stdout.write(`${JSON.stringify({ ...evidence, evidencePath }, null, 2)}\n`);
socket.close();

if (
  !evidence.checks.explicitListening
  || !evidence.checks.explicitCancelled
  || !evidence.checks.escapeListening
  || !evidence.checks.escapeCancelled
  || !evidence.checks.typedTextPreserved
  || !evidence.checks.tracksStopped
  || evidence.checks.errorVisible
  || criticalConsoleErrors.length > 0
  || pageErrors.length > 0
) process.exitCode = 1;
