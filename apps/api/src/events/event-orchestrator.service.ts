import { Injectable, Logger } from '@nestjs/common';
import {
  FounderEvent,
  FounderEventStatus,
  FounderEventType,
  NotificationType,
  Prisma,
  SuggestedUpdateStatus,
} from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { NotificationsService } from '../notifications/notifications.service';
import { FounderMetricsService } from './founder-metrics.service';

@Injectable()
export class EventOrchestratorService {
  private readonly logger = new Logger(EventOrchestratorService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly notifications: NotificationsService,
    private readonly metrics: FounderMetricsService,
  ) {}

  async process(event: FounderEvent) {
    try {
      switch (event.type) {
        case FounderEventType.GITHUB_COMMIT:
        case FounderEventType.DEPLOY_SUCCESS:
          await this.onBuildSignal(event);
          break;
        case FounderEventType.BUILD_PUBLISHED:
          await this.onBuildPublished(event);
          break;
        case FounderEventType.BUILD_QUEUE_CAPTURED:
        case FounderEventType.AGENT_RUN_COMPLETE:
        case FounderEventType.GITHUB_ISSUE_CREATED:
          await this.onActivity(event);
          break;
        case FounderEventType.COPILOT_COMMAND:
        case FounderEventType.QUICK_COMMAND:
          await this.onActivity(event);
          break;
        default:
          break;
      }

      await this.prisma.founderEvent.update({
        where: { id: event.id },
        data: { status: FounderEventStatus.PROCESSED, processedAt: new Date() },
      });
    } catch (err) {
      this.logger.warn(`Event ${event.id} failed: ${err instanceof Error ? err.message : err}`);
      await this.prisma.founderEvent.update({
        where: { id: event.id },
        data: { status: FounderEventStatus.FAILED, processedAt: new Date() },
      });
    }
  }

  private async onBuildSignal(event: FounderEvent) {
    const payload = event.payload as { suggestionId?: string; autoPublish?: boolean };
    if (event.userId) {
      await this.notifications.notifyUser(event.userId, {
        type: NotificationType.FOUNDER_EVENT,
        title: event.title,
        body: 'Review the suggested update in Founder Copilot — publish everywhere when ready.',
        link: '/founder-den?tab=build',
      });
    }

    if (payload.autoPublish && payload.suggestionId && event.userId) {
      await this.tryAutoPublish(event.userId, payload.suggestionId);
    }

    if (event.projectId) {
      await this.metrics.refreshLaunchReadiness(event.projectId);
    }
  }

  private async onBuildPublished(event: FounderEvent) {
    if (!event.projectId) return;

    const { score, previous } = await this.metrics.refreshLaunchReadiness(event.projectId);
    await this.metrics.refreshBubbleScore(event.projectId);

    const delta = score - previous;
    if (event.userId && delta !== 0) {
      await this.notifications.notifyUser(event.userId, {
        type: NotificationType.FOUNDER_EVENT,
        title: 'Launch readiness updated',
        body: `Now at ${score}% (${delta > 0 ? '+' : ''}${delta}% from this publish).`,
        link: '/founder-den?tab=funding',
      });
    }
  }

  private async onActivity(event: FounderEvent) {
    if (!event.userId) return;
    const payload = event.payload as { silent?: boolean };
    if (payload.silent) return;

    await this.notifications.notifyUser(event.userId, {
      type: NotificationType.FOUNDER_EVENT,
      title: event.title,
      body: `Via ${event.source} — coordinated by Founder OS event bus.`,
      link: '/founder-den?tab=build',
    });
  }

  private async tryAutoPublish(userId: string, suggestionId: string) {
    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    if (!settings?.autoPublishOnEvent) return;

    const suggestion = await this.prisma.suggestedBuildUpdate.findFirst({
      where: { id: suggestionId, status: SuggestedUpdateStatus.PENDING },
    });
    if (!suggestion) return;

    await this.prisma.suggestedBuildUpdate.update({
      where: { id: suggestionId },
      data: {
        status: SuggestedUpdateStatus.PUBLISHED,
        publishLog: { auto: true, at: new Date().toISOString() } as Prisma.InputJsonValue,
      },
    });
  }
}
