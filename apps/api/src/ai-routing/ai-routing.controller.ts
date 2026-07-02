import { Body, Controller, Delete, Get, Param, Post, Put, UseGuards } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { AdminGuard } from '../auth/guards';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { Public } from '../auth/public.decorator';
import { AiInvokerService } from './ai-invoker.service';
import { AiRoutingService } from './ai-routing.service';

@SkipThrottle()
@Controller('ai-routing')
export class AiRoutingController {
  constructor(
    private readonly routing: AiRoutingService,
    private readonly invoker: AiInvokerService,
  ) {}

  // ─── Public-ish: which provider is routed for a section ────────────────────
  /** Returns the routed provider label for a section (used by the X Share modal). */
  @Public()
  @Get('section/:section')
  async getSectionRouting(@Param('section') section: string) {
    const row = await this.routing.getSectionRouting(section);
    if (!row) return { section, providerKey: null, providerLabel: null, ready: false };
    return {
      section: row.section,
      providerKey: row.providerKey,
      providerLabel: row.providerLabel,
      ready: row.providerEnabled && row.providerHasKey,
    };
  }

  // ─── Admin: providers ──────────────────────────────────────────────────────

  @UseGuards(AdminGuard)
  @Get('providers')
  listProviders() {
    return this.routing.listProviders();
  }

  @UseGuards(AdminGuard)
  @Post('providers')
  upsertProvider(
    @CurrentUser() user: AuthUser,
    @Body()
    body: {
      key: string;
      label?: string;
      baseUrl?: string;
      defaultModel?: string;
      adapter?: string;
      apiKey?: string | null;
      enabled?: boolean;
    },
  ) {
    return this.routing.upsertProvider(user.id, body);
  }

  @UseGuards(AdminGuard)
  @Delete('providers/:key')
  removeProvider(@CurrentUser() user: AuthUser, @Param('key') key: string) {
    return this.routing.removeProvider(user.id, key);
  }

  // ─── Admin: section routing ────────────────────────────────────────────────

  @UseGuards(AdminGuard)
  @Get('sections')
  listSections() {
    return this.routing.listSections();
  }

  @UseGuards(AdminGuard)
  @Put('sections/:section')
  setSectionProvider(
    @CurrentUser() user: AuthUser,
    @Param('section') section: string,
    @Body() body: { providerKey: string },
  ) {
    return this.routing.setSectionProvider(user.id, section, body.providerKey);
  }
}
