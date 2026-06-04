'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { fetchAttestationDashboard, fetchVaultCvmStatus } from '@/lib/api';
import { AI_STACK_HREF } from '@/lib/copilot-ai-stack';

type Props = {
  accessToken: string;
  onRefresh?: () => void;
};

function formatRelative(iso: string | null): string {
  if (!iso) return 'never';
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return 'just now';
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)}h ago`;
  return `${Math.floor(ms / 86_400_000)}d ago`;
}

export function MissionControlTrustStrip({ accessToken, onRefresh }: Props) {
  const [loading, setLoading] = useState(true);
  const [memoryScore, setMemoryScore] = useState<number | null>(null);
  const [memoryStatus, setMemoryStatus] = useState<'healthy' | 'partial' | 'offline' | null>(null);
  const [memoryMode, setMemoryMode] = useState<string | null>(null);
  const [teeVerified, setTeeVerified] = useState(false);
  const [teePending, setTeePending] = useState(false);
  const [lastAttested, setLastAttested] = useState<string | null>(null);
  const [phalaSealed, setPhalaSealed] = useState(false);
  const [cvmBackupVerified, setCvmBackupVerified] = useState(false);
  const [secretsSummary, setSecretsSummary] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [d, cvm] = await Promise.all([
        fetchAttestationDashboard(accessToken),
        fetchVaultCvmStatus(accessToken).catch(() => null),
      ]);
      setMemoryScore(d.memoryIntegrity.score);
      setMemoryStatus(d.memoryIntegrity.status);
      setMemoryMode(d.memoryIntegrity.mode);
      const latest = d.phalaTee.latest;
      const verified = Boolean(latest?.verified) || d.phalaTee.verifiedCount > 0;
      setTeeVerified(verified);
      setTeePending(!verified && d.phalaTee.recentCount > 0);
      const attestedAt =
        latest?.verified && latest.createdAt
          ? latest.createdAt
          : d.memoryIntegrity.lastVaultScanAt;
      setLastAttested(attestedAt);
      setPhalaSealed(Boolean(d.secretsStorage?.phalaInferenceOnly));
      setCvmBackupVerified(cvm?.backupState === 'verified');
      setSecretsSummary(d.secretsStorage?.summary ?? null);
    } catch {
      setMemoryScore(null);
      setMemoryStatus(null);
      setTeeVerified(false);
      setTeePending(false);
      setPhalaSealed(false);
      setCvmBackupVerified(false);
      setSecretsSummary(null);
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  const memoryPill =
    memoryStatus === 'healthy'
      ? 'bg-emerald-950/50 text-emerald-300'
      : memoryStatus === 'partial'
        ? 'bg-amber-950/40 text-amber-200'
        : 'bg-zinc-800 text-zinc-500';

  const settingsHref = `${AI_STACK_HREF}#founder-attestation`;

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-emerald-500/20 bg-gradient-to-r from-emerald-950/15 to-zinc-900/40 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-semibold text-emerald-400/90">Privacy</span>
        {loading ? (
          <span className="text-zinc-600">Checking attestation…</span>
        ) : (
          <>
            {memoryScore != null && (
              <span className={`rounded-full px-2 py-0.5 ${memoryPill}`}>
                Vault {memoryScore}%
                {memoryStatus === 'healthy' ? ' ✓' : ''}
              </span>
            )}
            {memoryMode && (
              <span className="text-zinc-600">
                · {memoryMode.replace(/_/g, ' ')}
              </span>
            )}
            {phalaSealed && (
              <span className="rounded-full bg-violet-950/50 px-2 py-0.5 text-violet-200">
                Phala inference-sealed
              </span>
            )}
            {cvmBackupVerified && (
              <span className="rounded-full bg-violet-950/50 px-2 py-0.5 text-violet-200">
                CVM vault backup
              </span>
            )}
            {teeVerified ? (
              <span className="rounded-full bg-emerald-600/20 px-2 py-0.5 font-medium text-emerald-300">
                TEE verified
              </span>
            ) : teePending ? (
              <span className="rounded-full bg-amber-950/50 px-2 py-0.5 text-amber-200">
                TEE receipt pending
              </span>
            ) : (
              <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-zinc-500">
                TEE not attested
              </span>
            )}
            <span className="text-zinc-600">
              · Last checked {formatRelative(lastAttested)}
            </span>
            {secretsSummary && (
              <span className="hidden text-zinc-600 lg:inline" title={secretsSummary}>
                · Keys sealed at rest
              </span>
            )}
          </>
        )}
      </div>
      <Link
        href={settingsHref}
        className="text-xs font-medium text-emerald-400 hover:text-emerald-300"
        onClick={() => onRefresh?.()}
      >
        Verify vault & TEE →
      </Link>
    </div>
  );
}
