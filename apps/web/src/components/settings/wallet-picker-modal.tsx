'use client';

import { EvmWalletOption, SolanaWalletOption } from '@/lib/wallet-providers';

export function WalletPickerModal({
  title,
  wallets,
  onPick,
  onClose,
}: {
  title: string;
  wallets: (EvmWalletOption | SolanaWalletOption)[];
  onPick: (wallet: EvmWalletOption | SolanaWalletOption) => void;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="w-full max-w-sm rounded-2xl border border-zinc-700 bg-zinc-950 p-5 shadow-2xl">
        <div className="flex items-center justify-between gap-3">
          <h3 className="font-semibold text-white">{title}</h3>
          <button type="button" onClick={onClose} className="text-zinc-500 hover:text-white">
            ✕
          </button>
        </div>
        {wallets.length === 0 ? (
          <p className="mt-4 text-sm text-zinc-500">No compatible wallets detected in this browser.</p>
        ) : (
          <ul className="mt-4 space-y-2">
            {wallets.map((w) => (
              <li key={w.id}>
                <button
                  type="button"
                  onClick={() => onPick(w)}
                  className="flex w-full items-center gap-3 rounded-xl border border-zinc-800 bg-zinc-900/60 px-4 py-3 text-left text-sm text-white transition hover:border-indigo-500/40 hover:bg-zinc-900"
                >
                  {'icon' in w && w.icon ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={w.icon} alt="" className="h-8 w-8 rounded-lg" />
                  ) : (
                    <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-zinc-800 text-xs">
                      {w.name.slice(0, 2)}
                    </span>
                  )}
                  <span className="font-medium">{w.name}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
