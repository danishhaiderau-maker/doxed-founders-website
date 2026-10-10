import { UnauthorizedException } from '@nestjs/common';
import { timingSafeEqual } from 'node:crypto';

/**
 * Read-only ops auth: BOT_ADMIN_TOKEN, or the separate read-only
 * MONITOR_READ_TOKEN (Health Monitor) when configured. Only used on GET
 * routes that never place, cancel, arm or switch anything.
 */
export function assertBotReadToken(adminHeader?: string, authorization?: string): void {
  const monitor = (process.env.MONITOR_READ_TOKEN ?? '').trim();
  const admin = (process.env.BOT_ADMIN_TOKEN ?? '').trim();
  const bearer = typeof authorization === 'string' && authorization.toLowerCase().startsWith('bearer ')
    ? authorization.slice(7).trim()
    : '';
  const a = Buffer.from((adminHeader?.trim() || bearer).trim(), 'utf8');
  const eq = (exp: string) => {
    if (!exp) return false;
    const b = Buffer.from(exp, 'utf8');
    return a.length > 0 && a.length === b.length && timingSafeEqual(a, b);
  };
  if (eq(admin) || (monitor && monitor !== admin && eq(monitor))) return;
  if (!admin && !monitor) throw new UnauthorizedException('BOT_ADMIN_TOKEN is not configured');
  throw new UnauthorizedException('Invalid BOT_ADMIN_TOKEN or MONITOR_READ_TOKEN');
}

