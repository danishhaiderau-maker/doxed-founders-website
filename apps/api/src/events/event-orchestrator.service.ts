import { Injectable, Logger } from '@nestjs/common';
import {
  FounderEvent,
  FounderEventStatus,
  FounderEventType,
  Prisma,
  SuggestedUpdateStatus,
} from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { FounderMetricsService } from './founder-metrics.service';

@Injectable()
export class EventOrchestratorService {
  private readonly logger = new Logger(EventOrchestratorService.name);

  constructor(
    private readonly prisma: PrismaService,
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
        case FounderEventType.RAISE_ALLOCATION:
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

  /**
   * Build/dev events (commits, deploys, build publishes) are founder-internal telemetry —
   * they were previously surfaced as user-facing FOUNDER_EVENT notifications ("Day 34 — 8
   * commits pushed", "Launch readiness updated", etc.) which spammed the Alerts feed.
   * They are now cut at source: no notifyUser. The metrics refresh + auto-publish side
   * effects remain so the founder-den dashboard still updates.
   */
  private async onBuildSignal(event: FounderEvent) {
    const payload = event.payload as { suggestionId?: string; autoPublish?: boolean };

    if (payload.autoPublish && payload.suggestionId && event.userId) {
      await this.tryAutoPublish(event.userId, payload.suggestionId);
    }

    if (event.projectId) {
      await this.metrics.refreshLaunchReadiness(event.projectId);
    }
  }

  private async onBuildPublished(event: FounderEvent) {
    if (!event.projectId) return;

    await this.metrics.refreshLaunchReadiness(event.projectId);
    await this.metrics.refreshBubbleScore(event.projectId);
  }

  /**
   * Agent-activity events (BUILD_QUEUE_CAPTURED, AGENT_RUN_COMPLETE, GITHUB_ISSUE_CREATED,
   * COPILOT_COMMAND, QUICK_COMMAND, RAISE_ALLOCATION) were previously surfaced as
   * FOUNDER_EVENT notifications with body "Via <source> — coordinated by Founder OS event
   * bus." — pure agent-activity spam. Cut at source: no notifyUser.
   */
  private async onActivity(_event: FounderEvent) {
    // Intentionally no-op for the Alerts feed. Event is still marked PROCESSED below so
    // the orchestrator queue drains.
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
