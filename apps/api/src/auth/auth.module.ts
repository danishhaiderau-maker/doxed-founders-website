import { Module, forwardRef } from '@nestjs/common';
import { JwtModule } from '@nestjs/jwt';
import { PassportModule } from '@nestjs/passport';
import { AccountModule } from '../account/account.module';
import { GitHubModule } from '../github/github.module';
import { SecurityModule } from '../security/security.module';
import { AuthController } from './auth.controller';
import { AuthService } from './auth.service';
import { AdminGuard, JwtAuthGuard } from './guards';
import { OptionalJwtAuthGuard } from './optional-jwt.guard';
import { JwtStrategy } from './jwt.strategy';

@Module({
  imports: [
    forwardRef(() => SecurityModule),
    AccountModule,
    GitHubModule,
    PassportModule.register({ defaultStrategy: 'jwt' }),
    JwtModule.register({
      secret: process.env.JWT_SECRET ?? 'dev-secret-change-in-production',
      signOptions: {
        // Shorter access-token TTL (was 7d) limits the window for a stolen token to be
        // replayed. validatePayload still does a per-request DB lookup + banned check, so
        // banning a user revokes all their tokens immediately. Override via JWT_EXPIRES_IN.
        expiresIn: (process.env.JWT_EXPIRES_IN ?? '24h') as import('ms').StringValue,
      },
    }),
  ],
  controllers: [AuthController],
  providers: [AuthService, JwtStrategy, AdminGuard, JwtAuthGuard, OptionalJwtAuthGuard],
  exports: [AuthService, JwtModule, AdminGuard, JwtAuthGuard, OptionalJwtAuthGuard],
})
export class AuthModule {}
