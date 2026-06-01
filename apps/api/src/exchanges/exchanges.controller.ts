import { Body, Controller, Delete, Get, Param, Post } from '@nestjs/common';
import { AuthUser } from '../auth/auth.types';
import { CurrentUser } from '../auth/current-user.decorator';
import { Public } from '../auth/public.decorator';
import { ExchangesService } from './exchanges.service';

@Controller('exchanges')
export class ExchangesController {
  constructor(private readonly exchanges: ExchangesService) {}

  @Public()
  @Get('providers')
  listProviders() {
    return this.exchanges.listProviders();
  }

  @Get(':provider/status')
  status(@CurrentUser() user: AuthUser, @Param('provider') provider: string) {
    return this.exchanges.getUserExchangeStatus(user.id, provider);
  }

  @Post(':provider/connect')
  connect(
    @CurrentUser() user: AuthUser,
    @Param('provider') provider: string,
    @Body()
    body: { apiKey: string; apiSecret: string; passphrase?: string; testnet?: boolean },
  ) {
    return this.exchanges.connectUserExchange(user.id, provider, body);
  }

  @Delete(':provider/connect')
  disconnect(@CurrentUser() user: AuthUser, @Param('provider') provider: string) {
    return this.exchanges.disconnectUserExchange(user.id, provider);
  }
}
