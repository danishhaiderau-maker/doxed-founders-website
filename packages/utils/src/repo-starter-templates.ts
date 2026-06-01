export type RepoStarterFile = { path: string; content: string };

export type RepoStarterTemplate = {
  key: string;
  label: string;
  description: string;
  tags: string[];
  defaultRepoName: string;
  files: RepoStarterFile[];
};

export const REPO_STARTER_TEMPLATES: RepoStarterTemplate[] = [
  {
    key: 'next-web3-dapp',
    label: 'Next.js Web3 dApp',
    description: 'Wallet-ready landing + token page scaffold for Solana/EVM projects.',
    tags: ['nextjs', 'web3', 'token'],
    defaultRepoName: 'my-dapp',
    files: [
      {
        path: 'README.md',
        content: `# My dApp

Starter scaffold from Doxxed Crypto Founder OS.

## Next steps
- Connect wallet provider (wagmi / Solana wallet adapter)
- Replace placeholder token metadata
- Ship commits — Founder OS auto-syncs build updates
`,
      },
      {
        path: 'package.json',
        content: JSON.stringify(
          {
            name: 'my-dapp',
            private: true,
            scripts: { dev: 'next dev', build: 'next build', start: 'next start' },
            dependencies: { next: '^15.0.0', react: '^19.0.0', 'react-dom': '^19.0.0' },
          },
          null,
          2,
        ),
      },
      {
        path: 'app/page.tsx',
        content: `export default function Home() {
  return (
    <main style={{ padding: '2rem', fontFamily: 'system-ui' }}>
      <h1>My Web3 Project</h1>
      <p>Built with Founder OS — connect wallet and token metadata next.</p>
    </main>
  );
}
`,
      },
      {
        path: '.github/founder-os/project-context.md',
        content: `# Project context

## Vision
Ship a credible on-chain product with transparent build logs.

## Stack
Next.js · wallet connect · token landing
`,
      },
    ],
  },
  {
    key: 'token-landing',
    label: 'Token landing page',
    description: 'Marketing site + docs links for pre-launch tokens.',
    tags: ['landing', 'marketing'],
    defaultRepoName: 'token-landing',
    files: [
      {
        path: 'README.md',
        content: `# Token landing

Public landing for your token — sync build updates via Founder OS.
`,
      },
      {
        path: 'index.html',
        content: `<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>My Token</title></head>
<body>
  <h1>My Token</h1>
  <p>Demand validation · build in public · launch with trust.</p>
</body>
</html>
`,
      },
      {
        path: '.github/founder-os/roadmap.md',
        content: `# Roadmap

- [ ] Landing page live
- [ ] Community channels linked
- [ ] Raise Room validation
- [ ] Token launch
`,
      },
    ],
  },
  {
    key: 'api-bot',
    label: 'API + bot starter',
    description: 'Node API skeleton for alerts, bots, and backend services.',
    tags: ['api', 'bot', 'node'],
    defaultRepoName: 'founder-api-bot',
    files: [
      {
        path: 'README.md',
        content: `# Founder API bot

Webhook-friendly API starter — deploy to Railway and connect Founder OS deploy webhooks.
`,
      },
      {
        path: 'package.json',
        content: JSON.stringify(
          {
            name: 'founder-api-bot',
            private: true,
            type: 'module',
            scripts: { dev: 'node --watch src/index.js', start: 'node src/index.js' },
          },
          null,
          2,
        ),
      },
      {
        path: 'src/index.js',
        content: `import http from 'node:http';

const server = http.createServer((req, res) => {
  if (req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, service: 'founder-api-bot' }));
    return;
  }
  res.writeHead(404);
  res.end('Not found');
});

server.listen(process.env.PORT ?? 4000, () => {
  console.log('API bot listening');
});
`,
      },
    ],
  },
];

export function getRepoStarterTemplate(key: string): RepoStarterTemplate | undefined {
  return REPO_STARTER_TEMPLATES.find((t) => t.key === key);
}
