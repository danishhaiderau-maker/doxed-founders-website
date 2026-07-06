import { Injectable, Logger } from '@nestjs/common';
import {
  BuilderTier,
  CommunityValidationCategory,
  FounderEventType,
  FounderJourneyStage,
  FounderPresenceLevel,
  ProjectLifecycleStage,
  SimulatedRaiseStatus,
  UserProgressTier,
} from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { ProjectsService } from '../projects/projects.service';
import { FounderDenService } from '../founder-den/founder-den.service';
import { FounderOsService } from '../founder-os/founder-os.service';
import {
  DEMO_HANDLE_PREFIX,
  DEMO_SCALE_PRESETS,
  DEMO_SLUG_PREFIX,
  demoFounderWhere,
  demoProjectWhere,
  demoUserEmail,
  demoUserWhere,
  isDemoModeEnabled,
  parseDemoSeedScale,
} from './demo.constants';

type SmokeCheck = {
  name: string;
  passed: boolean;
  detail: string;
  durationMs: number;
};

const DEMO_PROJECT_BLUEPRINTS: {
  slugSuffix: string;
  name: string;
  ticker: string;
  categorySlug: string;
  stage: ProjectLifecycleStage;
  withRaise: boolean;
  raiseGoalUsd: number;
}[] = [
  { slugSuffix: 'payflow', name: 'Demo PayFlow', ticker: 'DPAY', categorySlug: 'payments', stage: ProjectLifecycleStage.MVP, withRaise: true, raiseGoalUsd: 25000 },
  { slugSuffix: 'scoutlens', name: 'Demo ScoutLens', ticker: 'DSCL', categorySlug: 'infrastructure', stage: ProjectLifecycleStage.BETA, withRaise: true, raiseGoalUsd: 40000 },
  { slugSuffix: 'chainvault', name: 'Demo ChainVault', ticker: 'DCVT', categorySlug: 'defi', stage: ProjectLifecycleStage.SIMULATED_RAISE, withRaise: true, raiseGoalUsd: 75000 },
  { slugSuffix: 'playgrid', name: 'Demo PlayGrid', ticker: 'DPLG', categorySlug: 'gaming', stage: ProjectLifecycleStage.PROTOTYPE, withRaise: false, raiseGoalUsd: 0 },
  { slugSuffix: 'idmesh', name: 'Demo IDMesh', ticker: 'DIDM', categorySlug: 'identity', stage: ProjectLifecycleStage.DEMAND_VALIDATION, withRaise: true, raiseGoalUsd: 18000 },
  { slugSuffix: 'liquidity-lab', name: 'Demo Liquidity Lab', ticker: 'DLLB', categorySlug: 'defi', stage: ProjectLifecycleStage.LAUNCH_READY, withRaise: true, raiseGoalUsd: 120000 },
  { slugSuffix: 'nodeforge', name: 'Demo NodeForge', ticker: 'DNFG', categorySlug: 'infrastructure', stage: ProjectLifecycleStage.IDEA, withRaise: false, raiseGoalUsd: 0 },
  { slugSuffix: 'wallet-weave', name: 'Demo WalletWeave', ticker: 'DWWV', categorySlug: 'payments', stage: ProjectLifecycleStage.TOKEN_LAUNCH, withRaise: true, raiseGoalUsd: 95000 },
  { slugSuffix: 'trustpulse', name: 'Demo TrustPulse', ticker: 'DTPL', categorySlug: 'identity', stage: ProjectLifecycleStage.BRAINSTORMING, withRaise: false, raiseGoalUsd: 0 },
  { slugSuffix: 'raise-radar', name: 'Demo Raise Radar', ticker: 'DRRD', categorySlug: 'infrastructure', stage: ProjectLifecycleStage.LIVE_TRADING, withRaise: true, raiseGoalUsd: 150000 },
  { slugSuffix: 'founderforge', name: 'Demo FounderForge', ticker: 'DFFG', categorySlug: 'infrastructure', stage: ProjectLifecycleStage.MVP, withRaise: true, raiseGoalUsd: 32000 },
  { slugSuffix: 'conviction-hub', name: 'Demo Conviction Hub', ticker: 'DCVH', categorySlug: 'defi', stage: ProjectLifecycleStage.BETA, withRaise: true, raiseGoalUsd: 55000 },
  { slugSuffix: 'scout-signal', name: 'Demo Scout Signal', ticker: 'DSSG', categorySlug: 'gaming', stage: ProjectLifecycleStage.PROTOTYPE, withRaise: false, raiseGoalUsd: 0 },
  { slugSuffix: 'builder-beacon', name: 'Demo Builder Beacon', ticker: 'DBBC', categorySlug: 'infrastructure', stage: ProjectLifecycleStage.DEMAND_VALIDATION, withRaise: true, raiseGoalUsd: 22000 },
  { slugSuffix: 'token-timeline', name: 'Demo Token Timeline', ticker: 'DTTL', categorySlug: 'defi', stage: ProjectLifecycleStage.SIMULATED_RAISE, withRaise: true, raiseGoalUsd: 88000 },
];

