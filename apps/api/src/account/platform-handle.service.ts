import { BadRequestException, Injectable } from '@nestjs/common';
import {
  generatePlatformHandle,
  isReservedPlatformHandle,
  validatePlatformHandleInput,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class PlatformHandleService {
  constructor(private readonly prisma: PrismaService) {}

  async ensureHandle(userId: string): Promise<string> {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: { platformHandle: true },
    });
    if (user?.platformHandle) return user.platformHandle;

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
