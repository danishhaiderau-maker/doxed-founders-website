import { Body, Controller, Get, Post, Query, Res, UseGuards } from '@nestjs/common';
import type { Response } from 'express';
import { SkipThrottle } from '@nestjs/throttler';
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

@SkipThrottle()
@Controller('auth')
export class AuthController {
  constructor(
    private readonly authService: AuthService,
    private readonly securityService: SecurityService,
    private readonly githubOAuth: GitHubOAuthService,
  ) {}

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
  @Post('register')
  register(@Body() dto: RegisterDto) {
    return this.authService.register(dto);
  }

  @Public()
  @Post('login')
  login(@Body() dto: LoginDto) {
    return this.authService.login(dto);
  }

  @Public()
  @Post('oauth')
  oauth(@Body() dto: OAuthLoginDto) {
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
}
