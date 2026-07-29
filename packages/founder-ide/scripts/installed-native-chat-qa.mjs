import fs from 'node:fs';
import path from 'node:path';

const endpoint = process.env.FOUNDER_IDE_CDP || 'http://127.0.0.1:9452';
const outputDir = path.resolve(process.env.FOUNDER_IDE_QA_DIR || 'artifacts/installed-visual-qa');
const nonce = process.env.FOUNDER_IDE_QA_NONCE || `QA-${Date.now()}`;
const requestedMode = process.env.FOUNDER_IDE_QA_MODE?.trim().toLowerCase() || '';
const requestedRoute = process.env.FOUNDER_IDE_QA_ROUTE?.trim().toLowerCase() || '';
const requestedTitle = process.env.FOUNDER_IDE_QA_TITLE?.trim().toLowerCase() || '';
const trustWorkspace = process.env.FOUNDER_IDE_QA_TRUST_WORKSPACE === '1';
const startNewChat = process.env.FOUNDER_IDE_QA_NEW_CHAT === '1';
const expectedModel = process.env.FOUNDER_IDE_QA_EXPECT_MODEL?.trim().toLowerCase() || '';
const expectedRouteKind = process.env.FOUNDER_IDE_QA_ROUTE_KIND?.trim().toLowerCase()
  || (requestedRoute ? 'managed' : 'any');
if (!['any', 'managed', 'personal', 'local'].includes(expectedRouteKind)) {
  throw new Error(`Unsupported FOUNDER_IDE_QA_ROUTE_KIND: ${expectedRouteKind}`);
}
const evidenceId = nonce.replace(/[^a-z0-9._-]+/gi, '-').slice(0, 96);
fs.mkdirSync(outputDir, { recursive: true });

const targets = await fetch(`${endpoint}/json/list`).then((response) => response.json());
async function clickFounderNewChat(candidates) {
  for (const candidate of candidates) {
    if (!candidate.webSocketDebuggerUrl || !['page', 'iframe'].includes(candidate.type)) continue;
    const candidateSocket = new WebSocket(candidate.webSocketDebuggerUrl);
    try {
      await new Promise((resolve, reject) => {
        candidateSocket.addEventListener('open', resolve, { once: true });
        candidateSocket.addEventListener('error', reject, { once: true });
      });
      const result = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Founder New chat lookup timed out.')), 10_000);
        candidateSocket.addEventListener('message', ({ data }) => {
          const message = JSON.parse(data.toString());
          if (message.id !== 1) return;
          clearTimeout(timer);
          resolve(message.result?.result?.value === true);
        });
        candidateSocket.send(JSON.stringify({
          id: 1,
          method: 'Runtime.evaluate',
          params: {
            expression: `(() => {
              const root = document.querySelector('iframe')?.contentDocument || document;
              const control = root.querySelector('[data-action="newChat"]');
              if (!control) return false;
              const style = (root.defaultView || window).getComputedStyle(control);
              const rect = control.getBoundingClientRect();
              if (
                style.display === 'none'
                || style.visibility === 'hidden'
                || rect.width <= 0
                || rect.height <= 0
              ) return false;
              control.click();
              return true;
            })()`,
            returnByValue: true,
          },
        }));
      });
      if (result) return true;
    } catch {
      // Another target may own the Founder hub.
    } finally {
      candidateSocket.close();
    }
  }
  return false;
}
if (startNewChat) {
  const openedNewChat = await clickFounderNewChat(targets);
  if (!openedNewChat) throw new Error('Founder New chat control was not found.');
  await new Promise((resolve) => setTimeout(resolve, 1_500));
}
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
if (trustWorkspace) {
  let trusted = await evaluate(`(() => {
    const candidates = [...document.querySelectorAll('*')]
      .filter((candidate) => {
        const rect = candidate.getBoundingClientRect();
        const style = getComputedStyle(candidate);
        return /Trust the authors of all files|Yes, I trust the authors/i.test(
          candidate.textContent || '',
        )
          && rect.width > 0
          && rect.height > 0
          && style.visibility !== 'hidden'
          && style.display !== 'none';
      })
      .map((candidate) => {
        const describedControl = candidate
          .closest('.monaco-description-button')
          ?.querySelector('button, [role="button"], a');
        return describedControl
          || candidate.closest('button, [role="button"], a')
          || candidate;
      });
    const control = [...new Set(candidates)]
      .sort((left, right) => {
        const leftRect = left.getBoundingClientRect();
        const rightRect = right.getBoundingClientRect();
        return (leftRect.width * leftRect.height) - (rightRect.width * rightRect.height);
      })[0];
    control?.click();
    return Boolean(control);
  })()`);
  if (trusted) await wait(1_500);
  let trustDialogVisible = await evaluate(`(() => {
    return [...document.querySelectorAll('[role="dialog"], .monaco-dialog-box')]
      .some((candidate) => {
        const rect = candidate.getBoundingClientRect();
        return /Do you trust the authors of the files in this folder/i.test(
          candidate.textContent || '',
        ) && rect.width > 0 && rect.height > 0;
      });
  })()`);
  if (trustDialogVisible) {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Tab', code: 'Tab' });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Tab', code: 'Tab' });
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter' });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter' });
    await wait(1_500);
    trusted = true;
    trustDialogVisible = await evaluate(`(() => {
      return [...document.querySelectorAll('[role="dialog"], .monaco-dialog-box')]
        .some((candidate) => {
          const rect = candidate.getBoundingClientRect();
          return /Do you trust the authors of the files in this folder/i.test(
            candidate.textContent || '',
          ) && rect.width > 0 && rect.height > 0;
        });
    })()`);
  }
  if (trustDialogVisible) {
    throw new Error('Founder IDE workspace-trust dialog could not be approved.');
  }
  if (trusted) await wait(6_000);
}
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

