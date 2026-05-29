import { Body, Controller, Get, Param, Post } from '@nestjs/common';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { ConvictionShareService } from './conviction-share.service';
import { PostConvictionShareDto } from './dto/conviction-share.dto';

@Controller('conviction-share')
export class ConvictionShareController {
  constructor(private readonly convictionShare: ConvictionShareService) {}

  @Get('x-status')
  xStatus(@CurrentUser() user: AuthUser) {
    return this.convictionShare.getXConnectionStatus(user.id);
  }

  @Get('positions/:projectId')
  positionConviction(@CurrentUser() user: AuthUser, @Param('projectId') projectId: string) {
    return this.convictionShare.getPositionConviction(user.id, projectId);
  }

  @Post('post-to-x')
  postToX(@CurrentUser() user: AuthUser, @Body() dto: PostConvictionShareDto) {
    return this.convictionShare.postProofOfConviction(user.id, dto);
  }
}
