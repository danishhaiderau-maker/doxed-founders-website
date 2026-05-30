import { SOLANA_USDC_DECIMALS, SOLANA_USDC_MINT } from '@dcf/utils';

type TokenBalance = {
  accountIndex: number;
  mint: string;
  owner?: string;
  uiTokenAmount?: { amount: string; decimals: number; uiAmount: number | null };
};

type ParsedTx = {
  meta?: {
    err: unknown;
    preBalances?: number[];
    postBalances?: number[];
    preTokenBalances?: TokenBalance[];
    postTokenBalances?: TokenBalance[];
  };
  transaction?: {
    message?: {
      accountKeys?: { pubkey: string }[] | string[];
    };
  };
};

export type SolanaPaymentVerification = {
  ok: boolean;
  payerAddress?: string;
  amountUsd?: number;
  reason?: string;
};

function accountKeyAt(tx: ParsedTx, index: number): string | null {
  const keys = tx.transaction?.message?.accountKeys;
  if (!keys?.length) return null;
  const key = keys[index];
  return typeof key === 'string' ? key : key.pubkey;
}

function tokenDelta(
  pre: TokenBalance[],
  post: TokenBalance[],
  mint: string,
  owner: string,
): number {
  const findAmount = (rows: TokenBalance[]) => {
    const row = rows.find((r) => r.mint === mint && r.owner === owner);
    return row?.uiTokenAmount?.uiAmount ?? 0;
  };
  return findAmount(post) - findAmount(pre);
}

function nativeSolDelta(
  tx: ParsedTx,
  treasuryAddress: string,
): { deltaSol: number; payerAddress: string | null } {
  const pre = tx.meta?.preBalances ?? [];
  const post = tx.meta?.postBalances ?? [];
  let treasuryIndex = -1;
  for (let i = 0; i < Math.max(pre.length, post.length); i += 1) {
    const key = accountKeyAt(tx, i);
    if (key === treasuryAddress) {
      treasuryIndex = i;
      break;
    }
  }
  if (treasuryIndex < 0) {
    return { deltaSol: 0, payerAddress: null };
  }
  const deltaLamports = (post[treasuryIndex] ?? 0) - (pre[treasuryIndex] ?? 0);
  let payerAddress: string | null = null;
  let maxOut = 0;
  for (let i = 0; i < Math.max(pre.length, post.length); i += 1) {
    if (i === treasuryIndex) continue;
    const delta = (pre[i] ?? 0) - (post[i] ?? 0);
    if (delta > maxOut) {
      maxOut = delta;
      payerAddress = accountKeyAt(tx, i);
    }
  }
  return { deltaSol: deltaLamports / 1e9, payerAddress };
}

export async function verifySolanaTopUpPayment(input: {
  rpcUrl: string;
  txSignature: string;
  treasuryAddress: string;
  expectedPayerAddress: string;
  minAmountUsd: number;
  asset: 'USDC' | 'SOL';
}): Promise<SolanaPaymentVerification> {
  const response = await fetch(input.rpcUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: 1,
      method: 'getTransaction',
      params: [
        input.txSignature,
        { encoding: 'jsonParsed', maxSupportedTransactionVersion: 0 },
      ],
    }),
  });

  if (!response.ok) {
    return { ok: false, reason: `Solana RPC error (${response.status})` };
  }

  const payload = (await response.json()) as { result?: ParsedTx | null };
  const tx = payload.result;
  if (!tx) {
    return { ok: false, reason: 'Transaction not found yet — wait for confirmation and retry' };
  }
  if (tx.meta?.err) {
    return { ok: false, reason: 'Transaction failed on-chain' };
  }

  const preToken = tx.meta?.preTokenBalances ?? [];
  const postToken = tx.meta?.postTokenBalances ?? [];

  if (input.asset === 'USDC') {
    const received = tokenDelta(
      preToken,
      postToken,
      SOLANA_USDC_MINT,
      input.treasuryAddress,
    );
    if (received + 1e-9 < input.minAmountUsd) {
      return {
        ok: false,
        reason: `Expected at least $${input.minAmountUsd} USDC to treasury`,
      };
    }

    let payerAddress: string | null = null;
    let maxSent = 0;
    for (const row of preToken) {
      if (row.mint !== SOLANA_USDC_MINT || !row.owner) continue;
      if (row.owner === input.treasuryAddress) continue;
      const sent = -tokenDelta(preToken, postToken, SOLANA_USDC_MINT, row.owner);
      if (sent > maxSent) {
        maxSent = sent;
        payerAddress = row.owner;
      }
    }

    if (!payerAddress) {
      return { ok: false, reason: 'Could not identify USDC sender' };
    }
    if (payerAddress !== input.expectedPayerAddress) {
      return {
        ok: false,
        reason: 'Payment must come from your linked Solana wallet',
      };
    }

    return { ok: true, payerAddress, amountUsd: received };
  }

  const { deltaSol, payerAddress } = nativeSolDelta(tx, input.treasuryAddress);
  if (deltaSol + 1e-9 < input.minAmountUsd) {
    return {
      ok: false,
      reason: `Expected at least ${input.minAmountUsd} SOL to treasury`,
    };
  }
  if (!payerAddress || payerAddress !== input.expectedPayerAddress) {
    return {
      ok: false,
      reason: 'Payment must come from your linked Solana wallet',
    };
  }

  return { ok: true, payerAddress, amountUsd: deltaSol };
}

export function usdcRawAmount(amountUsd: number): bigint {
  return BigInt(Math.round(amountUsd * 10 ** SOLANA_USDC_DECIMALS));
}
