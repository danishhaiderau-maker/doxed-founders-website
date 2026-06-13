/** Copy-paste + submit URLs for Conservative BTC Agent directory listings. */

export const CONSERVATIVE_BTC_DIRECTORY_PROFILE = {
  name: 'Conservative BTC Agent',
  tagline: 'Exchange-neutral BTC perp signal API — pay success fee on profit only, not on losses.',
  description:
    'Conservative BTC Agent publishes live BTC perpetual signal cycles from a transparent research pipeline. Subscribers execute on their own exchange using exchange-neutral percentage offsets. Success fee: 10% of profit on close only; $0 on loss. REST Signal Cycle API with mandatory stop-loss-at-fill rule.',
  hubUrl: 'https://doxxedcrypto.digital/agent-hub/conservative-btc',
  siteUrl: 'https://doxxedcrypto.digital',
  docsUrl: 'https://doxxedcrypto.digital/docs/signal-api',
  agentCardUrl: 'https://doxxedcrypto.digital/.well-known/agent-card.json',
  agentJsonUrl: 'https://doxxedcrypto.digital/.well-known/agent.json',
  iconUrl: 'https://doxxedcrypto.digital/icons/conservative-btc-agent.png',
  thumbnailUrl: 'https://doxxedcrypto.digital/icons/conservative-btc-agent-thumbnail.png',
  mandateUrl:
    'https://doxed-founders-website-production.up.railway.app/api/trading-agents/conservative-btc/signals/mandate',
  company: 'Doxxed Crypto Founder',
  authorEmail: 'danish.haider.au@gmail.com',
  categories: ['Research', 'Data Analysis', 'Workflow Automation', 'Finance'],
  tags: [
    'BTC',
    'bitcoin',
    'trading',
    'signals',
    'API',
    'exchange-agnostic',
    'workflow',
    'data analysis',
    'AI agent',
  ],
  pricingModel: 'Freemium',
  features: [
    'Exchange-neutral ENSE signal intents',
    'Mandatory stop-loss-at-fill for subscribers',
    'Success-fee billing only on profitable closes',
    'Live public showcase dashboard',
    'AgentCard + subscriber docs',
  ],
} as const;

export type AgentDirectoryTarget = {
  id: string;
  label: string;
  submitUrl: string;
  free: boolean;
  automated: boolean;
  categoryHints: string[];
  notes?: string;
};

export const AGENT_DIRECTORY_WEB_TARGETS: AgentDirectoryTarget[] = [
  {
    id: 'AGENTSCAN',
    label: 'AI Agents Directory',
    submitUrl: 'https://aiagentsdirectory.com/submit-agent',
    free: true,
    automated: false,
    categoryHints: ['Research', 'Data Analysis', 'Workflow'],
    notes: 'Free tier requires AAD badge on site (added to Agent Hub footer).',
  },
  {
    id: 'PIKAGENT',
    label: 'Pikagent',
    submitUrl: 'https://www.pikagent.com/submit',
    free: true,
    automated: false,
    categoryHints: ['Data Analysis', 'Workflow Automation', 'Other'],
  },
  {
    id: 'FUSHU',
    label: 'Fushu',
    submitUrl: 'https://fushu.dev/register',
    free: true,
    automated: true,
    categoryHints: ['agent', 'REST'],
    notes: 'Also ships fushu.json in repo root; API submit when available.',
  },
  {
    id: 'AGENTS_ONE',
    label: 'Agents.one',
    submitUrl: 'https://agents.one/submit-agent/',
    free: true,
    automated: false,
    categoryHints: ['Finance', 'Automation'],
  },
  {
    id: 'LISTMYAGENT',
    label: 'ListMyAgent',
    submitUrl: 'https://listmyagent.com/add-ai-agent-listing/',
    free: true,
    automated: false,
    categoryHints: ['Research', 'Finance', 'Workflow'],
  },
  {
    id: 'AIAGENTS_BUZZ',
    label: 'AI Agents Buzz',
    submitUrl: 'https://aiagentsbuzz.com/submit/',
    free: true,
    automated: false,
    categoryHints: ['Finance', 'Research', 'Workflow', 'Data'],
  },
  {
    id: 'SKILLS_SH',
    label: 'OpenServ',
    submitUrl: 'https://www.openserv.ai/',
    free: true,
    automated: false,
    categoryHints: ['API marketplace apply'],
    notes: 'Application review — attach docs + mandate URL.',
  },
];
