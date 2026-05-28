import { execSync } from 'node:child_process';

function ghToken() {
  const out = execSync('git credential fill', {
    input: 'protocol=https\nhost=github.com\n\n',
    encoding: 'utf8',
  });
  const match = out.match(/^password=(.+)$/m);
  return match?.[1]?.trim();
}

const token = ghToken();
if (!token) {
  console.error('No GitHub token');
  process.exit(1);
}

const res = await fetch('https://api.github.com/repos/danishhaiderau-maker/doxed-founders-website', {
  method: 'PATCH',
  headers: {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({ private: true, visibility: 'private' }),
});

const body = await res.json();
if (!res.ok) {
  console.error('Failed:', res.status, body.message ?? body);
  process.exit(1);
}

console.log(`Repo is now ${body.private ? 'PRIVATE' : 'public'} (${body.full_name})`);
