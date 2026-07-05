import {
  BadRequestException,
  Body,
  Controller,
  ForbiddenException,
  Get,
  Headers,
  Post,
  Query,
  Res,
  UseGuards,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { Response } from 'express';
import { Throttle, SkipThrottle } from '@nestjs/throttler';
import { GitHubOAuthService } from '../github/github-oauth.service';
import { Verify2FaLoginDto } from '../security/dto/security.dto';
import { SecurityService } from '../security/security.service';
import { AuthService } from './auth.service';
import { AuthUser } from './auth.types';
import { CurrentUser } from './current-user.decorator';
import { LoginDto, RegisterDto } from './dto/auth.dto';
import { OAuthLoginDto } from './dto/oauth.dto';
import { JwtAuthGuard } from './guards';
import { Public } from './public.decorator';

@Controller('auth')
export class AuthController {
  constructor(
    private readonly authService: AuthService,
    private readonly securityService: SecurityService,
    private readonly githubOAuth: GitHubOAuthService,
    private readonly config: ConfigService,
  ) {}

  /**
   * Block public account-takeover via /auth/oauth. The OAuth endpoint must only be
   * callable server-to-server from NextAuth (which verifies the provider token).
   * When INTERNAL_AUTH_SECRET is configured (production), require a matching header.
   * Timing-safe compare avoids leaking the secret via response-time side channels.
   */
  private assertInternalAuthSecret(header: string | undefined) {
    const expected = this.config.get<string>('INTERNAL_AUTH_SECRET')?.trim();
    if (!expected) return; // dev/local: no shared secret configured
    const provided = (header ?? '').trim();
    if (provided.length !== expected.length) {
      throw new ForbiddenException('Internal auth required for OAuth linking');
    }
    let diff = 0;
    for (let i = 0; i < expected.length; i++) {
      diff |= provided.charCodeAt(i) ^ expected.charCodeAt(i);
    }
    if (diff !== 0) {
      throw new ForbiddenException('Internal auth required for OAuth linking');
    }
  }

  @Get('github/status')
  @UseGuards(JwtAuthGuard)
  githubStatus() {
    return { configured: this.githubOAuth.isConfigured() };
  }

  @Get('github/start')
  @UseGuards(JwtAuthGuard)
  githubStart(@CurrentUser() user: AuthUser) {
    return this.githubOAuth.start(user.id);
  }

  @Public()
  @Get('github/callback')
  async githubCallback(
    @Query('code') code: string,
    @Query('state') state: string,
    @Res() res: Response,
  ) {
    try {
      const result = await this.githubOAuth.handleCallback(code, state);
      res.redirect(result.redirectUrl);
    } catch {
      res.redirect(`${this.githubOAuth.webAppUrl()}/founder-den?github=error`);
    }
  }

  @Public()
  @Throttle({ default: { limit: 5, ttl: 60000 } })
  @Post('register')
  register(@Body() dto: RegisterDto) {
    return this.authService.register(dto);
  }

  @Public()
  @Throttle({ default: { limit: 10, ttl: 60000 } })
  @Post('login')
  login(@Body() dto: LoginDto) {
    return this.authService.login(dto);
  }

  @Public()
  @Throttle({ default: { limit: 5, ttl: 60000 } })
  @Post('oauth')
  oauth(
    @Body() dto: OAuthLoginDto,
    @Headers('x-internal-auth-secret') internalSecret: string | undefined,
  ) {
    // Block the public account-takeover path: only NextAuth (server-to-server)
    // may link OAuth identities to existing users.
    this.assertInternalAuthSecret(internalSecret);
    return this.authService.oauthLogin(dto);
  }

  @Public()
  @Post('verify-2fa')
  verify2fa(@Body() dto: Verify2FaLoginDto) {
    return this.securityService.complete2FaLogin(
      dto.pendingToken,
      dto.totpCode,
      dto.recoveryCode,
    );
  }

  @Get('me')
  me(@CurrentUser() user: AuthUser) {
    return this.authService.getProfile(user.id);
  }

  /**
   * Mint a fresh API JWT for an existing NextAuth session. Called server-side
   * from the NextAuth jwt callback when the stored accessToken is expired — the
   * web session can outlive the 24h API token, which otherwise makes Founder OS
   * look offline (all authenticated fetches return 401).
   */
  @Public()
  @Post('session-refresh')
  sessionRefresh(
    @Body() body: { userId?: string },
    @Headers('x-internal-auth-secret') internalSecret: string | undefined,
  ) {
    this.assertInternalAuthSecret(internalSecret);
    const userId = body.userId?.trim();
    if (!userId) throw new BadRequestException('userId required');
    return this.authService.buildAuthResponseForUserId(userId);
  }
}
