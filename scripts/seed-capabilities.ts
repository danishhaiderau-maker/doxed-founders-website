/**
 * Seed the Capability Registry with the proposal values for every provider/model
 * we know about today. Safe to re-run — uses upserts and never overwrites the
 * reputation fields (successRate / retryRate / sampleCount) once they've been
 * touched by the Learning Engine.
 *
 * Usage:
 *   npm run db:seed:capabilities
 *   tsx scripts/seed-capabilities.ts
 */
import { PrismaClient } from '@prisma/client';
import { CAPABILITY_SEEDS } from '../apps/api/src/capability-registry/capability-registry.seeds';

const prisma = new PrismaClient();

async function main() {
  console.log(`\n=== Capability Registry seed (${CAPABILITY_SEEDS.length} rows) ===\n`);

  let created = 0;
  let updated = 0;

  for (const seed of CAPABILITY_SEEDS) {
    const existing = await prisma.capability.findUnique({
      where: { provider_model: { provider: seed.provider, model: seed.model } },
    });

    // Never clobber reputation on re-seed — only display + capability + cost
    // + intent score fields are mutable here.
    const data = {
      displayName: seed.displayName,
      isActive: seed.isActive ?? true,
      toolUse: seed.toolUse ?? false,
      jsonMode: seed.jsonMode ?? false,
      largeContext: seed.largeContext ?? false,
      largeContextWindow: seed.largeContextWindow ?? null,
      vision: seed.vision ?? false,
      streaming: seed.streaming ?? true,
      inputCostPer1M: seed.inputCostPer1M,
      outputCostPer1M: seed.outputCostPer1M,
      latencyP50Ms: seed.latencyP50Ms,
      codeScore: seed.codeScore ?? 0.5,
      reasoningScore: seed.reasoningScore ?? 0.5,
      simpleQaScore: seed.simpleQaScore ?? 0.5,
      agentScore: seed.agentScore ?? 0.5,
      visionScore: seed.visionScore ?? 0.0,
    };

    await prisma.capability.upsert({
      where: { provider_model: { provider: seed.provider, model: seed.model } },
      create: {
        provider: seed.provider,
        model: seed.model,
        ...data,
        // Fresh reputation defaults on first creation.
        successRate: 1.0,
        retryRate: 0.0,
        sampleCount: 0,
      },
      update: data,
    });

    if (existing) updated += 1;
    else created += 1;
  }

  const total = await prisma.capability.count();
  const byProvider = await prisma.capability.groupBy({
    by: ['provider'],
    _count: true,
    orderBy: { provider: 'asc' },
  });

  console.log(`Created ${created}, updated ${updated}. Total rows: ${total}`);
  console.log('\nBy provider:');
  for (const row of byProvider) {
    console.log(`  ${row.provider.padEnd(12)} ${row._count}`);
  }
  console.log(
    '\nNote: Intent scores and costs are PROPOSAL values for founder review.\n' +
      'The Learning Engine (Phase 4) will refine successRate / retryRate from traffic.\n',
  );
}

main()
  .catch((err) => {
    console.error('Seed failed:', err);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
