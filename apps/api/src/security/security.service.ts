import {
  BadRequestException,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';
import {
  generateAuthenticationOptions,
  generateRegistrationOptions,
  verifyAuthenticationResponse,
  verifyRegistrationResponse,
} from '@simplewebauthn/server';
import type {
  AuthenticationResponseJSON,
  RegistrationResponseJSON,
} from '@simplewebauthn/server';
import { generateSecret, generateURI, verifySync } from 'otplib';
import * as bcrypt from 'bcrypt';
import bs58 from 'bs58';
import nacl from 'tweetnacl';
import { computeSecurityScore } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { AuthService } from '../auth/auth.service';
import {
  decryptSecret,
  encryptSecret,
  generateRecoveryCode,
  hashToken,
  randomToken,
  tryDecryptSecret,
} from './security-crypto.util';
import { ChangePasswordDto } from './dto/security.dto';

const BCRYPT_ROUNDS = 12;
const PENDING_TTL_MS = 5 * 60 * 1000;

function webAuthnConfig() {
  const corsOrigin = process.env.CORS_ORIGINS?.split(',')[0]?.trim();
  const origin =
    process.env.WEBAUTHN_ORIGIN?.trim() ||
    process.env.PUBLIC_SITE_URL?.trim() ||
    corsOrigin ||
    (process.env.NEXTAUTH_URL ?? 'http://localhost:3000');
  const rpId =
    process.env.WEBAUTHN_RP_ID?.trim() ||
    new URL(origin).hostname.replace(/^www\./, '');
  const rpName = process.env.WEBAUTHN_RP_NAME ?? 'Doxxed Crypto Founder OS';
  return { origin, rpId, rpName };
}

@Injectable()
export class SecurityService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly auth: AuthService,
  ) {}

  async getSecurityProfile(userId: string) {
    const [user, totp, passkeys, recoveryCount, wallets] = await Promise.all([
      this.prisma.user.findUnique({
        where: { id: userId },
        select: { email: true, passwordHash: true },
      }),
      this.prisma.userTotp.findUnique({ where: { userId } }),
      this.prisma.webAuthnCredential.findMany({
        where: { userId },
        select: {
          id: true,
          credentialId: true,
          label: true,
          deviceType: true,
          backedUp: true,
          createdAt: true,
          lastUsedAt: true,
        },
      }),
      this.prisma.recoveryCode.count({
        where: { userId, usedAt: null },
      }),
      this.prisma.walletConnection.findMany({
        where: { userId, chain: { in: ['SOLANA', 'ETHEREUM'] } },
      }),
    ]);

    if (!user) throw new UnauthorizedException('User not found');

    const solanaWallet = wallets.find((w) => w.chain === 'SOLANA') ?? null;
    const evmWallet = wallets.find((w) => w.chain === 'ETHEREUM') ?? null;

    const score = computeSecurityScore({
      walletConnected: Boolean(solanaWallet || evmWallet),
      passkeyEnabled: passkeys.length > 0,
      totpEnabled: Boolean(totp?.enabled),
      recoveryCodesActive: recoveryCount > 0,
    });

    const mapWallet = (w: typeof solanaWallet) =>
      w ? { chain: w.chain, address: w.address, verifiedAt: w.verifiedAt } : null;

    return {
      email: user.email,
      hasPassword: Boolean(user.passwordHash),
      totpEnabled: Boolean(totp?.enabled),
      totpPendingSetup: Boolean(totp && !totp.enabled),
      passkeys,
      recoveryCodesRemaining: recoveryCount,
      wallet: mapWallet(solanaWallet),
      solanaWallet: mapWallet(solanaWallet),
      evmWallet: mapWallet(evmWallet),
      securityScore: score,
    };
  }

  async changePassword(userId: string, dto: ChangePasswordDto) {
    const user = await this.prisma.user.findUnique({ where: { id: userId } });
    if (!user?.passwordHash) {
      throw new BadRequestException('Password login is not enabled for this account');
    }
    const valid = await bcrypt.compare(dto.currentPassword, user.passwordHash);
    if (!valid) throw new UnauthorizedException('Current password is incorrect');
    const passwordHash = await bcrypt.hash(dto.newPassword, BCRYPT_ROUNDS);
    await this.prisma.user.update({ where: { id: userId }, data: { passwordHash } });
    return { ok: true };
  }

  async setupTotp(userId: string) {
    const user = await this.prisma.user.findUnique({ where: { id: userId } });
    if (!user) throw new UnauthorizedException('User not found');
    const secret = generateSecret();
    await this.prisma.userTotp.upsert({
      where: { userId },
      create: {
        userId,
        secretEncrypted: encryptSecret(secret),
        enabled: false,
      },
      update: {
        secretEncrypted: encryptSecret(secret),
        enabled: false,
        enabledAt: null,
      },
    });
    const otpauth = generateURI({ issuer: 'Founder OS', label: user.email, secret });
    return { secret, otpauthUrl: otpauth };
  }

  async enableTotp(userId: string, code: string) {
    const totp = await this.prisma.userTotp.findUnique({ where: { userId } });
    if (!totp) throw new BadRequestException('Run TOTP setup first');
    const secret = decryptSecret(totp.secretEncrypted);
    if (!this.verifyTotpToken(code.replace(/\s/g, ''), secret)) {
      throw new BadRequestException('Invalid authenticator code');
    }
    await this.prisma.userTotp.update({
      where: { userId },
      data: { enabled: true, enabledAt: new Date() },
    });
    return { ok: true };
  }

  async disableTotp(userId: string, code: string) {
    await this.verifyTotpCode(userId, code);
    await this.prisma.userTotp.update({
      where: { userId },
      data: { enabled: false, enabledAt: null },
    });
    return { ok: true };
  }

  async verifyTotpCode(userId: string, code: string) {
    const totp = await this.prisma.userTotp.findUnique({ where: { userId } });
    if (!totp?.enabled) throw new BadRequestException('2FA is not enabled');
    const secret = tryDecryptSecret(totp.secretEncrypted);
    if (!secret) {
      throw new UnauthorizedException(
        'Authenticator unavailable — use a recovery code, or run npm run fix:admin-2fa to resync JWT_SECRET',
      );
    }
    const normalized = code.replace(/\s/g, '').toUpperCase();
    if (normalized.includes('-')) {
      return this.useRecoveryCode(userId, normalized);
    }
    if (!this.verifyTotpToken(normalized, secret)) {
      throw new UnauthorizedException('Invalid authenticator code');
    }
    return { ok: true };
  }

  async generateRecoveryCodes(userId: string, verificationCode?: string) {
    const [totp, passkeyCount] = await Promise.all([
      this.prisma.userTotp.findUnique({ where: { userId } }),
      this.prisma.webAuthnCredential.count({ where: { userId } }),
    ]);

    if (totp?.enabled) {
      if (!verificationCode?.trim()) {
        throw new BadRequestException('Authenticator code required');
      }
      await this.verifyTotpCode(userId, verificationCode);
    } else if (passkeyCount > 0) {
      // Passkey-only accounts: session auth is enough to rotate backup codes.
    } else {
      throw new BadRequestException('Add a passkey or authenticator app before generating backup codes');
    }

    return this.createRecoveryCodes(userId);
  }

  private async createRecoveryCodes(userId: string) {
    await this.prisma.recoveryCode.deleteMany({ where: { userId } });
    const plainCodes: string[] = [];
    const rows: { userId: string; codeHash: string }[] = [];
    for (let i = 0; i < 10; i++) {
      const code = generateRecoveryCode();
      plainCodes.push(code);
      rows.push({
        userId,
        codeHash: await bcrypt.hash(code.replace(/-/g, ''), BCRYPT_ROUNDS),
      });
    }
    await this.prisma.recoveryCode.createMany({ data: rows });
    return { codes: plainCodes };
  }

  private async useRecoveryCode(userId: string, code: string) {
    const normalized = code.replace(/-/g, '').toUpperCase();
    const codes = await this.prisma.recoveryCode.findMany({
      where: { userId, usedAt: null },
    });
    for (const row of codes) {
      if (await bcrypt.compare(normalized, row.codeHash)) {
        await this.prisma.recoveryCode.update({
          where: { id: row.id },
          data: { usedAt: new Date() },
        });
        return { ok: true, usedRecoveryCode: true };
      }
    }
    throw new UnauthorizedException('Invalid recovery code');
  }

  async userRequires2Fa(userId: string): Promise<boolean> {
    const [totp, passkeys] = await Promise.all([
      this.prisma.userTotp.findUnique({ where: { userId } }),
      this.prisma.webAuthnCredential.count({ where: { userId } }),
    ]);
    return Boolean(totp?.enabled) || passkeys > 0;
  }

  async createLoginPending(userId: string) {
    const token = randomToken();
    const tokenHash = hashToken(token);
    await this.prisma.authPendingChallenge.create({
      data: {
        userId,
        tokenHash,
        kind: 'LOGIN_2FA',
        expiresAt: new Date(Date.now() + PENDING_TTL_MS),
      },
    });
    const methods: string[] = [];
    const totp = await this.prisma.userTotp.findUnique({ where: { userId } });
    const passkeyCount = await this.prisma.webAuthnCredential.count({ where: { userId } });
    if (totp?.enabled) methods.push('totp');
    if (passkeyCount > 0) methods.push('passkey', 'recovery');
    return { pendingToken: token, methods };
  }

  async complete2FaLogin(pendingToken: string, totpCode?: string, recoveryCode?: string) {
    const challenge = await this.getPending(pendingToken, 'LOGIN_2FA');
    const recovery = recoveryCode?.trim();
    const totp = totpCode?.trim();
    try {
      if (recovery) {
        await this.useRecoveryCode(challenge.userId, recovery);
      } else if (totp) {
        await this.verifyTotpCode(challenge.userId, totp);
      } else {
        throw new BadRequestException('Provide authenticator code or recovery code');
      }
    } catch (err) {
      throw err;
    }
    await this.prisma.authPendingChallenge.delete({ where: { id: challenge.id } });
    return this.auth.buildAuthResponseForUserId(challenge.userId);
  }

  async passkeyRegisterOptions(userId: string) {
    const user = await this.prisma.user.findUnique({ where: { id: userId } });
    if (!user) throw new UnauthorizedException('User not found');
    const { origin, rpId, rpName } = webAuthnConfig();
    const existing = await this.prisma.webAuthnCredential.findMany({
      where: { userId },
      select: { credentialId: true, transports: true },
    });
    const options = await generateRegistrationOptions({
      rpName,
      rpID: rpId,
      userName: user.email,
      userDisplayName: user.name ?? user.email,
      userID: Buffer.from(userId),
      attestationType: 'none',
      excludeCredentials: existing.map((c) => ({
        id: c.credentialId,
        transports: c.transports?.split(',') as never,
      })),
      authenticatorSelection: {
        residentKey: 'preferred',
        userVerification: 'preferred',
      },
    });
    const token = randomToken();
    await this.prisma.authPendingChallenge.create({
      data: {
        userId,
        tokenHash: hashToken(token),
        kind: 'PASSKEY_REGISTER',
        payload: JSON.parse(JSON.stringify(options)),
        expiresAt: new Date(Date.now() + PENDING_TTL_MS),
      },
    });
    return { options, registerToken: token };
  }

  async passkeyRegisterVerify(userId: string, registerToken: string, response: RegistrationResponseJSON, label?: string) {
    const challenge = await this.consumePending(registerToken, 'PASSKEY_REGISTER');
    if (challenge.userId !== userId) throw new UnauthorizedException('Challenge mismatch');
    const { origin, rpId } = webAuthnConfig();
    const verification = await verifyRegistrationResponse({
      response,
      expectedChallenge: (challenge.payload as { challenge: string }).challenge,
      expectedOrigin: origin,
      expectedRPID: rpId,
    });
    if (!verification.verified || !verification.registrationInfo) {
      throw new BadRequestException('Passkey registration failed');
    }
    const { credential, credentialDeviceType, credentialBackedUp } = verification.registrationInfo;
    await this.prisma.webAuthnCredential.create({
      data: {
        userId,
        credentialId: credential.id,
        publicKey: Buffer.from(credential.publicKey).toString('base64'),
        counter: BigInt(credential.counter),
        deviceType: credentialDeviceType,
        backedUp: credentialBackedUp,
        transports: response.response.transports?.join(',') ?? null,
        label: label?.trim() || 'Passkey',
      },
    });

    const remaining = await this.prisma.recoveryCode.count({
      where: { userId, usedAt: null },
    });
    let recoveryCodes: string[] | undefined;
    if (remaining === 0) {
      recoveryCodes = (await this.createRecoveryCodes(userId)).codes;
    }

    return { ok: true, recoveryCodes };
  }

  async passkeyLoginOptions(pendingToken: string) {
    const challenge = await this.getPending(pendingToken, 'LOGIN_2FA');
    const credentials = await this.prisma.webAuthnCredential.findMany({
      where: { userId: challenge.userId },
    });
    const { rpId } = webAuthnConfig();
    const options = await generateAuthenticationOptions({
      rpID: rpId,
      allowCredentials: credentials.map((c) => ({
        id: c.credentialId,
        transports: c.transports?.split(',') as never,
      })),
      userVerification: 'preferred',
    });
    const token = randomToken();
    await this.prisma.authPendingChallenge.create({
      data: {
        userId: challenge.userId,
        tokenHash: hashToken(token),
        kind: 'PASSKEY_LOGIN',
        payload: JSON.parse(JSON.stringify(options)),
        expiresAt: new Date(Date.now() + PENDING_TTL_MS),
      },
    });
    return { options, passkeyToken: token };
  }

  async passkeyLoginVerify(passkeyToken: string, response: AuthenticationResponseJSON) {
    const challenge = await this.consumePending(passkeyToken, 'PASSKEY_LOGIN');
    const credential = await this.prisma.webAuthnCredential.findUnique({
      where: { credentialId: response.id },
    });
    if (!credential || credential.userId !== challenge.userId) {
      throw new UnauthorizedException('Unknown passkey');
    }
    const { origin, rpId } = webAuthnConfig();
    const verification = await verifyAuthenticationResponse({
      response,
      expectedChallenge: (challenge.payload as { challenge: string }).challenge,
      expectedOrigin: origin,
      expectedRPID: rpId,
      credential: {
        id: credential.credentialId,
        publicKey: Buffer.from(credential.publicKey, 'base64'),
        counter: Number(credential.counter),
        transports: credential.transports?.split(',') as never,
      },
    });
    if (!verification.verified) throw new UnauthorizedException('Passkey verification failed');
    await this.prisma.webAuthnCredential.update({
      where: { id: credential.id },
      data: {
        counter: BigInt(verification.authenticationInfo.newCounter),
        lastUsedAt: new Date(),
      },
    });
    await this.prisma.authPendingChallenge.deleteMany({
      where: { userId: challenge.userId, kind: 'LOGIN_2FA' },
    });
    return this.auth.buildAuthResponseForUserId(challenge.userId);
  }

  async deletePasskey(userId: string, credentialId: string) {
    const row = await this.prisma.webAuthnCredential.findFirst({
      where: { userId, credentialId },
    });
    if (!row) throw new BadRequestException('Passkey not found');
    await this.prisma.webAuthnCredential.delete({ where: { id: row.id } });
    return { ok: true };
  }

  async renamePasskey(userId: string, credentialId: string, label: string) {
    const row = await this.prisma.webAuthnCredential.findFirst({
      where: { userId, credentialId },
    });
    if (!row) throw new BadRequestException('Passkey not found');
    await this.prisma.webAuthnCredential.update({
      where: { id: row.id },
      data: { label: label.trim() },
    });
    return { ok: true };
  }

  async walletChallenge(userId: string) {
    const nonce = randomToken(16);
    const message = `Sign in to Doxxed Crypto Founder OS\nUser: ${userId}\nNonce: ${nonce}\nIssued: ${new Date().toISOString()}`;
    const token = randomToken();
    await this.prisma.authPendingChallenge.create({
      data: {
        userId,
        tokenHash: hashToken(token),
        kind: 'WALLET_VERIFY',
        payload: { message, nonce },
        expiresAt: new Date(Date.now() + PENDING_TTL_MS),
      },
    });
    return { challengeToken: token, message };
  }

  async walletVerify(
    userId: string,
    challengeToken: string,
    address: string,
    signature: string,
    message: string,
    chain: 'SOLANA' | 'ETHEREUM' = 'SOLANA',
  ) {
    const challenge = await this.consumePending(challengeToken, 'WALLET_VERIFY');
    if (challenge.userId !== userId) throw new UnauthorizedException('Challenge mismatch');
    const payload = challenge.payload as { message: string; nonce: string };
    if (payload.message !== message) throw new BadRequestException('Message mismatch');

    const valid =
      chain === 'ETHEREUM'
        ? this.verifyEvmSignature(message, signature, address)
        : this.verifySolanaSignature(message, signature, address);
    if (!valid) throw new BadRequestException('Invalid wallet signature');

    await this.prisma.walletConnection.upsert({
      where: { userId_chain: { userId, chain } },
      create: { userId, chain, address },
      update: { address, verifiedAt: new Date() },
    });
    return { ok: true, address, chain };
  }

  async disconnectWallet(userId: string, chain = 'SOLANA') {
    await this.prisma.walletConnection.deleteMany({
      where: { userId, chain: chain as 'SOLANA' | 'ETHEREUM' },
    });
    return { ok: true };
  }

  private verifyTotpToken(token: string, secret: string): boolean {
    return verifySync({ token, secret }).valid;
  }

  private verifyEvmSignature(message: string, signature: string, address: string): boolean {
    try {
      const { verifyMessage } = require('ethers') as typeof import('ethers');
      const recovered = verifyMessage(message, signature);
      return recovered.toLowerCase() === address.toLowerCase();
    } catch {
      return false;
    }
  }

  private verifySolanaSignature(message: string, signature: string, address: string): boolean {
    try {
      const msgBytes = new TextEncoder().encode(message);
      const sigBytes = bs58.decode(signature);
      const pubBytes = bs58.decode(address);
      return nacl.sign.detached.verify(msgBytes, sigBytes, pubBytes);
    } catch {
      return false;
    }
  }

  private async getPending(token: string, kind: string) {
    const row = await this.prisma.authPendingChallenge.findUnique({
      where: { tokenHash: hashToken(token) },
    });
    if (!row || row.kind !== kind || row.expiresAt < new Date()) {
      throw new UnauthorizedException('Challenge expired or invalid');
    }
    return row;
  }

  private async consumePending(token: string, kind: string) {
    const row = await this.getPending(token, kind);
    await this.prisma.authPendingChallenge.delete({ where: { id: row.id } });
    return row;
  }
}
