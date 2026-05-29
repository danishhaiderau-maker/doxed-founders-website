'use client';

import { startRegistration } from '@simplewebauthn/browser';
import bs58 from 'bs58';
import { useCallback, useEffect, useState } from 'react';
import {
  changePassword,
  deletePasskey,
  disableTotp,
  disconnectWallet,
  enableTotp,
  fetchSecurityProfile,
  generateRecoveryCodes,
  passkeyRegisterOptions,
  passkeyRegisterVerify,
  SecurityProfile,
  setupTotp,
  walletChallenge,
  walletVerify,
} from '@/lib/api';

type SolanaProvider = {
  connect(): Promise<{ publicKey: { toString(): string } }>;
  signMessage(message: Uint8Array, display?: string): Promise<{ signature: Uint8Array }>;
  publicKey?: { toString(): string };
  isConnected?: boolean;
};

function getSolanaProvider(): SolanaProvider | null {
  if (typeof window === 'undefined') return null;
  return window.phantom?.solana ?? window.solflare ?? window.backpack ?? null;
}

declare global {
  interface Window {
    phantom?: { solana?: SolanaProvider };
    solflare?: SolanaProvider;
    backpack?: SolanaProvider;
  }
}

export function SecuritySettingsPanel({ accessToken }: { accessToken: string }) {
  const [profile, setProfile] = useState<SecurityProfile | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [totpSecret, setTotpSecret] = useState<string | null>(null);
  const [totpCode, setTotpCode] = useState('');
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [passwordForm, setPasswordForm] = useState({ current: '', next: '' });

  const load = useCallback(async () => {
    try {
      setProfile(await fetchSecurityProfile(accessToken));
    } catch {
      setProfile(null);
    }
  }, [accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  if (!profile) {
    return <p className="text-sm text-zinc-500">Loading security profile…</p>;
  }

  const score = profile.securityScore;

  return (
    <div className="space-y-8">
      {msg && (
        <p className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-4 py-2 text-sm text-emerald-200">
          {msg}
        </p>
      )}
      {err && (
        <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-2 text-sm text-red-300">
          {err}
        </p>
      )}

      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-white">Security score</h2>
            <p className="mt-1 text-sm text-zinc-500">Stronger account = safer founder workspace</p>
          </div>
          <p className="text-4xl font-bold text-emerald-400">
            {score.score}
            <span className="text-lg text-zinc-500">/{score.maxScore}</span>
          </p>
        </div>
        <ul className="mt-6 grid gap-2 sm:grid-cols-2">
          {score.items.map((item) => (
            <li
              key={item.key}
              className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm ${
                item.enabled
                  ? 'border-emerald-500/30 text-emerald-200'
                  : 'border-zinc-800 text-zinc-500'
              }`}
            >
              <span>{item.enabled ? '✓' : '○'}</span>
              {item.label}
            </li>
          ))}
        </ul>
      </section>

      {profile.hasPassword && (
        <section className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
          <h2 className="font-semibold text-white">Password</h2>
          <div className="mt-4 grid gap-3 sm:max-w-md">
            <input
              type="password"
              value={passwordForm.current}
              onChange={(e) => setPasswordForm({ ...passwordForm, current: e.target.value })}
              placeholder="Current password"
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
            />
            <input
              type="password"
              value={passwordForm.next}
              onChange={(e) => setPasswordForm({ ...passwordForm, next: e.target.value })}
              placeholder="New password (8+ chars)"
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
            />
            <button
              type="button"
              onClick={async () => {
                setErr(null);
                try {
                  await changePassword(passwordForm.current, passwordForm.next, accessToken);
                  setMsg('Password updated');
                  setPasswordForm({ current: '', next: '' });
                } catch (e) {
                  setErr(e instanceof Error ? e.message : 'Password change failed');
                }
              }}
              className="rounded-lg bg-zinc-700 py-2 text-sm text-white hover:bg-zinc-600"
            >
              Change password
            </button>
          </div>
        </section>
      )}

      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
        <h2 className="font-semibold text-white">Authenticator app (TOTP)</h2>
        <p className="mt-1 text-xs text-zinc-500">Google Authenticator, Authy, 1Password, etc.</p>
        {!profile.totpEnabled && !totpSecret && (
          <button
            type="button"
            onClick={async () => {
              setErr(null);
              try {
                const r = await setupTotp(accessToken);
                setTotpSecret(r.secret);
                setMsg('Scan the secret in your authenticator app, then enter a code to enable.');
              } catch (e) {
                setErr(e instanceof Error ? e.message : 'Setup failed');
              }
            }}
            className="mt-4 rounded-lg bg-emerald-600 px-4 py-2 text-sm text-white"
          >
            Set up 2FA
          </button>
        )}
        {totpSecret && !profile.totpEnabled && (
          <div className="mt-4 space-y-3">
            <p className="break-all rounded-lg bg-zinc-950 p-3 font-mono text-xs text-amber-200">
              {totpSecret}
            </p>
            <input
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value)}
              placeholder="6-digit code"
              className="w-full max-w-xs rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
            />
            <button
              type="button"
              onClick={async () => {
                try {
                  await enableTotp(totpCode, accessToken);
                  setTotpSecret(null);
                  setTotpCode('');
                  setMsg('2FA enabled');
                  load();
                } catch (e) {
                  setErr(e instanceof Error ? e.message : 'Invalid code');
                }
              }}
              className="rounded-lg bg-emerald-600 px-4 py-2 text-sm text-white"
            >
              Enable 2FA
            </button>
          </div>
        )}
        {profile.totpEnabled && (
          <div className="mt-4 flex flex-wrap gap-2">
            <input
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value)}
              placeholder="Code to disable"
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
            />
            <button
              type="button"
              onClick={async () => {
                try {
                  await disableTotp(totpCode, accessToken);
                  setTotpCode('');
                  setMsg('2FA disabled');
                  load();
                } catch (e) {
                  setErr(e instanceof Error ? e.message : 'Failed');
                }
              }}
              className="rounded-lg border border-red-500/40 px-4 py-2 text-sm text-red-300"
            >
              Disable 2FA
            </button>
          </div>
        )}
      </section>

      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
        <h2 className="font-semibold text-white">Passkeys & hardware keys</h2>
        <p className="mt-1 text-xs text-zinc-500">Face ID, Touch ID, Windows Hello, YubiKey</p>
        <button
          type="button"
          onClick={async () => {
            setErr(null);
            try {
              const { options, registerToken } = await passkeyRegisterOptions(accessToken);
              const attestation = await startRegistration({ optionsJSON: options as never });
              await passkeyRegisterVerify(registerToken, attestation as never, accessToken, 'Passkey');
              setMsg('Passkey registered');
              load();
            } catch (e) {
              setErr(e instanceof Error ? e.message : 'Passkey registration failed');
            }
          }}
          className="mt-4 rounded-lg bg-sky-600 px-4 py-2 text-sm text-white"
        >
          Add passkey
        </button>
        {profile.passkeys.length > 0 && (
          <ul className="mt-4 space-y-2">
            {profile.passkeys.map((pk) => (
              <li
                key={pk.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-zinc-800 px-3 py-2 text-sm"
              >
                <span className="text-zinc-300">
                  {pk.label ?? 'Passkey'} · {pk.deviceType ?? 'device'}
                </span>
                <button
                  type="button"
                  onClick={async () => {
                    await deletePasskey(pk.credentialId, accessToken);
                    setMsg('Passkey removed');
                    load();
                  }}
                  className="text-xs text-red-400 hover:underline"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
        <h2 className="font-semibold text-white">Backup recovery codes</h2>
        <p className="mt-1 text-xs text-zinc-500">
          One-time codes if you lose your authenticator. Remaining: {profile.recoveryCodesRemaining}
        </p>
        {recoveryCodes && (
          <ul className="mt-3 grid gap-1 font-mono text-sm text-amber-200 sm:grid-cols-2">
            {recoveryCodes.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        )}
        {profile.totpEnabled && (
          <button
            type="button"
            onClick={async () => {
              const code = prompt('Enter current authenticator code');
              if (!code) return;
              try {
                const r = await generateRecoveryCodes(code, accessToken);
                setRecoveryCodes(r.codes);
                setMsg('Save these codes somewhere safe — shown once');
                load();
              } catch (e) {
                setErr(e instanceof Error ? e.message : 'Failed');
              }
            }}
            className="mt-4 rounded-lg border border-amber-500/40 px-4 py-2 text-sm text-amber-200"
          >
            Generate new backup codes
          </button>
        )}
      </section>

      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
        <h2 className="font-semibold text-white">Solana wallet (sign-only)</h2>
        <p className="mt-1 text-xs text-zinc-500">
          Phantom, Backpack, or Solflare. We never store seed phrases or private keys.
        </p>
        {profile.wallet ? (
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <code className="break-all text-xs text-emerald-300">{profile.wallet.address}</code>
            <button
              type="button"
              onClick={async () => {
                await disconnectWallet(accessToken);
                setMsg('Wallet disconnected');
                load();
              }}
              className="text-xs text-red-400 hover:underline"
            >
              Disconnect
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={async () => {
              setErr(null);
              const provider = getSolanaProvider();
              if (!provider) {
                setErr('Install Phantom, Backpack, or Solflare');
                return;
              }
              try {
                const { challengeToken, message } = await walletChallenge(accessToken);
                const conn = await provider.connect();
                const address = conn.publicKey.toString();
                const encoded = new TextEncoder().encode(message);
                const { signature } = await provider.signMessage(encoded, 'utf8');
                await walletVerify(
                  challengeToken,
                  address,
                  bs58.encode(signature),
                  message,
                  accessToken,
                );
                setMsg('Wallet verified and linked');
                load();
              } catch (e) {
                setErr(e instanceof Error ? e.message : 'Wallet connect failed');
              }
            }}
            className="mt-4 rounded-lg bg-purple-600 px-4 py-2 text-sm text-white"
          >
            Connect wallet
          </button>
        )}
      </section>
    </div>
  );
}