const TRUST_CATEGORIES: CommunityValidationCategory[] = [
  CommunityValidationCategory.LOOKS_LEGIT,
  CommunityValidationCategory.BUILDING_CONSISTENTLY,
  CommunityValidationCategory.COMMUNITY_EXISTS,
  CommunityValidationCategory.NEEDS_MORE_PROOF,
];

@Injectable()
export class DemoSeedService {
  private readonly logger = new Logger(DemoSeedService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly projects: ProjectsService,
    private readonly founderDen: FounderDenService,
    private readonly founderOs: FounderOsService,
  ) {}

  async getStatus() {
    const [users, projects, founders, activeRaises, allocationAgg, ledgerAgg] = await Promise.all([
      this.prisma.user.count({ where: demoUserWhere() }),
      this.prisma.project.count({ where: demoProjectWhere() }),
      this.prisma.founder.count({ where: demoFounderWhere() }),
      this.prisma.simulatedRaise.count({
        where: { status: SimulatedRaiseStatus.ACTIVE, project: demoProjectWhere() },
      }),
      this.prisma.raiseAllocation.aggregate({
        where: { raise: { project: demoProjectWhere() } },
        _sum: { amountUsd: true },
        _count: true,
      }),
      this.prisma.pointLedger.aggregate({
        where: { user: demoUserWhere(), amount: { gt: 0 } },
        _sum: { amount: true },
      }),
    ]);

    const sampleProject = await this.prisma.project.findFirst({
      where: demoProjectWhere(),
      orderBy: { createdAt: 'asc' },
      select: { slug: true, name: true },
    });

    return {
      enabled: isDemoModeEnabled(),
      scale: parseDemoSeedScale(process.env.DEMO_SEED_SCALE),
      seeded: users > 0 || projects > 0,
      counts: {
        users,
        projects,
        founders,
        activeRaises,
        raiseAllocations: allocationAgg._count,
        totalPaperRaiseUsd: Number(allocationAgg._sum.amountUsd ?? 0),
        lifetimeContributionPoints: ledgerAgg._sum.amount ?? 0,
      },
      samples: {
        projectSlug: sampleProject?.slug ?? `${DEMO_SLUG_PREFIX}payflow`,
        userEmail: demoUserEmail(1, 'founder'),
      },
    };
  }

