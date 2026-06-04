/** Lightweight commit → initiative/outcome signals (no LLM). */

export type CommitSignal = { sha: string; message: string; date?: string };

/** Repeated Founder OS memory sync — not product initiative signal. */
export function isFounderOsSyncNoiseCommit(message: string): boolean {
  const msg = message.trim();
  if (!msg) return true;
  return (
    /^chore\(founder-os\):\s*sync\b/i.test(msg) ||
    /^sync (project context|roadmap|tasks)\b/i.test(msg) ||
    /^chore:\s*sync (tasks|roadmap|context)/i.test(msg)
  );
}

export function filterCommitsForIntelligence(commits: CommitSignal[]): CommitSignal[] {
  return commits.filter((c) => !isFounderOsSyncNoiseCommit(c.message));
}

export type InitiativeTheme = {
  key: string;
  label: string;
  commitCount: number;
  samples: string[];
};

const THEME_RULES: { key: string; label: string; pattern: RegExp }[] = [
  { key: 'founder_os', label: 'Founder OS / Mission Control', pattern: /founder\s*os|mission\s*control|copilot|command\s*center/i },
  { key: 'feed', label: 'Feed & Money stream', pattern: /feed|money\s*feed|unified\s*feed|terminal/i },
  { key: 'discover', label: 'Discover & listings', pattern: /discover|listing|scout|bubble/i },
  { key: 'vault', label: 'Founder Vault & privacy', pattern: /vault|founder\s*node|encrypt|phala|cvm/i },
  { key: 'builder', label: 'Builder Agent & Cursor', pattern: /builder|cursor|openhands|agent\s*run/i },
  { key: 'predictions', label: 'Prediction markets', pattern: /predict|market|oracle|stake/i },
  { key: 'rewards', label: 'Builder rewards & DDollar', pattern: /reward|ddollar|airdrop|reputation/i },
  { key: 'mobile', label: 'Mobile & Android', pattern: /android|capacitor|mobile/i },
  { key: 'deploy', label: 'Deploy & infra', pattern: /deploy|vercel|railway|neon|production|sync:all/i },
  { key: 'security', label: 'Security & auth', pattern: /security|auth|webauthn|jwt|2fa/i },
];

export function groupCommitsByInitiative(commits: CommitSignal[]): InitiativeTheme[] {
  const buckets = new Map<string, InitiativeTheme>();

  for (const c of commits) {
    const msg = c.message.trim();
    if (!msg || /^merge\b/i.test(msg) || isFounderOsSyncNoiseCommit(msg)) continue;

    let matched = false;
    for (const rule of THEME_RULES) {
      if (rule.pattern.test(msg)) {
        const existing = buckets.get(rule.key) ?? {
          key: rule.key,
          label: rule.label,
          commitCount: 0,
          samples: [],
        };
        existing.commitCount += 1;
        if (existing.samples.length < 3) existing.samples.push(msg.slice(0, 120));
        buckets.set(rule.key, existing);
        matched = true;
        break;
      }
    }
    if (!matched) {
      const key = 'product';
      const existing = buckets.get(key) ?? {
        key,
        label: 'Product & platform',
        commitCount: 0,
        samples: [],
      };
      existing.commitCount += 1;
      if (existing.samples.length < 2) existing.samples.push(msg.slice(0, 120));
      buckets.set(key, existing);
    }
  }

  return [...buckets.values()].sort((a, b) => b.commitCount - a.commitCount);
}

export function summarizeShippedOutcomes(commits: CommitSignal[], max = 6): string[] {
  const outcomes: string[] = [];
  const seen = new Set<string>();

  for (const c of commits) {
    const msg = c.message.trim();
    if (!msg || /^merge\b/i.test(msg) || /^fix typo/i.test(msg) || isFounderOsSyncNoiseCommit(msg)) continue;
    const normalized = msg.toLowerCase();
    if (seen.has(normalized)) continue;
    seen.add(normalized);

    if (/^(feat|add|implement|ship|launch|build|introduce)/i.test(msg)) {
      outcomes.push(msg.replace(/^(feat|add|implement)[:(\s]+/i, '').slice(0, 140));
    } else if (/^(fix|patch|resolve)/i.test(msg) && outcomes.length < max) {
      outcomes.push(`Fix: ${msg.replace(/^fix[:(\s]+/i, '').slice(0, 120)}`);
    }
    if (outcomes.length >= max) break;
  }

  return outcomes;
}
