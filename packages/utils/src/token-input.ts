const EVM_CONTRACT = /^0x[a-fA-F0-9]{40}$/;
const SOL_CONTRACT = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

export type ParsedTokenInput =
  | { kind: 'url'; url: string }
  | { kind: 'contract'; address: string; chainHint?: string };

export function parseTokenInput(raw: string): ParsedTokenInput | null {
  const input = raw.trim();
  if (!input) return null;

  if (/dexscreener\.com/i.test(input) || input.startsWith('http')) {
    return { kind: 'url', url: input };
  }

  const contractMatch = input.match(
    /(?:^|\s)(?:CA|Contract|Address)?[:\s]*([0-9A-Za-z]{32,66})(?:\s|$)/i,
  );
  const candidate = (contractMatch?.[1] ?? input).trim();

  if (EVM_CONTRACT.test(candidate)) {
    return { kind: 'contract', address: candidate };
  }
  if (SOL_CONTRACT.test(candidate) && !candidate.startsWith('0x')) {
    return { kind: 'contract', address: candidate, chainHint: 'SOLANA' };
  }

  return null;
}

export const CONTRACT_CHAIN_FALLBACK = [
  'SOLANA',
  'BASE',
  'ETHEREUM',
  'BNB_CHAIN',
  'ARBITRUM',
  'POLYGON',
] as const;
