'use client';

import { formatPublicAccountLabel } from '@dcf/utils';

interface AccountWelcomeProps {
  name?: string | null;
  email?: string | null;
  prefix?: string;
  className?: string;
}

export function AccountWelcome({
  name,
  email,
  prefix = 'Welcome',
  className = '',
}: AccountWelcomeProps) {
  const label = formatPublicAccountLabel(name, email);

  return (
    <p className={`text-sm ${className}`}>
      <span className="text-[var(--color-muted)]">{prefix}, </span>
      <span className="font-semibold text-white">{label}</span>
    </p>
  );
}

export function AccountLabel({
  name,
  email,
  className = '',
}: Omit<AccountWelcomeProps, 'prefix'>) {
  return (
    <span className={`font-medium text-white ${className}`}>
      {formatPublicAccountLabel(name, email)}
    </span>
  );
}
