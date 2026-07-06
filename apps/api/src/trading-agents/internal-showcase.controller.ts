import { Body, Controller, Headers, Post } from '@nestjs/common';
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
