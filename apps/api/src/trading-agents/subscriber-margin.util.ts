import { resolveSubscriberMaxMarginUsd } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';

export async function loadSubscriberMaxMarginUsd(prisma: PrismaService): Promise<number> {
  const row = await prisma.platformSettings.findUnique({ where: { id: 'default' } });
  return resolveSubscriberMaxMarginUsd({
    platformValue: row?.subscriberMaxMarginUsd ?? null,
    envValue: process.env.SUBSCRIBER_MAX_MARGIN_USD,
  });
}
