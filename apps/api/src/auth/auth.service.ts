import {
  ConflictException,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';
import { UserRole } from '@prisma/client';
import * as bcrypt from 'bcrypt';
import { POINTS } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { PointsService } from '../points/points.service';
import { hashToken, randomToken } from '../security/security-crypto.util';
import { LoginDto, RegisterDto } from './dto/auth.dto';
import { OAuthLoginDto } from './dto/oauth.dto';
import { AuthResponse, AuthUser, JwtPayload } from './auth.types';

const BCRYPT_ROUNDS = 12;

@Injectable()
export class AuthService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly jwt: JwtService,
    private readonly points: PointsService,
  ) {}

  async register(dto: RegisterDto): Promise<AuthResponse> {
    const email = dto.email.trim().toLowerCase();
    const existing = await this.prisma.user.findUnique({ where: { email } });
    if (existing) {
      throw new ConflictException('An account with this email already exists');
    }

    const passwordHash = await bcrypt.hash(dto.password, BCRYPT_ROUNDS);
    const user = await this.prisma.user.create({
      data: {
        email,
        passwordHash,
        name: dto.name?.trim() || null,
        role: UserRole.USER,
        paperPortfolio: {
          create: {
            cashBalance: 10_000,
            totalValue: 10_000,
          },
        },
      },
    });

    await this.points.award(user.id, POINTS.REGISTER);

    return await this.buildAuthResponse(user);
  }

  async login(dto: LoginDto): Promise<AuthResponse> {
    const email = dto.email.trim().toLowerCase();
    const user = await this.prisma.user.findUnique({ where: { email } });
    if (!user?.passwordHash) {
      throw new UnauthorizedException('Invalid email or password');
    }

    if (user.banned) {
      throw new UnauthorizedException('This account has been suspended');
    }

    const valid = await bcrypt.compare(dto.password, user.passwordHash);
    if (!valid) {
      throw new UnauthorizedException('Invalid email or password');
    }

    if (await this.userRequires2Fa(user.id)) {
      const pendingToken = randomToken();
      await this.prisma.authPendingChallenge.create({
        data: {
          userId: user.id,
          tokenHash: hashToken(pendingToken),
          kind: 'LOGIN_2FA',
          expiresAt: new Date(Date.now() + 5 * 60 * 1000),
        },
      });
      const methods: string[] = [];
      const totp = await this.prisma.userTotp.findUnique({ where: { userId: user.id } });
      const passkeyCount = await this.prisma.webAuthnCredential.count({ where: { userId: user.id } });
      if (totp?.enabled) methods.push('totp');
      if (passkeyCount > 0) methods.push('passkey', 'recovery');
      return { requires2fa: true, pendingToken, methods };
    }

    return await this.buildAuthResponse(user);
  }

  private async userRequires2Fa(userId: string): Promise<boolean> {
    const [totp, passkeyCount] = await Promise.all([
      this.prisma.userTotp.findUnique({ where: { userId } }),
      this.prisma.webAuthnCredential.count({ where: { userId } }),
    ]);
    return Boolean(totp?.enabled) || passkeyCount > 0;
  }

  async buildAuthResponseForUserId(userId: string): Promise<AuthResponse> {
    const user = await this.prisma.user.findUnique({ where: { id: userId } });
    if (!user || user.banned) throw new UnauthorizedException('User not found');
    return this.buildAuthResponse(user);
  }

  async oauthLogin(dto: OAuthLoginDto): Promise<AuthResponse> {
    const email = this.resolveOAuthEmail(dto);
    const twitterHandle = dto.twitterHandle?.replace(/^@/, '').trim() || undefined;
    const tokenData =
      dto.oauthAccessToken && dto.oauthAccessTokenSecret
        ? {
            accessToken: dto.oauthAccessToken,
            accessTokenSecret: dto.oauthAccessTokenSecret,
          }
        : null;

    const upsertOAuthTokens = async (oauthAccountId: string) => {
      if (!tokenData) return;
      await this.prisma.oAuthAccount.update({
        where: { id: oauthAccountId },
        data: {
          accessToken: tokenData.accessToken,
          accessTokenSecret: tokenData.accessTokenSecret,
        },
      });
    };

    const linked = await this.prisma.oAuthAccount.findUnique({
      where: {
        provider_providerId: {
          provider: dto.provider,
          providerId: dto.providerId,
        },
      },
      include: { user: true },
    });

    if (linked) {
      if (linked.user.banned) {
        throw new UnauthorizedException('This account has been suspended');
      }
      if (tokenData) {
        await upsertOAuthTokens(linked.id);
      }
      if (twitterHandle && linked.user.twitterHandle !== twitterHandle) {
        const user = await this.prisma.user.update({
          where: { id: linked.user.id },
          data: { twitterHandle },
        });
        return await this.buildAuthResponse(user);
      }
      return await this.buildAuthResponse(linked.user);
    }

    const existingUser = await this.prisma.user.findUnique({ where: { email } });

    if (existingUser) {
      if (existingUser.banned) {
        throw new UnauthorizedException('This account has been suspended');
      }

      await this.prisma.oAuthAccount.create({
        data: {
          userId: existingUser.id,
          provider: dto.provider,
          providerId: dto.providerId,
          accessToken: tokenData?.accessToken,
          accessTokenSecret: tokenData?.accessTokenSecret,
        },
      });

      await this.prisma.paperPortfolio.upsert({
        where: { userId: existingUser.id },
        update: {},
        create: {
          userId: existingUser.id,
          cashBalance: 10_000,
          totalValue: 10_000,
        },
      });

      const updates: {
        name?: string;
        avatarUrl?: string;
        emailVerified?: Date;
        twitterHandle?: string;
      } = {};
      if (dto.name && !existingUser.name) updates.name = dto.name.trim();
      if (dto.avatarUrl && !existingUser.avatarUrl) updates.avatarUrl = dto.avatarUrl;
      if (twitterHandle) updates.twitterHandle = twitterHandle;
      updates.emailVerified = new Date();

      const user = await this.prisma.user.update({
        where: { id: existingUser.id },
        data: updates,
      });

      return await this.buildAuthResponse(user);
    }

    const user = await this.prisma.user.create({
      data: {
        email,
        name: dto.name?.trim() || null,
        avatarUrl: dto.avatarUrl || null,
        twitterHandle: twitterHandle ?? null,
        emailVerified: new Date(),
        role: UserRole.USER,
        oauthAccounts: {
          create: {
            provider: dto.provider,
            providerId: dto.providerId,
            accessToken: tokenData?.accessToken,
            accessTokenSecret: tokenData?.accessTokenSecret,
          },
        },
        paperPortfolio: {
          create: {
            cashBalance: 10_000,
            totalValue: 10_000,
          },
        },
      },
    });

    await this.points.award(user.id, POINTS.REGISTER);

    return await this.buildAuthResponse(user);
  }

  private resolveOAuthEmail(dto: OAuthLoginDto): string {
    const trimmed = dto.email?.trim().toLowerCase();
    if (trimmed) return trimmed;
    if (dto.provider === 'twitter') {
      return `twitter-${dto.providerId}@users.doxedcryptofounder.local`;
    }
    throw new UnauthorizedException('Email required for this sign-in provider');
  }

  async validatePayload(payload: JwtPayload): Promise<AuthUser | null> {
    const user = await this.prisma.user.findUnique({
      where: { id: payload.sub },
      select: {
        id: true,
        email: true,
        name: true,
        role: true,
        banned: true,
        reputationPoints: true,
        contributorLevel: true,
      },
    });

    if (!user || user.banned) {
      return null;
    }

    return {
      id: user.id,
      email: user.email,
      name: user.name,
      role: user.role,
      reputationPoints: user.reputationPoints,
      contributorLevel: user.contributorLevel,
    };
  }

  async getProfile(userId: string): Promise<AuthUser> {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: {
        id: true,
        email: true,
        name: true,
        role: true,
        banned: true,
        reputationPoints: true,
        contributorLevel: true,
      },
    });

    if (!user || user.banned) {
      throw new UnauthorizedException('User not found');
    }

    return {
      id: user.id,
      email: user.email,
      name: user.name,
      role: user.role,
      reputationPoints: user.reputationPoints,
      contributorLevel: user.contributorLevel,
    };
  }

  private async buildAuthResponse(user: {
    id: string;
    email: string;
    name: string | null;
    role: UserRole;
  }): Promise<AuthResponse> {
    const profile = await this.getProfile(user.id);
    const payload: JwtPayload = {
      sub: user.id,
      email: user.email,
      role: user.role,
    };

    const accessToken = this.jwt.sign(payload);
    return {
      accessToken,
      user: profile,
    };
  }
}
