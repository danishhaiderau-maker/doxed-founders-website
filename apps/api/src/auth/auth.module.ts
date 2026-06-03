import { Module, forwardRef } from '@nestjs/common';
import { JwtModule } from '@nestjs/jwt';
import { PassportModule } from '@nestjs/passport';
import { GitHubModule } from '../github/github.module';
import { SecurityModule } from '../security/security.module';
import { resolveJwtSecret } from '../security/jwt-secret.util';
import { AuthController } from './auth.controller';
import { AuthService } from './auth.service';
import { AdminGuard, JwtAuthGuard } from './guards';
import { OptionalJwtAuthGuard } from './optional-jwt.guard';
import { JwtStrategy } from './jwt.strategy';

@Module({
  imports: [
    forwardRef(() => SecurityModule),
    GitHubModule,
    PassportModule.register({ defaultStrategy: 'jwt' }),
    JwtModule.register({
      secret: resolveJwtSecret(),
      signOptions: {
        expiresIn: (process.env.JWT_EXPIRES_IN ?? '7d') as `${number}d`,
      },
    }),
  ],
  controllers: [AuthController],
  providers: [AuthService, JwtStrategy, AdminGuard, JwtAuthGuard, OptionalJwtAuthGuard],
  exports: [AuthService, JwtModule, AdminGuard, JwtAuthGuard, OptionalJwtAuthGuard],
})
export class AuthModule {}
