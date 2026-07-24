'use client';

import { FormEvent, useCallback, useEffect, useState } from 'react';
import { RefreshCw, Trash2, UserPlus, Users } from 'lucide-react';
import {
  addFounderTeamMember,
  changeFounderTeamMemberRole,
  fetchFounderTeam,
  removeFounderTeamMember,
  type FounderPlanEntitlement,
  type FounderTeamMember,
  type FounderTeamOverview,
  type FounderTeamRole,
} from '@/lib/api';
import {
  canChangeFounderTeamRoles,
  canManageFounderTeam,
  canRemoveFounderTeamMember,
} from '@/components/account/founder-plan-account-state';

type Props = {
  token: string;
  entitlement: FounderPlanEntitlement;
};

type AssignableRole = Exclude<FounderTeamRole, 'OWNER'>;

function memberName(member: FounderTeamMember): string {
  return member.user.name?.trim()
    || member.user.platformHandle?.trim()
    || member.user.email;
}

function roleLabel(role: FounderTeamRole): string {
  if (role === 'OWNER') return 'Owner';
  if (role === 'ADMIN') return 'Admin';
  return 'Member';
}

export function FounderTeamMembers({ token, entitlement }: Props) {
  const [team, setTeam] = useState<FounderTeamOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<AssignableRole>('MEMBER');
  const canManage = canManageFounderTeam(entitlement.teamRole);
  const canChangeRoles = canChangeFounderTeamRoles(entitlement.teamRole);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setTeam(await fetchFounderTeam(token));
    } catch (reason) {
      setTeam(null);
      setLoadError(reason instanceof Error ? reason.message : 'Team members could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  async function addMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedEmail = email.trim().toLowerCase();
    if (!normalizedEmail) {
      setActionError('Enter the email of an existing Founder account.');
      return;
    }
    const nextRole: AssignableRole = entitlement.teamRole === 'owner' ? role : 'MEMBER';
    setBusy('add');
    setMessage(null);
    setActionError(null);
    try {
      await addFounderTeamMember(token, { email: normalizedEmail, role: nextRole });
      setEmail('');
      setRole('MEMBER');
      setMessage(`${normalizedEmail} added as ${roleLabel(nextRole)}.`);
      await load();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : 'Team member could not be added.');
    } finally {
      setBusy(null);
    }
  }

  async function changeRole(member: FounderTeamMember, nextRole: AssignableRole) {
    setBusy(member.id);
    setMessage(null);
    setActionError(null);
    try {
      await changeFounderTeamMemberRole(token, member.id, nextRole);
      setMessage(`${memberName(member)} is now ${roleLabel(nextRole)}.`);
      await load();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : 'The team role could not be changed.');
    } finally {
      setBusy(null);
    }
  }

  async function removeMember(member: FounderTeamMember) {
    if (!window.confirm(`Remove ${memberName(member)} from ${team?.name ?? 'this Team'}?`)) return;
    setBusy(member.id);
    setMessage(null);
    setActionError(null);
    try {
      await removeFounderTeamMember(token, member.id);
      setMessage(`${memberName(member)} removed from the Team.`);
      await load();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : 'The team member could not be removed.');
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="border-b border-zinc-800 pb-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-500/10 text-blue-300">
            <Users className="h-4 w-4" aria-hidden />
          </span>
          <div>
            <h3 className="font-semibold text-white">{team?.name ?? entitlement.teamName ?? 'Founder Team'}</h3>
            <p className="mt-1 text-sm text-zinc-400">
              One shared managed allowance with role-based access and an audit trail.
            </p>
          </div>
        </div>
        {team ? (
          <span className="text-xs text-zinc-500">
            {team.members.length} {team.members.length === 1 ? 'member' : 'members'}
          </span>
        ) : null}
      </div>

      {loading ? (
        <p className="mt-4 text-sm text-zinc-500" aria-live="polite">Loading Team members...</p>
      ) : loadError ? (
        <div className="mt-4 rounded-lg border border-amber-500/30 bg-amber-950/15 p-4">
          <p className="text-sm text-amber-200">{loadError}</p>
          <button
            type="button"
            onClick={() => void load()}
            className="mt-3 inline-flex items-center gap-2 rounded-lg border border-zinc-700 px-3 py-2 text-xs font-semibold text-zinc-200 hover:border-zinc-500"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            Retry
          </button>
        </div>
      ) : team ? (
        <>
          {canManage ? (
            <form onSubmit={addMember} className="mt-5 flex flex-col gap-2 sm:flex-row">
              <label className="min-w-0 flex-1">
                <span className="sr-only">Existing Founder account email</span>
                <input
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="Existing Founder account email"
                  disabled={busy !== null}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-white placeholder:text-zinc-600"
                />
              </label>
              {canChangeRoles ? (
                <label>
                  <span className="sr-only">Team role</span>
                  <select
                    value={role}
                    onChange={(event) => setRole(event.target.value as AssignableRole)}
                    disabled={busy !== null}
                    className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-white sm:w-28"
                  >
                    <option value="MEMBER">Member</option>
                    <option value="ADMIN">Admin</option>
                  </select>
                </label>
              ) : null}
              <button
                type="submit"
                disabled={busy !== null || !email.trim()}
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-500"
              >
                <UserPlus className="h-4 w-4" aria-hidden />
                {busy === 'add' ? 'Adding...' : 'Add member'}
              </button>
            </form>
          ) : (
            <p className="mt-4 text-xs text-zinc-500">Team membership is managed by the owner or an admin.</p>
          )}

          <div className="mt-4 divide-y divide-zinc-800 border-y border-zinc-800">
            {team.members.map((member) => {
              const removable = canRemoveFounderTeamMember(entitlement.teamRole, member.role);
              return (
                <div key={member.id} className="flex flex-wrap items-center gap-3 py-3">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-zinc-100">{memberName(member)}</p>
                    <p className="truncate text-xs text-zinc-500">{member.user.email}</p>
                  </div>
                  {canChangeRoles && member.role !== 'OWNER' ? (
                    <label>
                      <span className="sr-only">Role for {memberName(member)}</span>
                      <select
                        value={member.role}
                        onChange={(event) => void changeRole(member, event.target.value as AssignableRole)}
                        disabled={busy !== null}
                        className="rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-xs text-zinc-200"
                      >
                        <option value="MEMBER">Member</option>
                        <option value="ADMIN">Admin</option>
                      </select>
                    </label>
                  ) : (
                    <span className="rounded-full border border-zinc-700 px-2.5 py-1 text-xs text-zinc-400">
                      {roleLabel(member.role)}
                    </span>
                  )}
                  {removable ? (
                    <button
                      type="button"
                      onClick={() => void removeMember(member)}
                      disabled={busy !== null}
                      title={`Remove ${memberName(member)}`}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-zinc-500 hover:bg-red-950/30 hover:text-red-300 disabled:opacity-40"
                    >
                      <Trash2 className="h-4 w-4" aria-hidden />
                      <span className="sr-only">Remove {memberName(member)}</span>
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
        </>
      ) : null}

      {message ? <p className="mt-3 text-sm text-emerald-300">{message}</p> : null}
      {actionError ? <p className="mt-3 text-sm text-red-300">{actionError}</p> : null}
    </section>
  );
}
