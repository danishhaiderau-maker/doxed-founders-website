import {
  ConflictException,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';
import { UserRole } from '@prisma/client';
import * as bcrypt from 'bcrypt';
import { PrismaService } from '../prisma/prisma.service';
import { LoginDto, RegisterDto } from './dto/auth.dto';
import { OAuthLoginDto } from './dto/oauth.dto';
import { AuthResponse, AuthUser, JwtPayload } from './auth.types';

const BCRYPT_ROUNDS = 12;

@Injectable()
export class AuthService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly jwt: JwtService,
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

    return this.buildAuthResponse(user);
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

    return this.buildAuthResponse(user);
  }

  async oauthLogin(dto: OAuthLoginDto): Promise<AuthResponse> {
    const email = dto.email.trim().toLowerCase();

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
      return this.buildAuthResponse(linked.user);
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

      const updates: { name?: string; avatarUrl?: string; emailVerified?: Date } = {};
      if (dto.name && !existingUser.name) updates.name = dto.name.trim();
      if (dto.avatarUrl && !existingUser.avatarUrl) updates.avatarUrl = dto.avatarUrl;
      updates.emailVerified = new Date();

      const user = await this.prisma.user.update({
        where: { id: existingUser.id },
        data: updates,
      });

      return this.buildAuthResponse(user);
    }

    const user = await this.prisma.user.create({
      data: {
        email,
        name: dto.name?.trim() || null,
        avatarUrl: dto.avatarUrl || null,
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

    return this.buildAuthResponse(user);
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
    };
  }

  private buildAuthResponse(user: {
    id: string;
    email: string;
    name: string | null;
    role: UserRole;
  }): AuthResponse {
    const payload: JwtPayload = {
      sub: user.id,
      email: user.email,
      role: user.role,
    };

    const accessToken = this.jwt.sign(payload);
    return {
      accessToken,
      user: {
        id: user.id,
        email: user.email,
        name: user.name,
        role: user.role,
      },
    };
  }
}
