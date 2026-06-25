import { Body, Controller, Headers, Post } from '@nestjs/common';
import { Public } from '../auth/public.decorator';
import { ShowcaseSnapshotService, type ShowcaseSnapshotBody } from './showcase-snapshot.service';

@Controller('internal')
export class InternalShowcaseController {
  constructor(private readonly snapshots: ShowcaseSnapshotService) {}

  @Public()
  @Post('showcase-snapshot')
  pushSnapshot(
    @Headers('x-bot-control-secret') secret: string | undefined,
    @Body() body: ShowcaseSnapshotBody,
  ) {
    this.snapshots.assertAuthorized(secret);
    return this.snapshots.ingest(body);
  }
}
