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
          const visible = (element) => {
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          return {
            textTail: document.body.innerText.slice(-8000),
            controls: [...document.querySelectorAll('button, textarea, input, [contenteditable="true"], [role="button"]')]
              .filter(visible)
              .map((element) => ({
                tag: element.tagName.toLowerCase(),
                aria: element.getAttribute('aria-label') || '',
                title: element.getAttribute('title') || '',
                placeholder: element.getAttribute('placeholder') || '',
                text: element.textContent?.trim().replace(/\\s+/g, ' ').slice(0, 140) || '',
                className: typeof element.className === 'string' ? element.className.slice(0, 180) : '',
                rect: element.getBoundingClientRect().toJSON(),
              }))
              .slice(0, 200),
          };
        })()`,
        returnByValue: true,
      },
    }));
  });
  socket.close();
  return result;
}

const rows = [];
for (const target of targets.filter((candidate) => candidate.type === 'page')) {
  rows.push({ id: target.id, title: target.title, ...(await inspect(target)) });
}
process.stdout.write(`${JSON.stringify(rows, null, 2)}\n`);