  async seedEcosystem() {
    const scale = parseDemoSeedScale(process.env.DEMO_SEED_SCALE);
    const preset = DEMO_SCALE_PRESETS[scale];
    const existingUsers = await this.prisma.user.count({ where: demoUserWhere() });

    const chain = await this.prisma.chain.findUnique({ where: { slug: 'ETHEREUM' } });
    if (!chain) {
      throw new Error('Reference chain ETHEREUM missing — run prisma db seed first.');
    }

    const categories = await this.prisma.category.findMany({
      where: { slug: { in: ['defi', 'infrastructure', 'gaming', 'payments', 'identity'] } },
    });
    const categoryBySlug = Object.fromEntries(categories.map((c) => [c.slug, c.id]));

    const projectBlueprints = DEMO_PROJECT_BLUEPRINTS.slice(0, preset.projects);
    const created = {
      usersCreated: 0,
      usersUpdated: 0,
      foundersCreated: 0,
      projectsCreated: 0,
      raisesCreated: 0,
      allocationsCreated: 0,
      trustReportsCreated: 0,
      eventsCreated: 0,
      ledgerEntriesCreated: 0,
    };

    const demoUsers: { id: string; role: 'founder' | 'builder' | 'scout'; index: number }[] = [];

    for (let i = 1; i <= preset.users; i += 1) {
      const role: 'founder' | 'builder' | 'scout' =
        i <= preset.founders ? 'founder' : i <= preset.founders + Math.floor(preset.users * 0.35) ? 'builder' : 'scout';
      const email = demoUserEmail(i, role);
      const handle = `${DEMO_HANDLE_PREFIX}${role}_${String(i).padStart(3, '0')}`;
      const lifetimeEarned = 800 + (i * 47) % 2200;
      const spendable = Math.max(50, Math.floor(lifetimeEarned * (0.25 + (i % 5) * 0.1)));

      const progressTier =
        role === 'founder'
          ? UserProgressTier.FOUNDER_BUILDING
          : role === 'builder'
            ? UserProgressTier.COMMUNITY_CONTRIBUTOR
            : i % 2 === 0
              ? UserProgressTier.TRADER
              : UserProgressTier.EXPLORER;

      const user = await this.prisma.user.upsert({
        where: { email },
        create: {
          email,
          name: `demo_${role}_${String(i).padStart(3, '0')}`,
          platformHandle: handle,
          role: 'USER',
          reputationPoints: spendable,
          contributorLevel: Math.min(10, Math.floor(spendable / 200) + 1),
          progressTier,
          builderTier: role === 'founder' ? BuilderTier.VERIFIED_BUILDER : BuilderTier.PARASITE,
          xVerified: role === 'founder',
          notificationPrefs: { isDemo: true, lifetimeContributionEarned: lifetimeEarned },
        },
        update: {
          name: `demo_${role}_${String(i).padStart(3, '0')}`,
          platformHandle: handle,
          reputationPoints: spendable,
          progressTier,
          notificationPrefs: { isDemo: true, lifetimeContributionEarned: lifetimeEarned },
        },
      });

      if (user.createdAt.getTime() === user.updatedAt.getTime()) {
        created.usersCreated += 1;
      } else {
        created.usersUpdated += 1;
      }

      demoUsers.push({ id: user.id, role, index: i });

      const ledgerCount = await this.prisma.pointLedger.count({ where: { userId: user.id } });
      if (ledgerCount === 0) {
        const chunks = [
          { amount: Math.floor(lifetimeEarned * 0.4), actionKey: 'SCOUT_EARLY', label: 'Demo — early scout conviction' },
          { amount: Math.floor(lifetimeEarned * 0.35), actionKey: 'COMMUNITY_HELPFUL', label: 'Demo — helpful validation' },
          { amount: lifetimeEarned - Math.floor(lifetimeEarned * 0.75), actionKey: 'FOUNDER_BUILD_POST', label: 'Demo — build log contribution' },
        ];
        for (const chunk of chunks) {
          if (chunk.amount <= 0) continue;
          await this.prisma.pointLedger.create({
            data: {
              userId: user.id,
              amount: chunk.amount,
              actionKey: chunk.actionKey,
              label: chunk.label,
            },
          });
          created.ledgerEntriesCreated += 1;
        }
        const spent = lifetimeEarned - spendable;
        if (spent > 0) {
          await this.prisma.pointLedger.create({
            data: {
              userId: user.id,
              amount: -spent,
              actionKey: 'AI_SPEND',
              label: 'Demo — DDollar spent on Founder OS AI',
            },
          });
          created.ledgerEntriesCreated += 1;
        }
      }
    }

    const founderUsers = demoUsers.filter((u) => u.role === 'founder');
    const scoutUsers = demoUsers.filter((u) => u.role !== 'founder');

    for (let p = 0; p < projectBlueprints.length; p += 1) {
      const blueprint = projectBlueprints[p]!;
      const slug = `${DEMO_SLUG_PREFIX}${blueprint.slugSuffix}`;
      const founderUser = founderUsers[p % founderUsers.length];
      if (!founderUser) continue;

      const founderSlug = `${DEMO_SLUG_PREFIX}founder-${String(founderUser.index).padStart(3, '0')}`;
      const founder = await this.prisma.founder.upsert({
        where: { slug: founderSlug },
        create: {
          slug: founderSlug,
          userId: founderUser.id,
          name: `demo_founder_${String(founderUser.index).padStart(3, '0')}`,
          bio: `[Demo] Building ${blueprint.name} in public on Founder OS.`,
          presenceLevel: FounderPresenceLevel.VERIFIED_BUILDER,
          journeyStage: FounderJourneyStage.MVP,
          publicBuildingSince: new Date(Date.now() - 30 * 86400000),
          buildStreakDays: 7 + (p % 14),
        },
        update: {
          userId: founderUser.id,
          name: `demo_founder_${String(founderUser.index).padStart(3, '0')}`,
          bio: `[Demo] Building ${blueprint.name} in public on Founder OS.`,
        },
      });
      if (founder.createdAt.getTime() === founder.updatedAt.getTime()) {
        created.foundersCreated += 1;
      }

      await this.prisma.founderVerification.upsert({
        where: { founderId_type: { founderId: founder.id, type: 'GITHUB' } },
        create: { founderId: founder.id, type: 'GITHUB', verified: true, verifiedAt: new Date(), notes: 'Demo verification' },
        update: { verified: true, verifiedAt: new Date() },
      });

      const categoryId = categoryBySlug[blueprint.categorySlug];
      const project = await this.prisma.project.upsert({
        where: { slug },
        create: {
          slug,
          name: blueprint.name,
          ticker: blueprint.ticker,
          summary: `[Demo] Paper-traded conviction sandbox for ${blueprint.name}.`,
          description: `${blueprint.name} is synthetic demo data for Founder OS E2E testing. Slug prefix \`${DEMO_SLUG_PREFIX}\` — safe to reset.`,
          chainId: chain.id,
          categoryId,
          founderId: founder.id,
          approved: true,
          source: 'CURATED',
          lifecycleStage: blueprint.stage,
          launchReadiness: 20 + (p * 7) % 80,
          bubbleScore: 10 + (p * 13) % 90,
          featured: p < 3,
          socials: {
            create: {
              twitterUrl: `https://x.com/demo_${blueprint.slugSuffix}`,
              githubUrl: `https://github.com/demo-org/${blueprint.slugSuffix}`,
            },
          },
          metrics: {
            create: {
              priceUsd: 0.01 + p * 0.002,
              volume24h: 1000 + p * 500,
              liquidity: 5000 + p * 1000,
            },
          },
        },
        update: {
          name: blueprint.name,
          lifecycleStage: blueprint.stage,
          founderId: founder.id,
          launchReadiness: 20 + (p * 7) % 80,
          approved: true,
        },
      });
      if (project.createdAt.getTime() === project.updatedAt.getTime()) {
        created.projectsCreated += 1;
      }

      if (blueprint.withRaise) {
        const existingRaise = await this.prisma.simulatedRaise.findFirst({
          where: { projectId: project.id, status: SimulatedRaiseStatus.ACTIVE },
        });
        let raiseId = existingRaise?.id;
        if (!existingRaise) {
          const raise = await this.prisma.simulatedRaise.create({
            data: {
              projectId: project.id,
              goalUsd: blueprint.raiseGoalUsd,
              status: SimulatedRaiseStatus.ACTIVE,
              communityTokenPercent: 10 + (p % 5),
              durationDays: 14,
              startsAt: new Date(Date.now() - 5 * 86400000),
              endsAt: new Date(Date.now() + 9 * 86400000),
            },
          });
          raiseId = raise.id;
          created.raisesCreated += 1;
        }

        if (raiseId) {
          const allocators = scoutUsers.slice(0, Math.min(6, scoutUsers.length));
          for (let a = 0; a < allocators.length; a += 1) {
            const allocator = allocators[a]!;
            const amountUsd = 250 + ((p + a) * 173) % 2500;
            await this.prisma.raiseAllocation.upsert({
              where: { raiseId_userId: { raiseId, userId: allocator.id } },
              create: {
                raiseId,
                userId: allocator.id,
                amountUsd,
                burnedUsd: Math.round(amountUsd * 0.01 * 100) / 100,
                slotReserved: true,
              },
              update: { amountUsd },
            });
            created.allocationsCreated += 1;

            const dedupeKey = `demo-raise-${raiseId}-${allocator.id}`;
            const existingEvent = await this.prisma.founderEvent.findUnique({ where: { dedupeKey } });
            if (!existingEvent) {
              await this.prisma.founderEvent.create({
                data: {
                  founderId: founder.id,
                  projectId: project.id,
                  userId: allocator.id,
                  type: FounderEventType.RAISE_ALLOCATION,
                  source: 'raise-room',
                  title: `$${amountUsd.toLocaleString()} allocated to Raise Room`,
                  payload: { amountUsd, demo: true },
                  dedupeKey,
                },
              });
              created.eventsCreated += 1;
            }
          }
        }
      }

      const trustReporter = scoutUsers[p % scoutUsers.length];
      if (trustReporter) {
        await this.prisma.projectTrustReport.upsert({
          where: { projectId_userId: { projectId: project.id, userId: trustReporter.id } },
          create: {
            projectId: project.id,
            userId: trustReporter.id,
            category: TRUST_CATEGORIES[p % TRUST_CATEGORIES.length]!,
            voteWeight: 1 + (p % 3),
            comment: '[Demo] Synthetic trust validation signal for smoke tests.',
            rewarded: true,
          },
          update: {
            category: TRUST_CATEGORIES[p % TRUST_CATEGORIES.length]!,
            comment: '[Demo] Synthetic trust validation signal for smoke tests.',
          },
        });
        created.trustReportsCreated += 1;
      }

      const buildPostCount = await this.prisma.founderBuildPost.count({ where: { founderId: founder.id, projectId: project.id } });
      if (buildPostCount === 0) {
        await this.prisma.founderBuildPost.create({
          data: {
            founderId: founder.id,
            projectId: project.id,
            dayNumber: 1 + p,
            headline: `[Demo] Ship log #${p + 1} — ${blueprint.name}`,
            body: 'Synthetic build update for Raise Room live feed and Founder OS dashboard smoke checks.',
          },
        });
        await this.prisma.founderEvent.create({
          data: {
            founderId: founder.id,
            projectId: project.id,
            userId: founderUser.id,
            type: FounderEventType.BUILD_PUBLISHED,
            source: 'demo-seed',
            title: `[Demo] Build log published for ${blueprint.name}`,
            payload: { demo: true },
            dedupeKey: `demo-build-${project.id}-${p}`,
          },
        });
        created.eventsCreated += 1;
      }
    }

    const status = await this.getStatus();
    this.logger.log(`Demo ecosystem seed complete (scale=${scale}, existingUsers=${existingUsers})`);

    return {
      ok: true,
      scale,
      idempotent: existingUsers > 0,
      message:
        existingUsers > 0
          ? 'Demo ecosystem refreshed (upserted demo_* records).'
          : 'Demo ecosystem created.',
      created,
      status,
    };
  }

