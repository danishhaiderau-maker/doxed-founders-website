'use client';

import Link from 'next/link';
import bs58 from 'bs58';
import { useCallback, useEffect, useState } from 'react';
import { WalletPickerModal } from '@/components/settings/wallet-picker-modal';
import { listSolanaWallets, SolanaWalletOption } from '@/lib/wallet-providers';
import { fetchSecurityProfile, walletChallenge, walletVerify } from '@/lib/api';

const METADATA_URI = 'https://doxxedcrypto.digital/.well-known/agent-card.json';
const AGENT_JSON_URI = 'https://doxxedcrypto.digital/.well-known/agent.json';

type StepId = 1 | 2 | 3 | 4 | 5;

export function AgentRegistrationWizard({
  accessToken,
  linkedSolana,
  treasurySolana,
  onTreasurySaved,
  onMarkRegistered,
  busy,
}: {
  accessToken: string;
  linkedSolana: string | null;
  treasurySolana: string;
  onTreasurySaved: (solana: string, evm: string) => Promise<void>;
  onMarkRegistered: (registry: string, txSignature?: string) => Promise<void>;
  busy: boolean;
}) {
  const [step, setStep] = useState<StepId>(1);
  const [solanaPickerOpen, setSolanaPickerOpen] = useState(false);
  const [solanaWallets, setSolanaWallets] = useState<SolanaWalletOption[]>([]);
  const [walletBusy, setWalletBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [linked, setLinked] = useState<string | null>(linkedSolana);
  const [saidTx, setSaidTx] = useState('');
  const [spawnTx, setSpawnTx] = useState('');
  const [evmTreasury, setEvmTreasury] = useState('');

  const refreshProfile = useCallback(async () => {
    try {
      const p = await fetchSecurityProfile(accessToken);
      const addr = p.solanaWallet?.address ?? p.wallet?.address ?? null;
      setLinked(addr);
      return addr;
    } catch {
      return null;
    }
  }, [accessToken]);

  useEffect(() => {
    setLinked(linkedSolana);
  }, [linkedSolana]);

  useEffect(() => {
    if (linked && treasurySolana) setStep((s) => (s < 3 ? 3 : s));
    else if (linked) setStep((s) => (s < 2 ? 2 : s));
  }, [linked, treasurySolana]);

  async function connectPhantom(option: SolanaWalletOption) {
    setSolanaPickerOpen(false);
    setWalletBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const { challengeToken, message } = await walletChallenge(accessToken);
      const conn = await option.provider.connect();
      const address = conn.publicKey.toString();
      const encoded = new TextEncoder().encode(message);
      const { signature } = await option.provider.signMessage(encoded, 'utf8');
      await walletVerify(
        challengeToken,
        address,
        bs58.encode(signature),
        message,
        accessToken,
        'SOLANA',
      );
      setLinked(address);
      setMsg(`Phantom connected: ${address.slice(0, 8)}…${address.slice(-6)}`);
      setStep(2);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Phantom connect failed — approve the popup');
    } finally {
      setWalletBusy(false);
    }
  }

  async function saveTreasuryFromWizard() {
    if (!linked) {
      setErr('Connect Phantom first (Step 1)');
      return;
    }
    setErr(null);
    try {
      await onTreasurySaved(linked, evmTreasury.trim());
      setMsg('Treasury saved — fees will route to your Phantom');
      setStep(3);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed');
    }
  }

  function copy(text: string, label: string) {
    void navigator.clipboard.writeText(text);
    setMsg(`${label} copied`);
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-2">
        {([1, 2, 3, 4, 5] as StepId[]).map((n) => (
          <button
            key={n}
            type="button"
            onClick={() => setStep(n)}
            className={`rounded-full px-3 py-1 text-xs ${
              step === n ? 'bg-violet-600 text-white' : 'bg-zinc-800 text-zinc-400'
            }`}
          >
            Step {n}
          </button>
        ))}
      </div>

      {msg && (
        <p className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-4 py-2 text-sm text-emerald-200">
          {msg}
        </p>
      )}
      {err && (
        <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-2 text-sm text-red-300">{err}</p>
      )}

      {step === 1 && (
        <section className="rounded-xl border border-violet-500/40 bg-violet-950/20 p-6">
          <h2 className="text-lg font-semibold">Step 1 — Connect Phantom (popup)</h2>
          <p className="mt-2 text-sm text-zinc-400">
            This links your admin wallet to the platform. Phantom will ask you to <strong>sign a message</strong>{' '}
            (free, no SOL spent).
          </p>
          {linked ? (
            <div className="mt-4">
              <code className="block break-all rounded-lg bg-zinc-950 px-3 py-2 text-xs text-emerald-300">{linked}</code>
              <button
                type="button"
                onClick={() => setStep(2)}
                className="mt-4 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium"
              >
                Continue to Step 2 →
              </button>
            </div>
          ) : (
            <button
              type="button"
              disabled={walletBusy || busy}
              onClick={() => {
                setSolanaWallets(listSolanaWallets());
                setSolanaPickerOpen(true);
              }}
              className="mt-4 rounded-lg bg-purple-600 px-5 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
            >
              {walletBusy ? 'Waiting for Phantom…' : 'Connect Phantom — sign message'}
            </button>
          )}
          <p className="mt-3 text-xs text-zinc-500">
            Or use{' '}
            <Link href="/account?tab=security" className="text-violet-300 underline">
              Account → Security
            </Link>{' '}
            then return here.
          </p>
        </section>
      )}

      {step === 2 && (
        <section className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
          <h2 className="text-lg font-semibold">Step 2 — Save fee treasury</h2>
          <p className="mt-2 text-sm text-zinc-400">
            Signal success fees (USDC) and platform payouts go to this Phantom address.
          </p>
          <p className="mt-2 font-mono text-xs text-emerald-300">{linked ?? 'Connect Phantom first'}</p>
          <label className="mt-4 block text-sm">
            <span className="text-zinc-400">EVM wallet for Spawn (MetaMask on Base) — optional now</span>
            <input
              value={evmTreasury}
              onChange={(e) => setEvmTreasury(e.target.value)}
              className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm"
              placeholder="0x… for ERC-8004 mint later"
            />
          </label>
          <button
            type="button"
            disabled={!linked || busy}
            onClick={() => void saveTreasuryFromWizard()}
            className="mt-4 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium disabled:opacity-50"
          >
            Save treasury & continue
          </button>
        </section>
      )}

      {step === 3 && (
        <section className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
          <h2 className="text-lg font-semibold">Step 3 — SAID Protocol (Solana on-chain)</h2>
          <p className="mt-2 text-sm text-zinc-400">
            Register your agent identity on Solana. You will sign <strong>one transaction</strong> in Phantom (~0.001
            SOL gas). Optional verify badge: +0.01 SOL.
          </p>
          <ol className="mt-4 list-decimal space-y-2 pl-5 text-sm text-zinc-300">
            <li>Ensure Phantom has ~0.02 SOL on mainnet.</li>
            <li>
              Open{' '}
              <a
                href="https://www.saidprotocol.com/"
                target="_blank"
                rel="noreferrer"
                className="text-violet-300 underline"
              >
                saidprotocol.com
              </a>{' '}
              → <strong>Get Started</strong> (wallet popup to register).
            </li>
            <li>
              When asked for metadata URI, paste:{' '}
              <button
                type="button"
                onClick={() => copy(METADATA_URI, 'Metadata URI')}
                className="text-violet-300 underline"
              >
                {METADATA_URI}
              </button>
            </li>
            <li>Approve the register transaction in Phantom.</li>
          </ol>
          <details className="mt-4 rounded-lg border border-zinc-800 bg-zinc-900/50 p-3 text-xs text-zinc-400">
            <summary className="cursor-pointer text-zinc-300">CLI alternative (advanced)</summary>
            <pre className="mt-2 overflow-x-auto whitespace-pre-wrap">
              {`npx said-sdk register -k your-wallet.json -n "Conservative BTC Agent" --uri "${METADATA_URI}"`}
            </pre>
          </details>
          <label className="mt-4 block text-sm">
            <span className="text-zinc-400">Paste SAID transaction signature after signing</span>
            <input
              value={saidTx}
              onChange={(e) => setSaidTx(e.target.value)}
              className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-xs"
              placeholder="Solana tx signature"
            />
          </label>
          <button
            type="button"
            disabled={busy}
            onClick={() => void onMarkRegistered('SAID', saidTx.trim() || undefined).then(() => setStep(4))}
            className="mt-3 rounded-lg border border-emerald-600/50 bg-emerald-950/30 px-4 py-2 text-sm text-emerald-200"
          >
            I signed SAID — mark registered
          </button>
        </section>
      )}

      {step === 4 && (
        <section className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
          <h2 className="text-lg font-semibold">Step 4 — The Spawn / ERC-8004 (Base)</h2>
          <p className="mt-2 text-sm text-zinc-400">
            Mint on-chain identity on Base with MetaMask. Needs a small amount of Base ETH for gas.
          </p>
          <ol className="mt-4 list-decimal space-y-2 pl-5 text-sm text-zinc-300">
            <li>Switch MetaMask to <strong>Base</strong> network.</li>
            <li>
              Metadata URI:{' '}
              <button type="button" onClick={() => copy(AGENT_JSON_URI, 'Agent JSON URI')} className="text-violet-300 underline">
                {AGENT_JSON_URI}
              </button>
            </li>
            <li>
              Request API key at{' '}
              <a href="https://thespawn.io" target="_blank" rel="noreferrer" className="text-violet-300 underline">
                thespawn.io
              </a>{' '}
              or use{' '}
              <code className="text-zinc-400">npm run prepare:agent-registrations</code>
            </li>
            <li>Sign <code>register(string)</code> on contract 0x8004A169…9a432</li>
            <li>Run: <code>npx spawnr@latest check base:&lt;agent_id&gt;</code></li>
          </ol>
          <label className="mt-4 block text-sm">
            <span className="text-zinc-400">Base mint tx hash (optional)</span>
            <input
              value={spawnTx}
              onChange={(e) => setSpawnTx(e.target.value)}
              className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-xs"
              placeholder="0x…"
            />
          </label>
          <button
            type="button"
            disabled={busy}
            onClick={() => void onMarkRegistered('SPAWN', spawnTx.trim() || undefined).then(() => setStep(5))}
            className="mt-3 rounded-lg border border-emerald-600/50 bg-emerald-950/30 px-4 py-2 text-sm text-emerald-200"
          >
            I signed Spawn mint — mark registered
          </button>
        </section>
      )}

      {step === 5 && (
        <section className="rounded-xl border border-emerald-500/30 bg-emerald-950/15 p-6">
          <h2 className="text-lg font-semibold text-emerald-100">Step 5 — Manual directories</h2>
          <ul className="mt-3 space-y-2 text-sm text-zinc-300">
            <li>
              <a href="https://aiagentsdirectory.com/" target="_blank" rel="noreferrer" className="text-violet-300 underline">
                aiagentsdirectory.com
              </a>{' '}
              — submit AgentCard + hub URL
            </li>
            <li>
              <a href="https://www.openserv.ai/" target="_blank" rel="noreferrer" className="text-violet-300 underline">
                openserv.ai
              </a>{' '}
              — apply with API docs link
            </li>
          </ul>
          <p className="mt-4 text-xs text-zinc-500">
            DexScreener / Linktree / token sites are optional promo only — this agent is a signal API, not a token.
          </p>
          <button
            type="button"
            disabled={busy}
            onClick={() => void refreshProfile()}
            className="mt-4 text-sm text-violet-300 underline"
          >
            Refresh wallet status
          </button>
        </section>
      )}

      {solanaPickerOpen && (
        <WalletPickerModal
          title="Connect Phantom for admin registration"
          wallets={solanaWallets}
          onPick={(w) => void connectPhantom(w as SolanaWalletOption)}
          onClose={() => setSolanaPickerOpen(false)}
        />
      )}
    </div>
  );
}
