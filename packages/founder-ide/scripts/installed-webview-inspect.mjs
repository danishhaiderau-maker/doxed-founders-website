const endpoint = process.env.FOUNDER_IDE_CDP || 'http://127.0.0.1:9452';
const targets = await fetch(`${endpoint}/json/list`).then((response) => response.json());

async function inspect(target) {
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });
  const result = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Runtime.evaluate timed out')), 10_000);
    socket.addEventListener('message', ({ data }) => {
      const message = JSON.parse(data.toString());
      if (message.id !== 1) return;
      clearTimeout(timer);
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result?.result?.value);
    });
    socket.send(JSON.stringify({
      id: 1,
      method: 'Runtime.evaluate',
      params: {
        expression: `(() => {
          const root = document.querySelector('iframe')?.contentDocument || document;
          return ({
          html: document.body?.innerHTML?.slice(0, 500) || '',
          text: root.body?.innerText?.replace(/\\n{3,}/g, '\\n\\n').slice(0, 12000) || '',
          actions: [...root.querySelectorAll('[data-action], button, a')].map((element) => ({
            action: element.getAttribute('data-action') || '',
            text: element.textContent?.trim().replace(/\\s+/g, ' ').slice(0, 160) || '',
          })).filter((entry) => entry.action || entry.text).slice(0, 100),
        }); })()`,
        returnByValue: true,
      },
    }));
  });
  socket.close();
  return result;
}

const rows = [];
for (const target of targets.filter((candidate) => candidate.type === 'iframe')) {
  try {
    rows.push({ id: target.id, ...(await inspect(target)) });
  } catch (error) {
    rows.push({ id: target.id, error: String(error) });
  }
}
process.stdout.write(`${JSON.stringify(rows, null, 2)}\n`);
