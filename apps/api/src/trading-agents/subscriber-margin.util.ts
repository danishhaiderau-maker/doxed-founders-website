import { resolveSubscriberMaxMarginUsd } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import {
  PLATFORM_SETTINGS_ID,
  cachedPlatformSettings,
} from '../prisma/platform-settings-cache';

export async function loadSubscriberMaxMarginUsd(prisma: PrismaService): Promise<number> {
  const platformValue = await cachedPlatformSettings('subscriberMaxMarginUsd', async () => {
    const row = await prisma.platformSettings.findUnique({
      where: { id: PLATFORM_SETTINGS_ID },
      select: { subscriberMaxMarginUsd: true },
    });
    return row?.subscriberMaxMarginUsd ?? null;
  });
  return resolveSubscriberMaxMarginUsd({
    platformValue,
    envValue: process.env.SUBSCRIBER_MAX_MARGIN_USD,
  });
}
