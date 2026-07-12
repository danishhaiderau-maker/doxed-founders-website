import { Injectable, Logger } from '@nestjs/common';
import {
  Connection,
  Keypair,
  LAMPORTS_PER_SOL,
  PublicKey,
  SystemProgram,
  Transaction,
  clusterApiUrl,
  Commitment,
} from '@solana/web3.js';

/**
 * Solana devnet mint wrapper. Phase 8 thin slice — we are NOT shipping a full
 * SPL token program here. This service:
 *
 *   1. Generates (or reuses) a platform-funded mint keypair on devnet.
 *   2. Airdrops a small amount of SOL to it (for fee + rent).
 *   3. Records the mint address + supply on the TokenLaunch row.
 *
 * The full SPL token mint + transfer is a Phase 7+ deliverable. For Phase 8
 * we surface the mint address (with a Solana explorer link) so the launch
 * flow feels real end-to-end. Spec rule: devnet ONLY.
 *
 * Owner will swap https://api.devnet.solana.com for a paid RPC later.
 */
@Injectable()
export class SolanaMintService {
  private readonly logger = new Logger(SolanaMintService.name);
  private readonly connection: Connection;
  private readonly funder: Keypair;

  constructor() {
    const rpc =
      process.env.SOLANA_DEVNET_RPC_URL?.trim() || clusterApiUrl('devnet');
    const commitment: Commitment = 'confirmed';
    this.connection = new Connection(rpc, commitment);

    // Platform-funded mint keypair. Reused across launches during Phase 8
    // (each launch records its own mint address on the row, but the funder
    // is the platform wallet for now). The owner will rotate to per-launch
    // keypairs when migrating to mainnet.
    const secretEnv = process.env.SOLANA_DEVNET_FUNDER_SECRET;
    if (secretEnv) {
      try {
        const secret = Uint8Array.from(JSON.parse(secretEnv));
        this.funder = Keypair.fromSecretKey(secret);
      } catch (err) {
        this.logger.warn(
          `SOLANA_DEVNET_FUNDER_SECRET parse failed — generating ephemeral keypair: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
        this.funder = Keypair.generate();
      }
    } else {
      this.funder = Keypair.generate();
    }
  }

  /**
   * Mint a new SPL-style token on **devnet only** for a launch. Returns the
   * mint address + supply. Never targets mainnet — SOLANA_DEVNET_RPC_URL
   * defaults to clusterApiUrl('devnet').
   *
   * Phase 8 thin slice: System Program account creation (placeholder mint).
   * Real SPL Token Program mint is deferred until counsel / program choice.
   * When airdrop or fund-to-mint fails (rate limits), we still return a
   * deterministic mint address + explorer URL so the launch UX stays real.
   */
  async mintLaunchToken(projectName: string, supply: number): Promise<{
    mintAddress: string;
    supply: number;
    signature?: string;
    explorerUrl: string;
    network: 'devnet';
    stub: boolean;
  }> {
    const mintKeypair = Keypair.generate();
    const mintAddress = mintKeypair.publicKey.toBase58();
    let stub = true;

    // Fund the mint account with a small amount of SOL for rent + fees.
    // Wrapped in try/catch — devnet airdrops are rate-limited and flaky.
    let signature: string | undefined;
    try {
      const fundSig = await this.connection.requestAirdrop(
        this.funder.publicKey,
        0.05 * LAMPORTS_PER_SOL,
      );
      await this.connection.confirmTransaction(fundSig, 'confirmed');
      this.logger.log(`devnet airdrop funded funder: ${fundSig}`);
    } catch (err) {
      this.logger.warn(
        `devnet airdrop failed (rate-limit?) — proceeding with stub mint: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }

    try {
      const funderBalance = await this.connection.getBalance(
        this.funder.publicKey,
      );
      if (funderBalance > 5000) {
        const tx = new Transaction().add(
          SystemProgram.transfer({
            fromPubkey: this.funder.publicKey,
            toPubkey: mintKeypair.publicKey,
            lamports: Math.min(funderBalance - 5000, 0.001 * LAMPORTS_PER_SOL),
          }),
        );
        signature = await this.connection.sendTransaction(tx, [
          this.funder,
          mintKeypair,
        ]);
        await this.connection.confirmTransaction(signature, 'confirmed');
        stub = false;
      }
    } catch (err) {
      this.logger.warn(
        `devnet fund-to-mint failed — recording address without on-chain tx: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }

    const explorerUrl = `https://explorer.solana.com/address/${mintAddress}?cluster=devnet`;
    this.logger.log(
      `minted devnet token for "${projectName}" → ${mintAddress} (supply ${supply}, stub=${stub})`,
    );

    return { mintAddress, supply, signature, explorerUrl, network: 'devnet', stub };
  }

  /**
   * Read-only helper: get the devnet balance of an address (for the UI
   * explorer link / sanity check).
   */
  async getDevnetBalance(address: string): Promise<number> {
    try {
      const pubkey = new PublicKey(address);
      const lamports = await this.connection.getBalance(pubkey);
      return lamports / LAMPORTS_PER_SOL;
    } catch {
      return 0;
    }
  }

  /** Stable explorer URL for a mint address (devnet cluster). */
  explorerUrl(mintAddress: string): string {
    return `https://explorer.solana.com/address/${mintAddress}?cluster=devnet`;
  }
}
