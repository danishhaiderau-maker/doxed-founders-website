import { BadRequestException, Injectable } from '@nestjs/common';
import {
  generatePlatformHandle,
  isReservedPlatformHandle,
  normalizeTwitterHandle,
  userHasTwitterConnected,
  validatePlatformHandleInput,
} from '@dcf/utils';
import { UserRole } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { ADMIN_PLATFORM_HANDLE } from './referral.service';

@Injectable()
export class PlatformHandleService {
  constructor(private readonly prisma: PrismaService) {}

  async ensureHandle(userId: string): Promise<string> {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: {
        platformHandle: true,
        role: true,
        twitterHandle: true,
        oauthAccounts: { select: { provider: true } },
      },
    });
    if (!user) throw new BadRequestException('User not found');

    const hasTwitter = userHasTwitterConnected(user);
    const twitter = normalizeTwitterHandle(user.twitterHandle);
    if (hasTwitter && twitter) {
      return `@${twitter}`;
    }

    if (user.role === UserRole.ADMIN) {
      if (user.platformHandle !== ADMIN_PLATFORM_HANDLE) {
        await this.prisma.user.update({
          where: { id: userId },
          data: { platformHandle: ADMIN_PLATFORM_HANDLE },
        });
      }
      return ADMIN_PLATFORM_HANDLE;
    }

    if (user.platformHandle) return user.platformHandle;

    for (let attempt = 0; attempt < 40; attempt++) {
      const candidate = generatePlatformHandle(userId, attempt);
      if (isReservedPlatformHandle(candidate)) continue;
      const taken = await this.prisma.user.findUnique({
        where: { platformHandle: candidate },
      });
      if (taken) continue;
      const updated = await this.prisma.user.update({
        where: { id: userId },
        data: { platformHandle: candidate },
        select: { platformHandle: true },
      });
      return updated.platformHandle!;
    }

    const fallback = generatePlatformHandle(`${userId}-${Date.now()}`, 99);
    const updated = await this.prisma.user.update({
      where: { id: userId },
      data: { platformHandle: fallback },
      select: { platformHandle: true },
    });
    return updated.platformHandle!;
  }

  async updateHandle(userId: string, handle: string) {
    const validation = validatePlatformHandleInput(handle);
    if (!validation.ok) throw new BadRequestException(validation.error);

    const trimmed = handle.trim();
    const existing = await this.prisma.user.findFirst({
      where: { platformHandle: trimmed, NOT: { id: userId } },
    });
    if (existing) {
      throw new BadRequestException('That handle is already taken — try adding a number or different country');
    }

    return this.prisma.user.update({
      where: { id: userId },
      data: { platformHandle: trimmed },
      select: { platformHandle: true },
    });
  }
}
