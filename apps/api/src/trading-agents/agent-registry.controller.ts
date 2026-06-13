import { Body, Controller, Get, Param, Post, UseGuards } from '@nestjs/common';
import { AgentRegistryKind, AgentRegistryStatus, ChainSlug } from '@prisma/client';
import { AdminGuard } from '../auth/guards';
import { AgentRegistryService } from './agent-registry.service';

@Controller('admin-control/agent-registry')
export class AgentRegistryController {
  constructor(private readonly registry: AgentRegistryService) {}

  @UseGuards(AdminGuard)
  @Get(':slug')
  overview(@Param('slug') slug: string) {
    return this.registry.getRegistrationOverview(slug);
  }

  @UseGuards(AdminGuard)
  @Post(':slug/record')
  record(
    @Param('slug') slug: string,
    @Body()
    body: {
      registry: AgentRegistryKind;
      status?: AgentRegistryStatus;
      chainSlug?: ChainSlug;
      externalId?: string;
      ownerAddress?: string;
      metadataUri?: string;
      registryUrl?: string;
      txSignature?: string;
      notes?: string;
      verified?: boolean;
    },
  ) {
    return this.registry.upsertRegistration(slug, body);
  }
}
