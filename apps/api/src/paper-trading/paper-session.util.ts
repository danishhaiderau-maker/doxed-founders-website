import { ForbiddenException } from '@nestjs/common';
import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import { PrismaService } from '../prisma/prisma.service';

export const PAPER_SESSION_HEADER = 'x-paper-session-token';

export function createPaperSessionToken(): { token: string; hash: string } {
  const token = randomBytes(32).toString('hex');
  return { token, hash: hashPaperSessionToken(token) };
}

export function hashPaperSessionToken(token: string): string {
  return createHash('sha256').update(token).digest('hex');
}

export function readPaperSessionToken(headers: Record<string, unknown>): string | undefined {
  const raw = headers[PAPER_SESSION_HEADER] ?? headers['x-paper-session-token'];
  if (typeof raw === 'string' && raw.trim()) return raw.trim();
  if (Array.isArray(raw) && typeof raw[0] === 'string' && raw[0].trim()) return raw[0].trim();
  return undefined;
}

export async function assertPaperPortfolioAccess(
  prisma: PrismaService,
  userId: string,
  opts: { sessionToken?: string; authUserId?: string },
): Promise<void> {
  if (opts.authUserId && opts.authUserId === userId) return;

  const portfolio = await prisma.paperPortfolio.findUnique({
    where: { userId },
    select: { sessionTokenHash: true },
  });
  if (!portfolio) {
    throw new ForbiddenException('Paper portfolio not found');
  }

  const token = opts.sessionToken?.trim();
  if (!token || !portfolio.sessionTokenHash) {
    throw new ForbiddenException('Paper session token required');
  }

  const expected = Buffer.from(portfolio.sessionTokenHash, 'hex');
  const actual = Buffer.from(hashPaperSessionToken(token), 'hex');
  if (expected.length !== actual.length || !timingSafeEqual(expected, actual)) {
    throw new ForbiddenException('Invalid paper session token');
  }
}

export function redactPaperPortfolioEmail<T extends { accountEmail?: string | null }>(
  portfolio: T,
  isOwner: boolean,
): T {
  if (isOwner) return portfolio;
  return { ...portfolio, accountEmail: null };
}
