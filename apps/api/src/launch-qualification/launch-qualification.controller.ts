import { Controller, Get, Param } from '@nestjs/common';
import { Public } from '../auth/public.decorator';
import { LaunchQualificationService } from './launch-qualification.service';

@Controller('projects')
export class LaunchQualificationController {
  constructor(private readonly launchQualification: LaunchQualificationService) {}

  @Public()
  @Get(':slug/launch-qualification')
  getScore(@Param('slug') slug: string) {
    return this.launchQualification.getBySlug(slug);
  }

  @Public()
  @Get(':slug/token-metadata-preview')
  metadataPreview(@Param('slug') slug: string) {
    return this.launchQualification.getMetadataPreviewGate(slug);
  }
}
