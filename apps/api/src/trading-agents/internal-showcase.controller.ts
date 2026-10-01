import { Body, Controller, Get, Headers, Post, Query } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { Public } from '../auth/public.decorator';
import {
  ShowcaseInferenceUsageService,
  type ShowcaseInferenceUsageEntry,
} from './showcase-inference-usage.service';
import { ShowcaseSnapshotService, type ShowcaseSnapshotBody } from './showcase-snapshot.service';

@Controller('internal')
export class InternalShowcaseController {
  constructor(
    private readonly snapshots: ShowcaseSnapshotService,
    private readonly inferenceUsage: ShowcaseInferenceUsageService,
  ) {}

  @Public()
  @Post('showcase-snapshot')
  pushSnapshot(
    @Headers('x-bot-control-secret') secret: string | undefined,
    @Body() body: ShowcaseSnapshotBody,
  ) {
    this.snapshots.assertAuthorized(secret);
    return this.snapshots.ingest(body);
  }

  /**
   * Relay-executor worker reads the latest pushed snapshot from this process
   * over Railway private networking instead of from Postgres.
   */
  @Public()
  @SkipThrottle()
  @Get('showcase-snapshot/latest')
  latestSnapshot(
    @Headers('x-bot-control-secret') secret: string | undefined,
    @Query('since_seq') sinceSeq: string | undefined,
  ) {
    this.snapshots.assertAuthorized(secret);
    const parsed = sinceSeq != null && /^\d+$/.test(sinceSeq) ? Number(sinceSeq) : null;
    return this.snapshots.getLatestForPeer(parsed);
  }

  /**
   * Batched DeepSeek token usage from the home showcase BTC bot. Authenticated
   * via X-Bot-Control-Secret (same as showcase-relay-event). Best-effort: always
   * returns counts so the bot can fire-and-forget after each inference.
   */
  @Public()
  @Post('showcase-inference-usage')
  reportInferenceUsage(
    @Headers('x-bot-control-secret') secret: string | undefined,
    @Body()
    body: {
      entries?: ShowcaseInferenceUsageEntry[];
    },
  ) {
    this.inferenceUsage.assertAuthorized(secret);
    const entries = Array.isArray(body?.entries) ? body.entries : [];
    return this.inferenceUsage.recordBatch(
      entries.map((e) => ({
        promptTokens: Number(e?.promptTokens ?? 0),
        completionTokens: Number(e?.completionTokens ?? 0),
        provider: e?.provider,
        model: e?.model,
        source: e?.source,
        billingSource: e?.billingSource,
      })),
    );
  }
}
