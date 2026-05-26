import { Injectable } from '@nestjs/common';
import { AnalyticsEventType, Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class AnalyticsService {
  constructor(private readonly prisma: PrismaService) {}

  track(
    type: AnalyticsEventType,
    options?: {
      userId?: string;
      projectId?: string;
      metadata?: Prisma.InputJsonValue;
    },
  ) {
    return this.prisma.analyticsEvent.create({
      data: {
        type,
        userId: options?.userId,
        projectId: options?.projectId,
        metadata: options?.metadata,
      },
    });
  }
}
