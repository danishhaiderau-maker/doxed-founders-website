'use client';

export type Eip1193Provider = {
  request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
  isMetaMask?: boolean;
  isCoinbaseWallet?: boolean;
  isPhantom?: boolean;
  providers?: Eip1193Provider[];
};

export type EvmWalletOption = {
  id: string;
  name: string;
  icon?: string;
  provider: Eip1193Provider;
};

export type SolanaWalletOption = {
  id: string;
  name: string;
  provider: SolanaProvider;
};

export type SolanaProvider = {
  connect(): Promise<{ publicKey: { toString(): string } }>;
  disconnect?(): Promise<void>;
  signMessage(message: Uint8Array, display?: string): Promise<{ signature: Uint8Array }>;
  publicKey?: { toString(): string };
  isConnected?: boolean;
};

declare global {
  interface Window {
    phantom?: { solana?: SolanaProvider & { isPhantom?: boolean } };
    solflare?: SolanaProvider;
    backpack?: SolanaProvider;
    coinbaseWalletExtension?: Eip1193Provider;
    ethereum?: Eip1193Provider;
  }
}

type Eip6963Detail = {
  info: { uuid: string; name: string; icon: string; rdns: string };
  provider: Eip1193Provider;
};

function isPhantomEvm(provider: Eip1193Provider, info?: Eip6963Detail['info']): boolean {
  if (provider.isPhantom) return true;
  const rdns = info?.rdns?.toLowerCase() ?? '';
  const name = info?.name?.toLowerCase() ?? '';
  return rdns.includes('phantom') || name.includes('phantom');
}

function pushUniqueEvm(list: EvmWalletOption[], option: EvmWalletOption) {
  if (list.some((w) => w.id === option.id)) return;
  list.push(option);
}

/** Discover EVM wallets via EIP-6963 — avoids Phantom hijacking window.ethereum. */
export function discoverEvmWallets(timeoutMs = 400): Promise<EvmWalletOption[]> {
  return new Promise((resolve) => {
    const found: EvmWalletOption[] = [];

    function onAnnounce(event: Event) {
      const detail = (event as CustomEvent<Eip6963Detail>).detail;
      if (!detail?.provider || isPhantomEvm(detail.provider, detail.info)) return;
      pushUniqueEvm(found, {
        id: detail.info.uuid,
        name: detail.info.name,
        icon: detail.info.icon,
        provider: detail.provider,
      });
    }

    window.addEventListener('eip6963:announceProvider', onAnnounce);
    window.dispatchEvent(new Event('eip6963:requestProvider'));

    setTimeout(() => {
      window.removeEventListener('eip6963:announceProvider', onAnnounce);

      const eth = window.ethereum;
      if (eth?.providers?.length) {
        for (const p of eth.providers) {
          if (isPhantomEvm(p)) continue;
          if (p.isMetaMask) {
            pushUniqueEvm(found, { id: 'metamask', name: 'MetaMask', provider: p });
          } else if (p.isCoinbaseWallet) {
            pushUniqueEvm(found, { id: 'coinbase', name: 'Coinbase Wallet', provider: p });
          }
        }
      }

      if (window.coinbaseWalletExtension && !found.some((w) => w.id === 'coinbase')) {
        pushUniqueEvm(found, {
          id: 'coinbase',
          name: 'Coinbase Wallet',
          provider: window.coinbaseWalletExtension,
        });
      }

      if (eth && !isPhantomEvm(eth) && found.length === 0) {
        if (eth.isMetaMask) {
          pushUniqueEvm(found, { id: 'metamask', name: 'MetaMask', provider: eth });
        } else if (eth.isCoinbaseWallet) {
          pushUniqueEvm(found, { id: 'coinbase', name: 'Coinbase Wallet', provider: eth });
        } else {
          pushUniqueEvm(found, { id: 'injected', name: 'Browser wallet', provider: eth });
        }
      }

      resolve(found);
    }, timeoutMs);
  });
}

export function listSolanaWallets(): SolanaWalletOption[] {
  const wallets: SolanaWalletOption[] = [];
  if (window.phantom?.solana) {
    wallets.push({ id: 'phantom', name: 'Phantom', provider: window.phantom.solana });
  }
  if (window.backpack) {
    wallets.push({ id: 'backpack', name: 'Backpack', provider: window.backpack });
  }
  if (window.solflare) {
    wallets.push({ id: 'solflare', name: 'Solflare', provider: window.solflare });
  }
  return wallets;
}

export async function disconnectEvmWallet(provider: Eip1193Provider) {
  try {
    await provider.request({
      method: 'wallet_revokePermissions',
      params: [{ eth_accounts: {} }],
    });
  } catch {
    // not all wallets support revoke
  }
}

export async function disconnectSolanaWallet(provider: SolanaProvider) {
  try {
    await provider.disconnect?.();
  } catch {
    // ignore
  }
}

/** Allowed EVM chains for on-platform payments (not Ethereum mainnet — gas). */
export const EVM_PAYMENT_CHAINS = [
  { chainId: '0x2105', name: 'Base', slug: 'BASE' },
  { chainId: '0x38', name: 'BNB Chain', slug: 'BNB_CHAIN' },
] as const;

export const ETHEREUM_MAINNET_CHAIN_ID = '0x1';
