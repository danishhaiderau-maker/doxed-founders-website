import { Body, Controller, Get, Param, Patch, Post, UseGuards, BadRequestException } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { AdminGuard } from '../auth/guards';
import { OptionalJwtAuthGuard } from '../auth/optional-jwt.guard';
import { Public } from '../auth/public.decorator';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { DexscreenerService } from '../dexscreener/dexscreener.service';
import {
  CreateListingApplicationDto,
  PreviewContractDto,
  PreviewDexScreenerDto,
  ReviewListingApplicationDto,
} from './dto/listing-application.dto';
import { CastListingVoteDto } from './dto/listing-vote.dto';
import { ListingApplicationsService } from './listing-applications.service';
import { ListingVotesService } from './listing-votes.service';

@SkipThrottle()
@Controller('listing-applications')
export class ListingApplicationsController {
  constructor(
    private readonly listingService: ListingApplicationsService,
    private readonly votesService: ListingVotesService,
    private readonly dexscreenerService: DexscreenerService,
  ) {}

  @Public()
  @Post('preview-dexscreener')
  previewDexScreener(@Body() dto: PreviewDexScreenerDto) {
    return this.dexscreenerService.previewFromInput(dto.url);
  }

  @Public()
  @Post('preview-contract')
  previewContract(@Body() dto: PreviewContractDto) {
    return this.dexscreenerService.previewFromContract(
      dto.chainSlug,
      dto.contractAddress,
    );
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Post()
  create(@Body() dto: CreateListingApplicationDto, @CurrentUser() user?: AuthUser | null) {
    return this.listingService.create(dto, user?.id);
  }

  @Public()
  @Get('voting/stats')
  votingStats() {
    return this.votesService.getVotingStats();
  }

  @Public()
  @Get('voting/open')
  openForVoting() {
    return this.votesService.findOpenForVoting();
  }

  @Public()
  @Get('voting/:id')
  votingDetail(@Param('id') id: string) {
    return this.votesService.findOneForVoting(id);
  }

  @Post('voting/:id/vote')
  castVote(
    @Param('id') id: string,
    @CurrentUser() user: AuthUser,
    @Body() dto: CastListingVoteDto,
  ) {
    return this.votesService.castVote(id, user.id, dto);
  }

  @UseGuards(AdminGuard)
  @Post('voting/expire')
  expireVoting() {
    return this.votesService.expireClosedVoting();
  }

  @UseGuards(AdminGuard)
  @Get('pending')
  findPending() {
    return this.listingService.findPending();
  }

  @UseGuards(AdminGuard)
  @Patch(':id/review')
  review(@Param('id') id: string, @Body() dto: ReviewListingApplicationDto) {
    return this.listingService.review(id, dto);
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Patch(':id/scout')
  updateScout(
    @Param('id') id: string,
    @CurrentUser() user: AuthUser,
    @Body()
    body: {
      scoutHighlightNote?: string;
      whyList?: string;
      whyDoxxed?: string;
      founderDoxxedStatus?: 'DOXXED' | 'BUILDING_IN_PUBLIC';
    },
  ) {
    if (!user?.id) throw new BadRequestException('Sign in to edit your listing');
    return this.listingService.updateScoutFields(id, user.id, body);
  }

  @Public()
  @Get(':id')
  findOne(@Param('id') id: string) {
    return this.listingService.findById(id);
  }
}
