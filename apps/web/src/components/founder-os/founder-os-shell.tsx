'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  CheckCircle2,
  Cloud,
  Gauge,
  KeyRound,
  Lightbulb,
  MonitorSmartphone,
  Plug,
  Rocket,
  ShieldCheck,
  Wrench,
  type LucideIcon,
} from 'lucide-react';
import { apiUrl } from '@/lib/api-base';
import {
  fetchFounderPlanCatalog,
  fetchFounderPlanEntitlement,
  fetchFounderPromoStatus,
  type FounderPlanCatalog,
  type FounderPlanEntitlement,
  type FounderPromoUserStatus,
} from '@/lib/api';
import { FounderPlanSummary } from '@/components/account/founder-plan-summary';
import { FounderFreeQuotaCard } from '@/components/account/founder-free-quota-card';
import type { FounderPlanLoadState } from '@/components/account/founder-plan-account-state';
import { IdeaValidatorPanel } from '@/components/idea-validator/idea-validator-panel';
import { IdeaPopUp } from '@/components/idea-validator/idea-pop-up';
import { LamTaskSubmitter } from '@/components/lam/lam-task-submitter';
import { ConsentPopup } from '@/components/debug-squasher/consent-popup';
import { DailyReportCard } from '@/components/debug-squasher/daily-report-card';

type DashboardData = {
  founder?: {
    id: string;
    tier?: string;
    contributorLevel?: string;
    reputationPoints?: number;
    lifetimeContributionEarned?: number;
  } | null;
  user?: {
    reputationPoints?: number;
    contributorLevel?: string;
  };
  connectedApps?: Array<{
    provider: string;
    label: string;
    connectedAt?: string | null;
  }>;
};

type ToolView = 'review' | 'idea' | 'action';
type Props = { accessToken: string };

const WORKSPACE_ACTIONS: Array<{
  href: string;
  label: string;
  detail: string;
  icon: LucideIcon;
  tone: string;
}> = [
  {
    href: '/founder-den?tab=build',
    label: 'Continue building',
    detail: 'Projects, tasks, decisions, and proofs',
    icon: Rocket,
    tone: 'text-emerald-300 bg-emerald-500/10',
  },
  {
    href: '/phone',
    label: 'Control desktop',
    detail: 'Reach your paired Founder IDE securely',
    icon: MonitorSmartphone,
    tone: 'text-sky-300 bg-sky-500/10',
  },
  {
    href: '/settings/integrations',
    label: 'Connect services',
    detail: 'GitHub, Vercel, Railway, Neon, and more',
    icon: Plug,
    tone: 'text-amber-300 bg-amber-500/10',
  },
  {
    href: '/settings/builder?tab=ai',
    label: 'Manage AI',
    detail: 'Founder AI, personal keys, and local Ollama',
    icon: KeyRound,
    tone: 'text-cyan-300 bg-cyan-500/10',
  },
];

const TOOL_VIEWS: Array<{
  id: ToolView;
  label: string;
  detail: string;
  icon: LucideIcon;
}> = [
  {
    id: 'review',
    label: 'Daily review',
    detail: 'Health, links, and release evidence',
    icon: ShieldCheck,
  },
  {
    id: 'idea',
    label: 'Validate idea',
    detail: 'Research before committing resources',
    icon: Lightbulb,
  },
  {
    id: 'action',
    label: 'Run action',
    detail: 'Execute an approved browser task',
    icon: Wrench,
  },
];

function planName(plan: FounderPlanEntitlement['plan'] | undefined): string {
  if (plan === 'builder') return 'Founder Builder';
  if (plan === 'team') return 'Founder Team';
  return 'Founder Free';
}

