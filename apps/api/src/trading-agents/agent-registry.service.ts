import { Injectable, NotFoundException } from '@nestjs/common';
import {
  AgentRegistryKind,
  AgentRegistryStatus,
  ChainSlug,
  UserRole,
} from '@prisma/client';
import {
  AGENT_REGISTRY_TARGETS,
  buildConservativeBtcAgentCard,
  buildConservativeBtcErc8004AgentJson,
  resolveConservativeBtcAgentUrls,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { resolveSolanaTreasuryAddress } from '../payments/platform-treasury';

const SPAWN_REGISTRY = '0x8004A169FB4a3325136EB29fA0ceB6D2e539a432';

@Injectable()
export class AgentRegistryService {
  constructor(private readonly prisma: PrismaService) {}

  private async resolveAgent(slug: string) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug } });
    if (!agent) throw new NotFoundException('Agent not found');
    return agent;
  }

  async getRegistrationOverview(slug: string) {
    const agent = await this.resolveAgent(slug);
    const [treasury, evmTreasury, entries, adminWallet] = await Promise.all([
      resolveSolanaTreasuryAddress(this.prisma),
      this.prisma.platformTreasury
        .findUnique({ where: { id: 'default' } })
        .then((r) => r?.evmTreasuryAddress?.trim() ?? null),
      this.prisma.agentRegistryEntry.findMany({
        where: { agentId: agent.id },
        orderBy: { registry: 'asc' },
      }),
      this.prisma.walletConnection.findFirst({
        where: { chain: ChainSlug.SOLANA, user: { role: UserRole.ADMIN } },
        select: { address: true, user: { select: { email: true } } },
      }),
    ]);

    const site = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://doxxedcrypto.digital';
    const api = process.env.NEXT_PUBLIC_API_URL ?? process.env.TRADING_AGENT_BOT_URL ?? '';
    const urls = resolveConservativeBtcAgentUrls(site, api);
    const feeWalletSolana = treasury ?? adminWallet?.address ?? null;

    const agentCard = buildConservativeBtcAgentCard({
      site,
      api,
      feeWalletSolana,
      feeWalletEvm: evmTreasury,
    });
    const agentJson = buildConservativeBtcErc8004AgentJson({
      site,
      api,
      feeWalletSolana,
      ownerAddress: evmTreasury,
    });

    const entryByRegistry = new Map(entries.map((e) => [e.registry, e]));

    const checklist = AGENT_REGISTRY_TARGETS.map((target) => {
      const metadataUri =
        target.metadataUriKey === 'agentJson' ? urls.agentJson : urls.agentCard;
      const entry = entryByRegistry.get(target.id as AgentRegistryKind);
      return {
        registry: target.id,
        label: target.label,
        metadataUri,
        status: entry?.status ?? AgentRegistryStatus.PENDING,
        externalId: entry?.externalId ?? null,
        registryUrl: entry?.registryUrl ?? null,
        txSignature: entry?.txSignature ?? null,
        ownerAddress: entry?.ownerAddress ?? null,
        registeredAt: entry?.registeredAt?.toISOString() ?? null,
        verifiedAt: entry?.verifiedAt?.toISOString() ?? null,
        notes: entry?.notes ?? null,
        instructions: this.instructionsFor(target.id, {
          metadataUri,
          feeWalletSolana,
          evmTreasury,
          agentName: agent.name,
        }),
      };
    });

    return {
      agent: { slug: agent.slug, name: agent.name, id: agent.id },
      feeCollection: {
        solanaTreasury: feeWalletSolana,
        evmTreasury,
        adminLinkedSolana: adminWallet?.address ?? null,
        adminEmail: adminWallet?.user.email ?? null,
        ready: Boolean(feeWalletSolana),
        message: feeWalletSolana
          ? 'Signal success fees settle to this Solana address (USDC). DDollar is fallback when subscriber balance covers the fee.'
          : 'Connect Phantom in Account → Security and save Solana treasury in Admin → Platform.',
      },
      metadata: {
        urls,
        agentCard,
        agentJson,
      },
      spawn: {
        chainId: 8453,
        chainSlug: 'base',
        registryContract: SPAWN_REGISTRY,
        metadataUri: urls.agentJson,
        apiEndpoint: 'https://thespawn.io/api/v1/agents',
        qualityCheck: `npx spawnr@latest check base:<agent_id>`,
        samplePayload: {
          name: agent.name,
          description: agent.description ?? agentJson.description,
          chain_id: 8453,
          metadata_uri: urls.agentJson,
          image_url: agentJson.image,
          x402_support: false,
          services: agentJson.services,
          owner_address: evmTreasury ?? undefined,
        },
      },
      said: {
        metadataUri: urls.agentCard,
        registerCommand: `npx said-sdk register -k agent-wallet.json -n "${agent.name}" --uri "${urls.agentCard}"`,
        verifyCommand: 'npx said-sdk verify -k ~/.config/dcf/agent-wallet.json',
        verifyCostSol: 0.01,
        program: '5dpw6KEQPn248pnkkaYyWfHwu2nfb3LUMbTucb6LaA8G',
        docs: 'https://www.saidprotocol.com',
      },
      checklist,
      entries: entries.map((e) => ({
        id: e.id,
        registry: e.registry,
        status: e.status,
        chainSlug: e.chainSlug,
        externalId: e.externalId,
        ownerAddress: e.ownerAddress,
        metadataUri: e.metadataUri,
        registryUrl: e.registryUrl,
        txSignature: e.txSignature,
        registeredAt: e.registeredAt?.toISOString() ?? null,
        verifiedAt: e.verifiedAt?.toISOString() ?? null,
        notes: e.notes,
      })),
    };
  }

  private instructionsFor(
    registry: string,
    ctx: {
      metadataUri: string;
      feeWalletSolana: string | null;
      evmTreasury: string | null;
      agentName: string;
    },
  ): string[] {
    switch (registry) {
      case 'AGENT_CARD':
        return [
          `Metadata live at ${ctx.metadataUri}`,
          'No on-chain step — used as SAID metadata_uri and internal discovery.',
        ];
      case 'SAID':
        return [
          'Fund Phantom with ~0.02 SOL for register + verify fees.',
          `Run: npx said-sdk register -k <keypair.json> -n "${ctx.agentName}" --uri "${ctx.metadataUri}"`,
          'Sign with Phantom (import keypair or use said-register wizard).',
          'Optional verify badge: npx said-sdk verify (~0.01 SOL).',
          ctx.feeWalletSolana
            ? `Agent fees collect to admin treasury: ${ctx.feeWalletSolana}`
            : 'Set Solana treasury before going live.',
        ];
      case 'SPAWN':
        return [
          `Ensure ${ctx.metadataUri.replace('agent-card', 'agent.json')} returns HTTP 200.`,
          'POST to https://thespawn.io/api/v1/agents with Bearer API key (request at thespawn.io).',
          'Sign returned ERC-8004 register(string) tx on Base with admin EVM wallet.',
          ctx.evmTreasury
            ? `Owner wallet: ${ctx.evmTreasury}`
            : 'Link EVM wallet in Account → Security for Base registration.',
          'Run: npx spawnr@latest check base:<agent_id>',
        ];
      case 'ERC8004_SCAN':
        return [
          'Auto-indexed after Spawn/Base ERC-8004 mint.',
          'Check https://8004scan.io and https://agentscan.info after mint confirms.',
        ];
      case 'FUSHU':
        return [
          'Manifest: fushu.json in repo root.',
          'API: npm run register:agents-automated (POST /api/v1/submit when healthy).',
        ];
      case 'SKILLS_SH':
        return [
          'Set OPENSERV_USER_API_KEY in vault.',
          'Run: npm run provision:openserv',
          'Then Submit for Review on platform.openserv.ai if required.',
        ];
      default:
        return ['Complete prior registry steps first.'];
    }
  }

  async upsertRegistration(
    slug: string,
    body: {
      registry: AgentRegistryKind;
      status?: AgentRegistryStatus;
      chainSlug?: ChainSlug;
      externalId?: string;
      ownerAddress?: string;
      metadataUri?: string;
      registryUrl?: string;
      txSignature?: string;
      notes?: string;
      verified?: boolean;
    },
  ) {
    const agent = await this.resolveAgent(slug);
    const now = new Date();
    const status = body.status ?? AgentRegistryStatus.REGISTERED;

    const row = await this.prisma.agentRegistryEntry.upsert({
      where: {
        agentId_registry: { agentId: agent.id, registry: body.registry },
      },
      create: {
        agentId: agent.id,
        registry: body.registry,
        chainSlug: body.chainSlug ?? null,
        externalId: body.externalId?.trim() || null,
        ownerAddress: body.ownerAddress?.trim() || null,
        metadataUri: body.metadataUri?.trim() || null,
        registryUrl: body.registryUrl?.trim() || null,
        txSignature: body.txSignature?.trim() || null,
        status,
        notes: body.notes?.trim() || null,
        registeredAt: status !== AgentRegistryStatus.PENDING ? now : null,
        verifiedAt: body.verified ? now : null,
      },
      update: {
        chainSlug: body.chainSlug ?? undefined,
        externalId: body.externalId?.trim() || undefined,
        ownerAddress: body.ownerAddress?.trim() || undefined,
        metadataUri: body.metadataUri?.trim() || undefined,
        registryUrl: body.registryUrl?.trim() || undefined,
        txSignature: body.txSignature?.trim() || undefined,
        status,
        notes: body.notes?.trim() || undefined,
        registeredAt: status !== AgentRegistryStatus.PENDING ? now : undefined,
        verifiedAt: body.verified ? now : undefined,
      },
    });

    return {
      ok: true,
      entry: {
        id: row.id,
        registry: row.registry,
        status: row.status,
        externalId: row.externalId,
        registryUrl: row.registryUrl,
        txSignature: row.txSignature,
      },
    };
  }
}