  async resetDemoData() {
    const demoUserIds = (
      await this.prisma.user.findMany({ where: demoUserWhere(), select: { id: true } })
    ).map((u) => u.id);
    const demoProjectIds = (
      await this.prisma.project.findMany({ where: demoProjectWhere(), select: { id: true } })
    ).map((p) => p.id);
    const demoFounderIds = (
      await this.prisma.founder.findMany({ where: demoFounderWhere(), select: { id: true } })
    ).map((f) => f.id);

    if (demoUserIds.length === 0 && demoProjectIds.length === 0 && demoFounderIds.length === 0) {
      return {
        ok: true,
        message: 'No demo-tagged records found.',
        deleted: { users: 0, projects: 0, founders: 0 },
      };
    }

    await this.prisma.$transaction(async (tx) => {
      if (demoProjectIds.length > 0) {
        await tx.project.deleteMany({ where: { id: { in: demoProjectIds } } });
      }
      if (demoFounderIds.length > 0) {
        await tx.founder.deleteMany({ where: { id: { in: demoFounderIds } } });
      }
      if (demoUserIds.length > 0) {
        await tx.user.deleteMany({ where: { id: { in: demoUserIds } } });
      }
    });

    this.logger.warn(
      `Demo reset removed ${demoUserIds.length} users, ${demoProjectIds.length} projects, ${demoFounderIds.length} founders`,
    );

    return {
      ok: true,
      message: 'Demo-tagged records removed. Real users and projects untouched.',
      deleted: {
        users: demoUserIds.length,
        projects: demoProjectIds.length,
        founders: demoFounderIds.length,
      },
    };
  }

