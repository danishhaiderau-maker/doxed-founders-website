import {
  BadRequestException,
  Body,
  Controller,
  Delete,
  Get,
  Param,
  Post,
  Req,
  UseGuards,
} from '@nestjs/common';
import type { DeviceMemoryPayload } from '@dcf/utils';
import type { FounderNodeHeartbeat } from '@dcf/founder-vault';
import { CurrentUser } from '../auth/current-user.decorator';
import { Public } from '../auth/public.decorator';
import { AuthUser } from '../auth/auth.types';
import { FounderNodeGuard, type FounderNodeRequestUser } from './founder-node.guard';
import { FounderNodeService } from './founder-node.service';

@Controller('founder-node')
export class FounderNodeController {
  constructor(private readonly nodes: FounderNodeService) {}

  @Post('pairing-code')
  createPairingCode(@CurrentUser() user: AuthUser) {
    return this.nodes.createPairingCode(user.id);
  }

  @Get('status')
  status(@CurrentUser() user: AuthUser) {
    return this.nodes.getStatus(user.id);
  }

  @Delete(':nodeId')
  revoke(@CurrentUser() user: AuthUser, @Param('nodeId') nodeId: string) {
    return this.nodes.revokeNode(user.id, nodeId);
  }

  @Public()
  @Post('pair')
  pair(
    @Body()
    body: {
      code: string;
      nodeId: string;
      label: string;
      platform?: string;
      appVersion?: string;
    },
  ) {
    if (!body.code?.trim() || !body.nodeId?.trim()) {
      throw new BadRequestException('code and nodeId required');
    }
    return this.nodes.pair(body);
  }

  @UseGuards(FounderNodeGuard)
  @Post('heartbeat')
  heartbeat(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Body() body: FounderNodeHeartbeat,
  ) {
    return this.nodes.heartbeat(req.founderNode.nodeDbId, body);
  }

  @UseGuards(FounderNodeGuard)
  @Post('sync')
  sync(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Body() body: DeviceMemoryPayload,
  ) {
    return this.nodes.syncFromNode(req.founderNode.userId, req.founderNode.nodeDbId, body);
  }
}
