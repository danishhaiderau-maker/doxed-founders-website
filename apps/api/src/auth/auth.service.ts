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

    return await this.buildAuthResponse(user);
  }

  async oauthLogin(dto: OAuthLoginDto): Promise<AuthResponse> {
    const email = this.resolveOAuthEmail(dto);
    const twitterHandle = dto.twitterHandle?.replace(/^@/, '').trim() || undefined;

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