  private async runCheck(name: string, fn: () => Promise<{ passed: boolean; detail: string }>): Promise<SmokeCheck> {
    const start = Date.now();
    try {
      const result = await fn();
      return { name, ...result, durationMs: Date.now() - start };
    } catch (err) {
      return {
        name,
        passed: false,
        detail: err instanceof Error ? err.message : String(err),
        durationMs: Date.now() - start,
      };
    }
  }

  async runSmokeChecks() {
    const checks: SmokeCheck[] = [];

    checks.push(
      await this.runCheck('demo_mode_enabled', async () => ({
        passed: isDemoModeEnabled(),
        detail: isDemoModeEnabled() ? 'DEMO_MODE_ENABLED=true' : 'DEMO_MODE_ENABLED is not true',
      })),
    );

    checks.push(
      await this.runCheck('demo_users_seeded', async () => {
        const count = await this.prisma.user.count({ where: demoUserWhere() });
        return { passed: count >= 10, detail: `${count} demo users (@doxxed.demo)` };
      }),
    );

    checks.push(
      await this.runCheck('projects_list_has_demo', async () => {
        const all = await this.projects.findAll({});
        const demo = all.filter((p) => p.slug.startsWith(DEMO_SLUG_PREFIX));
        return { passed: demo.length >= 5, detail: `${demo.length} demo projects in GET /projects` };
      }),
    );

    checks.push(
      await this.runCheck('raise_room_heatmap', async () => {
        const heatmap = await this.founderDen.getDemandHeatmap();
        const demoRows = heatmap.filter((row) => row.project.slug.startsWith(DEMO_SLUG_PREFIX));
        return {
          passed: demoRows.length > 0,
          detail: `${demoRows.length} demo rows in demand heatmap (${heatmap.length} total active raises)`,
        };
      }),
    );

    const sampleSlug =
      (
        await this.prisma.project.findFirst({
          where: demoProjectWhere(),
          select: { slug: true },
        })
      )?.slug ?? `${DEMO_SLUG_PREFIX}payflow`;

    checks.push(
      await this.runCheck('project_detail_by_slug', async () => {
        const detail = await this.projects.findBySlug(sampleSlug);
        return {
          passed: Boolean(detail?.slug === sampleSlug),
          detail: detail ? `Loaded ${detail.slug} (${detail.name})` : `Missing project ${sampleSlug}`,
        };
      }),
    );

    checks.push(
      await this.runCheck('demo_user_ddollar_balance', async () => {
        const user = await this.prisma.user.findFirst({
          where: demoUserWhere(),
          orderBy: { createdAt: 'asc' },
          select: {
            email: true,
            reputationPoints: true,
            notificationPrefs: true,
            pointLedger: { where: { amount: { gt: 0 } }, select: { amount: true } },
          },
        });
        if (!user) return { passed: false, detail: 'No demo user found' };
        const lifetimeFromLedger = user.pointLedger.reduce((s, row) => s + row.amount, 0);
        const prefs = user.notificationPrefs as { lifetimeContributionEarned?: number } | null;
        const lifetime = prefs?.lifetimeContributionEarned ?? lifetimeFromLedger;
        return {
          passed: user.reputationPoints >= 0 && lifetime >= user.reputationPoints,
          detail: `${user.email}: spendable=${user.reputationPoints} DDollar, lifetime≈${lifetime}`,
        };
      }),
    );

    checks.push(
      await this.runCheck('point_ledger_readable', async () => {
        const demoUser = await this.prisma.user.findFirst({ where: demoUserWhere(), select: { id: true } });
        if (!demoUser) return { passed: false, detail: 'No demo user' };
        const rows = await this.prisma.pointLedger.findMany({
          where: { userId: demoUser.id },
          take: 5,
          orderBy: { createdAt: 'desc' },
        });
        return { passed: rows.length > 0, detail: `${rows.length} ledger rows for sample demo user` };
      }),
    );

    checks.push(
      await this.runCheck('trust_validation_signals', async () => {
        const count = await this.prisma.projectTrustReport.count({
          where: { project: demoProjectWhere() },
        });
        return { passed: count >= 3, detail: `${count} trust reports on demo projects` };
      }),
    );

    checks.push(
      await this.runCheck('simulated_raises_active', async () => {
        const count = await this.prisma.simulatedRaise.count({
          where: { status: SimulatedRaiseStatus.ACTIVE, project: demoProjectWhere() },
        });
        return { passed: count >= 3, detail: `${count} active simulated raises on demo projects` };
      }),
    );

    checks.push(
      await this.runCheck('founder_os_integrations_health', async () => {
        const integrations = await this.founderOs.getIntegrationProviders();
        return {
          passed: Array.isArray(integrations) && integrations.length > 0,
          detail: `${Array.isArray(integrations) ? integrations.length : 0} integration providers exposed`,
        };
      }),
    );

    checks.push(
      await this.runCheck('founder_events_activity_feed', async () => {
        const count = await this.prisma.founderEvent.count({
          where: { OR: [{ project: demoProjectWhere() }, { founder: demoFounderWhere() }] },
        });
        return { passed: count >= 5, detail: `${count} founder events for demo activity feed` };
      }),
    );

    checks.push(
      await this.runCheck('lifecycle_stage_coverage', async () => {
        const stages = await this.prisma.project.findMany({
          where: demoProjectWhere(),
          select: { lifecycleStage: true },
          distinct: ['lifecycleStage'],
        });
        return {
          passed: stages.length >= 5,
          detail: `${stages.length} distinct lifecycle stages across demo projects`,
        };
      }),
    );

    const passed = checks.filter((c) => c.passed).length;
    const failed = checks.filter((c) => !c.passed).length;

    return {
      passed,
      failed,
      total: checks.length,
      ok: failed === 0,
      ranAt: new Date().toISOString(),
      checks,
    };
  }
}