if (requestedRoute) {
  const knownRoutes = [
    'founder-os-auto',
    'founder-os-fast',
    'founder-os-reasoning',
    'founder-os-code',
  ];
  if (!knownRoutes.includes(requestedRoute)) {
    throw new Error(`Unknown Founder chat route: ${requestedRoute}`);
  }
  const selectVisibleRoute = () => evaluate(`(() => {
    const wanted = ${JSON.stringify(requestedRoute)};
    const candidates = [...document.querySelectorAll('*')]
      .filter((candidate) => {
        const rect = candidate.getBoundingClientRect();
        const style = getComputedStyle(candidate);
        const text = candidate.textContent?.replace(/\\s+/g, ' ').trim().toLowerCase() || '';
        return (text === wanted || text.startsWith(wanted + ' '))
          && rect.width > 0
          && rect.height > 0
          && rect.right > 0
          && rect.bottom > 0
          && rect.left < window.innerWidth
          && rect.top < window.innerHeight
          && style.visibility !== 'hidden'
          && style.display !== 'none'
          && style.opacity !== '0'
          && style.pointerEvents !== 'none';
      })
      .map((candidate) =>
        candidate.closest('[role="option"], button, [role="menuitem"]') || candidate
      );
    const option = [...new Set(candidates)]
      .sort((left, right) => {
        const leftRect = left.getBoundingClientRect();
        const rightRect = right.getBoundingClientRect();
        return (leftRect.width * leftRect.height) - (rightRect.width * rightRect.height);
      })[0];
    option?.click();
    return Boolean(option);
  })()`);
  let selected = await selectVisibleRoute();
  if (!selected) {
    const opened = await evaluate(`(() => {
      const composer = [...document.querySelectorAll('textarea')]
        .find((candidate) => /Enter instructions/i.test(candidate.placeholder || ''));
      if (!composer) return false;
      const composerRect = composer.getBoundingClientRect();
      const routePrefixes = ${JSON.stringify(knownRoutes)};
      const button = [...document.querySelectorAll('button')]
        .filter((candidate) => {
          const text = candidate.textContent?.trim().toLowerCase() || '';
          return text === 'glm' || routePrefixes.some((route) => text === route);
        })
        .filter((candidate) => {
          const rect = candidate.getBoundingClientRect();
          return rect.width > 0
            && rect.height > 0
            && rect.left >= composerRect.left
            && rect.top >= composerRect.bottom - 40
            && rect.top <= composerRect.bottom + 80;
        })[0];
      button?.click();
      return Boolean(button);
    })()`);
    if (!opened) throw new Error('Founder Chat route control was not found.');
    await wait(300);
    selected = await selectVisibleRoute();
  }
  if (!selected) throw new Error(`Founder Chat route ${requestedRoute} was not found.`);
  await wait(500);
}

