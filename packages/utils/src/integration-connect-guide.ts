export type ConnectGuideStep = {
  title: string;
  body: string;
  link?: { label: string; href: string };
};

export type IntegrationConnectGuide = {
  summary: string;
  whatItDoes: string;
  whatItDoesNot: string;
  steps: ConnectGuideStep[];
  note?: string;
};

export const INTEGRATION_CONNECT_GUIDES: Record<string, IntegrationConnectGuide> = {
  github: {
    summary: 'Link your public repo so Founder OS can sync commits into build updates.',
    whatItDoes:
      'Stores owner/repo and reads public commit history via GitHub REST API. No GitHub OAuth required for basic sync.',
    whatItDoesNot:
      'Does not access private repos, create issues, or dispatch Cursor agents. For issues/PRs add a GitHub PAT in Builder settings.',
    steps: [
      {
        title: '1. Find your repo name',
        body: 'On GitHub open your project → the URL is github.com/OWNER/REPO. Use exactly OWNER/REPO (e.g. danishhaiderau-maker/doxed-founders-website).',
      },
      {
        title: '2. Paste in Stack hub',
        body: 'Founder OS → Founder Copilot tab → Connected stack → type owner/repo → Connect GitHub.',
      },
      {
        title: '3. Sync commits',
        body: 'Click Sync commits. Founder OS drafts a suggested update you can publish to build feed + X.',
      },
      {
        title: '4. Optional — GitHub PAT',
        body: 'Settings → Builder → GitHub personal access token (repo scope) for creating issues from your queue.',
        link: { label: 'Open Builder settings', href: '/settings/builder' },
      },
    ],
  },
  cursor: {
    summary: 'Enable Founder Copilot workflows that use Cursor Cloud Agents on your repo.',
    whatItDoes:
      'Stack toggle marks “Founder Copilot enabled”. Remote agent dispatch requires your Cursor API key in Builder settings — separate from Neon/Vercel.',
    whatItDoesNot:
      'Does not connect Neon, Vercel, or database. Cursor only runs coding agents on GitHub — not your Postgres.',
    steps: [
      {
        title: '1. Connect GitHub repo first',
        body: 'Cursor agents need a linked GitHub repository (owner/repo in Stack hub).',
      },
      {
        title: '2. Click + Founder Copilot (Cursor)',
        body: 'In Connected stack this toggles Founder Copilot on your account.',
      },
      {
        title: '3. Add Cursor API key',
        body: 'Settings → Builder → Cursor Cloud Agents → paste API key from cursor.com/dashboard → Connect.',
        link: { label: 'Open Builder settings', href: '/settings/builder' },
      },
      {
        title: '4. Set default provider (optional)',
        body: 'Set Default provider to CURSOR to dispatch agents from Quick Build. Use an LLM key (DeepSeek, OpenAI…) for AI-written specs.',
      },
    ],
    note: 'Cursor “connected” in Stack hub ≠ API key connected. Both are needed for agents to run on your repo.',
  },
  vercel: {
    summary: 'Deploy webhooks → auto-suggested build updates when you ship.',
    whatItDoes: 'Verifies your Vercel token and gives you a webhook URL to paste in Vercel project settings.',
    whatItDoesNot: 'Does not host your site or replace Vercel billing. Free Hobby tier works — no upgrade required for webhooks.',
    steps: [
      {
        title: '1. Create Vercel token',
        body: 'vercel.com → Account Settings → Tokens → Create (Full Account or scoped to project).',
        link: { label: 'Vercel tokens', href: 'https://vercel.com/account/tokens' },
      },
      {
        title: '2. Connect in Founder OS',
        body: '+ Vercel → paste token → Connect. Copy the webhook URL shown in the success message.',
      },
      {
        title: '3. Add deploy hook',
        body: 'Vercel project → Settings → Git → Deploy Hooks (or Webhooks) → paste Founder OS webhook URL.',
      },
    ],
    note: 'Hobby (free) plan supports deploy hooks. Pro only needed for team features or higher limits — not for this integration.',
  },
  railway: {
    summary: 'Railway deploy events → Founder OS suggested updates.',
    whatItDoes: 'Verifies Railway account token and registers a deploy webhook.',
    whatItDoesNot: 'Does not run your API — Railway still hosts it. Founder OS only receives deploy notifications.',
    steps: [
      {
        title: '1. Railway token',
        body: 'railway.app → Account Settings → Tokens → Create token.',
        link: { label: 'Railway account', href: 'https://railway.app/account/tokens' },
      },
      { title: '2. Connect', body: '+ Railway in Stack hub → paste token → optional project name → Connect.' },
      {
        title: '3. Webhook',
        body: 'Railway service → Settings → Webhooks → add the URL from the connect success message.',
      },
    ],
  },
  neon: {
    summary: 'Show Postgres project in your stack — proves infra transparency.',
    whatItDoes: 'Verifies Neon API key and lists project name on your connected stack.',
    whatItDoesNot: 'Does not give Cursor or Founder OS access to your database. Neon is independent of Cursor.',
    steps: [
      {
        title: '1. Neon API key',
        body: 'console.neon.tech → Account → API keys → Create.',
        link: { label: 'Neon console', href: 'https://console.neon.tech/app/settings/api-keys' },
      },
      { title: '2. Connect', body: '+ Neon → paste neon_… key → optional project name → Connect.' },
    ],
    note: 'Cursor was never connected via Neon. If Cursor shows connected, that came from the Stack toggle + Cursor API key in Builder settings.',
  },
  digitalocean: {
    summary: 'Link DigitalOcean account for stack visibility.',
    whatItDoes: 'Verifies DO personal access token.',
    whatItDoesNot: 'Does not provision droplets from Founder OS.',
    steps: [
      {
        title: '1. DO token',
        body: 'cloud.digitalocean.com → API → Tokens → Generate (read access is enough for verify).',
        link: { label: 'DO API tokens', href: 'https://cloud.digitalocean.com/account/api/tokens' },
      },
      { title: '2. Connect', body: '+ DigitalOcean → paste dop_v1_… token → Connect.' },
    ],
  },
  supabase: {
    summary: 'Link Supabase project for backend transparency.',
    whatItDoes: 'Verifies Supabase access token against your projects.',
    whatItDoesNot: 'Does not migrate or query your database from Founder OS.',
    steps: [
      {
        title: '1. Supabase token',
        body: 'supabase.com/dashboard → Account → Access Tokens → Generate.',
        link: { label: 'Supabase tokens', href: 'https://supabase.com/dashboard/account/tokens' },
      },
      { title: '2. Connect', body: '+ Supabase → paste sbp_… token → optional project ref → Connect.' },
    ],
  },
  x: {
    summary: 'Publish build updates to X in one click.',
    whatItDoes: 'Uses Sign in with X (OAuth) — no manual API key in Stack hub.',
    whatItDoesNot: 'Cannot post until you sign in with X at login/register.',
    steps: [
      { title: '1. Sign in with X', body: 'Login or Register → Continue with X.' },
      { title: '2. Publish', body: 'After syncing GitHub, use Publish everywhere on suggested updates.' },
    ],
  },
};

export function getIntegrationConnectGuide(providerKey: string): IntegrationConnectGuide | undefined {
  return INTEGRATION_CONNECT_GUIDES[providerKey];
}
