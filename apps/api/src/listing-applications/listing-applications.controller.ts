import { Body, Controller, Get, Param, Patch, Post, UseGuards } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { AdminGuard } from '../auth/guards';
import { Public } from '../auth/public.decorator';
import { DexscreenerService } from '../dexscreener/dexscreener.service';
import {
  CreateListingApplicationDto,
  PreviewDexScreenerDto,
  ReviewListingApplicationDto,
} from './dto/listing-application.dto';
import { ListingApplicationsService } from './listing-applications.service';

@SkipThrottle()
@Controller('listing-applications')
export class ListingApplicationsController {
  constructor(
    private readonly listingService: ListingApplicationsService,
    private readonly dexscreenerService: DexscreenerService,
  ) {}

  @Public()
  @Post('preview-dexscreener')
  previewDexScreener(@Body() dto: PreviewDexScreenerDto) {
    return this.dexscreenerService.previewFromUrl(dto.url);
  }

  @Public()
  @Post()
  create(@Body() dto: CreateListingApplicationDto) {
    return this.listingService.create(dto);
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
  @Get(':id')
  findOne(@Param('id') id: string) {
    return this.listingService.findById(id);
  }
}
