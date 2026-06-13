import { PrismaService } from '../prisma/prisma.service';

/** Solana treasury from DB (admin UI) or Railway env bootstrap. */
export function solanaRpcUrl(): string {
  return (
    process.env.HELIUS_RPC_URL?.trim() ||
    process.env.SOLANA_RPC_URL?.trim() ||
    'https://api.mainnet-beta.solana.com'
  );
}

export function treasuryAddressFromEnv(): string | null {
  return (
    process.env.PLATFORM_SOLANA_TREASURY?.trim() ||
    process.env.SOLANA_TREASURY_ADDRESS?.trim() ||
    null
  );
}

export async function resolveSolanaTreasuryAddress(
  prisma: PrismaService,
): Promise<string | null> {
  const row = await prisma.platformTreasury.findUnique({ where: { id: 'default' } });
  return row?.solanaTreasuryAddress?.trim() || treasuryAddressFromEnv();
}

export function evmTreasuryAddressFromEnv(): string | null {
  return process.env.X402_EVM_PAY_TO?.trim() || process.env.PLATFORM_EVM_TREASURY?.trim() || null;
}

export async function resolveEvmTreasuryAddress(prisma: PrismaService): Promise<string | null> {
  const row = await prisma.platformTreasury.findUnique({ where: { id: 'default' } });
  return row?.evmTreasuryAddress?.trim() || evmTreasuryAddressFromEnv();
}

export function isSolanaTopUpConfigured(treasuryAddress: string | null): boolean {
  return Boolean(solanaRpcUrl() && treasuryAddress);
}