if (requestedMode) {
  await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape' });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape' });
  await wait(150);
  const modeNames = {
    ask: 'Ask',
    plan: 'Plan',
    build: 'Build',
    debug: 'Debug',
    team: 'Team',
    normal: 'Chat',
    gather: 'Gather',
    agent: 'Agent',
  };
  if (!modeNames[requestedMode]) throw new Error(`Unknown Founder chat mode: ${requestedMode}`);
  const currentMode = await evaluate(`(() => {
    const composer = [...document.querySelectorAll('textarea')]
      .find((candidate) => /Enter instructions/i.test(candidate.placeholder || ''));
    if (!composer) return null;
    const composerRect = composer.getBoundingClientRect();
    const labels = ${JSON.stringify(Object.values(modeNames))};
    const button = [...document.querySelectorAll('button')]
      .filter((candidate) => labels.includes(candidate.textContent?.trim() || ''))
      .filter((candidate) => {
        const rect = candidate.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0
          && rect.left >= composerRect.left
          && rect.top >= composerRect.bottom - 90
          && rect.top <= composerRect.bottom + 40;
      })[0];
    return button?.textContent?.trim() || null;
  })()`);
  if (!currentMode) throw new Error('Founder Chat mode control was not found.');
  if (currentMode !== modeNames[requestedMode]) {
    await evaluate(`(() => {
      const composer = [...document.querySelectorAll('textarea')]
        .find((candidate) => /Enter instructions/i.test(candidate.placeholder || ''));
      if (!composer) return false;
      const composerRect = composer.getBoundingClientRect();
      const labels = ${JSON.stringify(Object.values(modeNames))};
      const button = [...document.querySelectorAll('button')]
        .filter((candidate) => labels.includes(candidate.textContent?.trim() || ''))
        .filter((candidate) => {
          const rect = candidate.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0
            && rect.left >= composerRect.left
            && rect.top >= composerRect.bottom - 90
            && rect.top <= composerRect.bottom + 40;
        })[0];
      button?.click();
      return Boolean(button);
    })()`);
    await wait(300);
    const selected = await evaluate(`(() => {
      const wanted = ${JSON.stringify(modeNames[requestedMode])};
      const candidates = [...document.querySelectorAll('*')]
        .filter((candidate) => {
          const rect = candidate.getBoundingClientRect();
          const style = getComputedStyle(candidate);
          const text = candidate.textContent?.replace(/\\s+/g, ' ').trim() || '';
          return (text === wanted || text.startsWith(wanted + ' '))
            && rect.width > 0
            && rect.height > 0
            && rect.right > 0
            && rect.bottom > 0
            && rect.left < window.innerWidth
            && rect.top < window.innerHeight
            && style.visibility !== 'hidden'
            && style.display !== 'none'
            && style.opacity !== '0'
            && style.pointerEvents !== 'none';
        })
        .map((candidate) =>
          candidate.closest('[role="option"], button, [role="menuitem"]') || candidate
        );
      const option = [...new Set(candidates)]
        .sort((left, right) => {
          const leftRect = left.getBoundingClientRect();
          const rightRect = right.getBoundingClientRect();
          return (leftRect.width * leftRect.height) - (rightRect.width * rightRect.height);
        })[0];
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
const expectedResponse = `Founder AI is online. ${nonce}`;
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
let latestResponseTail = '';
let responseCount = 0;
let routeReceiptVisible = false;
while (Date.now() - startedAt < 90_000) {
  await wait(700);
  chatText = await evaluate(`document.body.innerText.slice(-24000)`);
  responseCount = chatText.split(expectedResponse).length - 1;
  latestResponseTail = responseCount >= 2
    ? chatText.slice(chatText.lastIndexOf(expectedResponse))
    : '';
  routeReceiptVisible = /Founder route[^\r\n]+/i.test(latestResponseTail);
  if (responseCount >= 2 && routeReceiptVisible) break;
}
const latencyMs = Date.now() - startedAt;
const screenshot = await send('Page.captureScreenshot', {
  format: 'png',
  fromSurface: true,
  captureBeyondViewport: false,
});
const screenshotPath = path.join(outputDir, `installed-founder-chat-native-${evidenceId}.png`);
fs.writeFileSync(screenshotPath, Buffer.from(screenshot.data, 'base64'));

const routeLine = latestResponseTail.match(/Founder route[^\r\n]+/i)?.[0] || '';
const route = routeLine.replace(/\s+/g, ' ').trim() || null;
const managedModel = routeLine.match(/deepseek\/(deepseek-v4-(?:pro|flash))/i)?.[1]?.toLowerCase() || null;
const personalModel = routeLine.match(
  /Founder route\s*\|\s*Personal AI\s*\|\s*[^|\r\n]+\|\s*([^|\r\n]+)/i,
)?.[1]?.trim().toLowerCase() || null;
const localModel = routeLine.match(
  /Founder route\s*\|\s*(?:Local AI|Ollama)\s*\|\s*(?:[^|\r\n]+\|\s*)?([^|\r\n]+)/i,
)?.[1]?.trim().toLowerCase() || null;
const routeKind = managedModel
  ? 'managed'
  : /\|\s*Personal AI\s*\|/i.test(routeLine)
    ? 'personal'
    : /\|\s*(?:Local AI|Ollama)\s*\|/i.test(routeLine)
      ? 'local'
      : null;
const resolvedModel = managedModel || personalModel || localModel;
const routeKindMatches = expectedRouteKind === 'any' || routeKind === expectedRouteKind;
const outsideManagedQuota = /outside managed quota/i.test(routeLine);
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
  routeKind,
  resolvedModel,
  checks: {
    submitted,
    responseVisible: responseCount >= 2,
    founderRoute: routeReceiptVisible,
    expectedRouteKind: routeKindMatches,
    deepSeekV4: expectedRouteKind !== 'managed' || Boolean(managedModel),
    outsideManagedQuota: !['personal', 'local'].includes(expectedRouteKind) || outsideManagedQuota,
    expectedModel: !expectedModel || resolvedModel === expectedModel,
    errorVisible: /Founder OS gateway returned|Authentication Fails|Bad gateway|invalid_request_error|Founder could not (?:send|reach)|Founder AI is temporarily unavailable|Founder session needs to be renewed/i.test(latestResponseTail),
  },
  chatTail: latestResponseTail || chatText,
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
  || !evidence.checks.expectedRouteKind
  || !evidence.checks.deepSeekV4
  || !evidence.checks.outsideManagedQuota
  || !evidence.checks.expectedModel
  || evidence.checks.errorVisible
  || criticalConsoleErrors.length > 0
  || pageErrors.length > 0
) process.exitCode = 1;
