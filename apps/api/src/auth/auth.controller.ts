import { Body, Controller, Get, Post, UseGuards } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { Verify2FaLoginDto } from '../security/dto/security.dto';
import { SecurityService } from '../security/security.service';
import { AuthService } from './auth.service';
import { AuthUser } from './auth.types';
import { CurrentUser } from './current-user.decorator';
import { LoginDto, RegisterDto } from './dto/auth.dto';
import { OAuthLoginDto } from './dto/oauth.dto';
import { Public } from './public.decorator';

@SkipThrottle()
@Controller('auth')
export class AuthController {
  constructor(
    private readonly authService: AuthService,
    private readonly securityService: SecurityService,
  ) {}

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