export function FounderOsShell({ accessToken }: Props) {
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [dashboardError, setDashboardError] = useState<string | null>(null);
  const [toolView, setToolView] = useState<ToolView>('review');
  const [planCatalog, setPlanCatalog] = useState<FounderPlanCatalog | null>(null);
  const [planEntitlement, setPlanEntitlement] = useState<FounderPlanEntitlement | null>(null);
  const [promoStatus, setPromoStatus] = useState<FounderPromoUserStatus | null>(null);
  const [planLoadState, setPlanLoadState] = useState<FounderPlanLoadState>('loading');
  const [planLoadError, setPlanLoadError] = useState<string | null>(null);

  const loadDashboard = useCallback(async () => {
    setDashboardError(null);
    try {
      const response = await fetch(apiUrl('/api/founder-os/dashboard'), {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (!response.ok) {
        throw new Error(`Workspace status could not be loaded (${response.status}).`);
      }
      setDashboard((await response.json()) as DashboardData);
    } catch (reason) {
      setDashboardError(
        reason instanceof Error ? reason.message : 'Workspace status could not be loaded.',
      );
    }
  }, [accessToken]);

  const loadPlan = useCallback(async () => {
    setPlanLoadState('loading');
    setPlanLoadError(null);
    setPlanCatalog(null);
    setPlanEntitlement(null);
    try {
      const [catalog, entitlement, promo] = await Promise.all([
        fetchFounderPlanCatalog(),
        fetchFounderPlanEntitlement(accessToken),
        fetchFounderPromoStatus(accessToken).catch(() => null),
      ]);
      setPlanCatalog(catalog);
      setPlanEntitlement(entitlement);
      setPromoStatus(promo);
      setPlanLoadState('ready');
    } catch (reason) {
      setPlanLoadError(
        reason instanceof Error ? reason.message : 'Founder plan could not be loaded.',
      );
      setPlanLoadState('error');
    }
  }, [accessToken]);

  useEffect(() => {
    void loadDashboard();
    void loadPlan();
  }, [loadDashboard, loadPlan]);

  const points =
    dashboard?.founder?.reputationPoints
    ?? dashboard?.user?.reputationPoints
    ?? null;
  const contributor =
    dashboard?.founder?.tier
    ?? dashboard?.user?.contributorLevel
    ?? null;
  const isDoxxed = contributor
    ? ['VERIFIED_BUILDER', 'DOXXED'].includes(String(contributor).toUpperCase())
    : false;

  return (
    <div className="space-y-8">
      <WorkspaceStatus
        plan={planEntitlement}
        points={points}
        connectedServices={dashboard?.connectedApps?.length ?? 0}
        error={dashboardError}
      />

      <section aria-labelledby="workspace-actions">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="text-xs font-medium text-zinc-500">Workspace</p>
            <h2 id="workspace-actions" className="mt-1 text-xl font-semibold text-white">
              Build, connect, and ship
            </h2>
          </div>
          <Link
            href="/account?tab=plan"
            className="inline-flex items-center gap-2 text-sm text-zinc-400 transition hover:text-white"
          >
            <Gauge className="h-4 w-4" aria-hidden />
            Plan and usage
          </Link>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {WORKSPACE_ACTIONS.map((action) => (
            <Link
              key={action.href}
              href={action.href}
              className="group flex min-h-24 items-start gap-3 rounded-lg border border-zinc-800 bg-zinc-950/50 p-4 transition hover:border-zinc-600 hover:bg-zinc-900/70"
            >
              <span
                className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${action.tone}`}
              >
                <action.icon className="h-4 w-4" strokeWidth={1.8} aria-hidden />
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-semibold text-zinc-100">
                  {action.label}
                </span>
                <span className="mt-1 block text-xs leading-5 text-zinc-500">
                  {action.detail}
                </span>
              </span>
            </Link>
          ))}
        </div>
      </section>

      <section className="border-t border-zinc-800 pt-8" aria-labelledby="plan-and-usage">
        <div className="mb-6">
          <p className="text-xs font-medium text-zinc-500">Founder AI</p>
          <h2 id="plan-and-usage" className="mt-1 text-xl font-semibold text-white">
            Plan and usage
          </h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-400">
            Live entitlement data is the source of truth. Founder-managed DeepSeek, personal AI
            profiles, and local Ollama stay clearly separated.
          </p>
        </div>
        <div className="space-y-6">
          <FounderPlanSummary
            token={accessToken}
            catalog={planCatalog}
            entitlement={planEntitlement}
            loadState={planLoadState}
            loadError={planLoadError}
            onRetry={() => void loadPlan()}
          />
          <FounderFreeQuotaCard status={promoStatus} />
        </div>
      </section>

      <WorkspaceTools
        accessToken={accessToken}
        selected={toolView}
        onSelect={setToolView}
      />

      <ConnectedServices services={dashboard?.connectedApps ?? []} />

      {!isDoxxed ? <FounderIdentity accessToken={accessToken} /> : null}

      <IdeaPopUp accessToken={accessToken} />
      <ConsentPopup />
    </div>
  );
}

function WorkspaceStatus({
  plan,
  points,
  connectedServices,
  error,
}: {
  plan: FounderPlanEntitlement | null;
  points: number | null;
  connectedServices: number;
  error: string | null;
}) {
  return (
    <section className="border-b border-zinc-800 pb-6" aria-label="Founder workspace status">
      <div className="flex flex-wrap items-start justify-between gap-5">
        <div>
          <div className="flex items-center gap-2 text-sm font-medium text-emerald-300">
            <span className="h-2 w-2 rounded-full bg-emerald-400" aria-hidden />
            Founder workspace
          </div>
          <h2 className="mt-2 text-2xl font-semibold text-white">
            Everything important, one place
          </h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-400">
            Continue a build, reach your desktop, review usage, or connect the service your project
            needs next.
          </p>
        </div>
        <div className="grid min-w-64 grid-cols-3 gap-4 text-right">
          <StatusValue label="Plan" value={plan ? planName(plan.plan) : 'Checking'} />
          <StatusValue
            label="DDollar"
            value={points == null ? 'Checking' : points.toLocaleString()}
          />
          <StatusValue label="Connected" value={String(connectedServices)} />
        </div>
      </div>
      {error ? (
        <p className="mt-4 text-sm text-amber-200" role="status">
          {error}
        </p>
      ) : null}
    </section>
  );
}

function StatusValue({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-zinc-500">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold text-zinc-100">{value}</p>
    </div>
  );
}

function WorkspaceTools({
  accessToken,
  selected,
  onSelect,
}: {
  accessToken: string;
  selected: ToolView;
  onSelect: (view: ToolView) => void;
}) {
  return (
    <section className="border-t border-zinc-800 pt-8" aria-labelledby="workspace-tools">
      <p className="text-xs font-medium text-zinc-500">Focused tools</p>
      <h2 id="workspace-tools" className="mt-1 text-xl font-semibold text-white">
        Choose the job, then see only what it needs
      </h2>
      <div
        className="mt-4 grid gap-1 rounded-lg border border-zinc-800 bg-zinc-950/60 p-1 sm:grid-cols-3"
        role="tablist"
        aria-label="Founder workspace tools"
      >
        {TOOL_VIEWS.map((view) => (
          <button
            key={view.id}
            type="button"
            role="tab"
            aria-selected={selected === view.id}
            onClick={() => onSelect(view.id)}
            className={`flex min-h-14 items-center gap-3 rounded-md px-3 py-2 text-left transition ${
              selected === view.id
                ? 'bg-zinc-800 text-white'
                : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100'
            }`}
          >
            <view.icon className="h-4 w-4 shrink-0" strokeWidth={1.8} aria-hidden />
            <span className="min-w-0">
              <span className="block text-sm font-semibold">{view.label}</span>
              <span className="block truncate text-xs text-zinc-500">{view.detail}</span>
            </span>
          </button>
        ))}
      </div>
      <div className="mt-5" role="tabpanel">
        {selected === 'review' ? <DailyReportCard /> : null}
        {selected === 'idea' ? <IdeaValidatorPanel accessToken={accessToken} /> : null}
        {selected === 'action' ? <LamTaskSubmitter accessToken={accessToken} /> : null}
      </div>
    </section>
  );
}

function ConnectedServices({
  services,
}: {
  services: NonNullable<DashboardData['connectedApps']>;
}) {
  return (
    <section className="border-t border-zinc-800 pt-8" aria-labelledby="connected-services">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-zinc-500">Infrastructure</p>
          <h2 id="connected-services" className="mt-1 text-xl font-semibold text-white">
            Connected services
          </h2>
        </div>
        <Link
          href="/settings/integrations"
          className="inline-flex items-center gap-2 text-sm text-zinc-400 transition hover:text-white"
        >
          <Cloud className="h-4 w-4" aria-hidden />
          Manage connections
        </Link>
      </div>
      {services.length === 0 ? (
        <div className="mt-4 flex items-start gap-3 rounded-lg border border-dashed border-zinc-700 p-4">
          <Plug className="mt-0.5 h-4 w-4 shrink-0 text-zinc-500" aria-hidden />
          <div>
            <p className="text-sm font-medium text-zinc-200">No service is connected yet</p>
            <p className="mt-1 text-xs leading-5 text-zinc-500">
              Add only what the current project needs. Founder OS will keep credentials and
              deployment access in one place.
            </p>
          </div>
        </div>
      ) : (
        <ul className="mt-4 divide-y divide-zinc-800 border-y border-zinc-800">
          {services.map((service) => (
            <li
              key={`${service.provider}:${service.label}`}
              className="flex min-h-12 items-center justify-between gap-4 py-3"
            >
              <span className="flex min-w-0 items-center gap-3">
                <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-300" aria-hidden />
                <span className="truncate text-sm font-medium text-zinc-200">{service.label}</span>
              </span>
              <span className="text-xs text-zinc-500">{service.provider}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function FounderIdentity({ accessToken }: { accessToken: string }) {
  const [expanded, setExpanded] = useState(false);
  const [form, setForm] = useState({
    githubUrl: '',
    twitterHandle: '',
    videoUrl: '',
    ideaDescription: '',
    projectName: '',
  });
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<
    { ok: true; message: string } | { ok: false; error: string } | null
  >(null);

  async function submit() {
    if (!form.githubUrl || !form.videoUrl || !form.ideaDescription) {
      setResult({
        ok: false,
        error: 'GitHub, founder video, and idea description are required.',
      });
      return;
    }
    setSubmitting(true);
    setResult(null);
    try {
      const response = await fetch(apiUrl('/api/founder-applications'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({
          ...form,
          lifecycleStage: 'IDEA',
        }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as { message?: string };
        throw new Error(body.message ?? `Submission failed (${response.status}).`);
      }
      setResult({
        ok: true,
        message: 'Application submitted. The team will review it and respond in your account.',
      });
    } catch (reason) {
      setResult({
        ok: false,
        error: reason instanceof Error ? reason.message : 'Submission failed.',
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="border-t border-zinc-800 pt-8" aria-labelledby="founder-identity">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex max-w-2xl items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-300">
            <ShieldCheck className="h-4 w-4" aria-hidden />
          </span>
          <div>
            <h2 id="founder-identity" className="text-lg font-semibold text-white">
              Verify your founder identity
            </h2>
            <p className="mt-1 text-sm leading-6 text-zinc-400">
              Connect your public work and a short founder video. Verification unlocks the
              builder workflow; your live plan still controls managed AI usage.
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setExpanded((current) => !current)}
          className="rounded-lg border border-zinc-700 px-4 py-2 text-sm font-semibold text-zinc-200 transition hover:border-zinc-500 hover:text-white"
          aria-expanded={expanded}
        >
          {expanded ? 'Close application' : 'Start verification'}
        </button>
      </div>

      {expanded ? (
        <div className="mt-5 grid gap-4 border-t border-zinc-800 pt-5 md:grid-cols-2">
          <Field
            label="GitHub URL"
            value={form.githubUrl}
            onChange={(value) => setForm({ ...form, githubUrl: value })}
            placeholder="https://github.com/your-handle"
          />
          <Field
            label="Project or company"
            value={form.projectName}
            onChange={(value) => setForm({ ...form, projectName: value })}
            placeholder="What are you building?"
          />
          <Field
            label="X handle"
            value={form.twitterHandle}
            onChange={(value) => setForm({ ...form, twitterHandle: value })}
            placeholder="@your-handle"
          />
          <Field
            label="Founder video URL"
            value={form.videoUrl}
            onChange={(value) => setForm({ ...form, videoUrl: value })}
            placeholder="YouTube, Loom, or a direct video link"
          />
          <div className="md:col-span-2">
            <TextArea
              label="What are you building?"
              value={form.ideaDescription}
              onChange={(value) => setForm({ ...form, ideaDescription: value })}
              placeholder="The problem, the product, and why you are building it."
            />
          </div>
          <div className="flex flex-wrap items-center gap-3 md:col-span-2">
            <button
              type="button"
              disabled={submitting}
              onClick={() => void submit()}
              className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-black transition hover:bg-emerald-400 disabled:opacity-50"
            >
              {submitting ? 'Submitting...' : 'Submit for review'}
            </button>
            {result?.ok ? (
              <p className="text-sm text-emerald-200" role="status">{result.message}</p>
            ) : null}
            {result && !result.ok ? (
              <p className="text-sm text-red-300" role="alert">{result.error}</p>
            ) : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="block text-sm">
      <span className="text-zinc-400">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
        placeholder={placeholder}
        className="mt-1.5 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 outline-none transition focus:border-blue-400"
      />
    </label>
  );
}

function TextArea({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="block text-sm">
      <span className="text-zinc-400">{label}</span>
      <textarea
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
        placeholder={placeholder}
        rows={4}
        className="mt-1.5 w-full resize-y rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 outline-none transition focus:border-blue-400"
      />
    </label>
  );
}
