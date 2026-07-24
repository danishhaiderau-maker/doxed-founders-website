import fs from 'node:fs';
import path from 'node:path';

const endpoint = process.env.FOUNDER_IDE_CDP || 'http://127.0.0.1:9453';
const outputDir = path.resolve(
  process.env.FOUNDER_IDE_QA_DIR || 'artifacts/installed-founder-navigation-qa',
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
    const { resolve, reject, timer } = pending.get(message.id);
    clearTimeout(timer);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message));
    else resolve(message.result);
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
    return await action({ send, evaluate });
  } finally {
    socket.close();
  }
}

async function readTargetText(target) {
  return withTarget(target, ({ evaluate }) => evaluate(`(() => {
    const root = document.querySelector('iframe')?.contentDocument || document;
    return root.body?.innerText || '';
  })()`));
}

async function clickFounderNavigation(label) {
  const candidates = (await targets()).filter(
    (target) => target.webSocketDebuggerUrl && (target.type === 'iframe' || target.type === 'page'),
  );
  const availableActions = [];
  for (const candidate of candidates) {
    const result = await withTarget(candidate, ({ evaluate }) => evaluate(`(() => {
      const root = document.querySelector('iframe')?.contentDocument || document;
      const visible = (element) => {
        const style = (root.defaultView || window).getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden'
          && rect.width > 0 && rect.height > 0;
      };
      const label = ${JSON.stringify(label)};
      const directAction = label === 'Remote' ? 'openRemote'
        : label === 'Connect' ? 'openConnections'
          : '';
      const actions = [...root.querySelectorAll('button, a, [role="button"], [data-action]')]
        .filter(visible);
      const element = actions
        .find((candidate) => candidate.getAttribute('data-action') === directAction)
        || actions.find((candidate) => {
          const text = (candidate.textContent || '').trim().replace(/\\s+/g, ' ');
          return text === label || text.startsWith(label) || text.startsWith(\`R \${label}\`)
            || text.startsWith(\`C \${label}\`);
        });
      element?.click();
      return {
        clicked: Boolean(element),
        actions: actions
          .map((candidate) => (candidate.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 120))
          .filter(Boolean),
      };
    })()`)).catch(() => ({ clicked: false, actions: [] }));
    availableActions.push(...result.actions);
    if (result.clicked) return { clicked: true, availableActions };
  }
  return { clicked: false, availableActions };
}

async function collectVisibleText() {
  const chunks = [];
  for (const candidate of await targets()) {
    if (!candidate.webSocketDebuggerUrl || !['page', 'iframe'].includes(candidate.type)) continue;
    chunks.push(await readTargetText(candidate).catch(() => ''));
  }
  return chunks.join('\n');
}

async function captureWorkbench(name) {
  const workbench = (await targets()).find(
    (target) => target.type === 'page' && target.url?.includes('/workbench/workbench.html'),
  );
  if (!workbench) throw new Error('Founder IDE workbench target not found');
  await withTarget(workbench, async ({ send }) => {
    const screenshot = await send('Page.captureScreenshot', {
      format: 'png',
      fromSurface: true,
      captureBeyondViewport: false,
    });
    fs.writeFileSync(path.join(outputDir, `${name}.png`), Buffer.from(screenshot.data, 'base64'));
  });
}

const evidence = {
  endpoint,
  remote: { clicked: false, rendered: false },
  connect: { clicked: false, rendered: false },
};

const remoteClick = await clickFounderNavigation('Remote');
evidence.remote.clicked = remoteClick.clicked;
evidence.remote.availableActions = remoteClick.availableActions;
await wait(3_000);
const remoteText = await collectVisibleText();
evidence.remote.rendered = /Remote sessions|Open web remote control|This computer is available/i.test(remoteText);
await captureWorkbench('installed-founder-remote');

const connectClick = await clickFounderNavigation('Connect');
evidence.connect.clicked = connectClick.clicked;
evidence.connect.availableActions = connectClick.availableActions;
await wait(3_000);
const connectText = await collectVisibleText();
evidence.connect.rendered = /Founder Connections|GitHub|Vercel|Railway|Neon|Services and infrastructure/i.test(
  connectText,
);
await captureWorkbench('installed-founder-connect');

fs.writeFileSync(
  path.join(outputDir, 'installed-founder-navigation-evidence.json'),
  `${JSON.stringify(evidence, null, 2)}\n`,
);
process.stdout.write(`${JSON.stringify(evidence, null, 2)}\n`);

if (
  !evidence.remote.clicked
  || !evidence.remote.rendered
  || !evidence.connect.clicked
  || !evidence.connect.rendered
) {
  process.exitCode = 1;
}
