#!/usr/bin/env node
/**
 * One-time migration: normalize project names that were stored as URLs or raw slugs.
 *
 * Usage:
 *   node scripts/migrate-normalize-project-names.mjs
 *   node scripts/migrate-normalize-project-names.mjs --dry-run
 */
import { PrismaClient } from '@prisma/client';

const dryRun = process.argv.includes('--dry-run');

function normalizeProjectName(raw) {
  if (!raw?.trim()) return '';
  let name = raw.trim();
  if (/^https?:\/\//i.test(name)) {
    try {
      const url = new URL(name);
      name = url.hostname.replace(/^www\./, '').split('.')[0] ?? name;
    } catch {
      name = name.replace(/^https?:\/\//i, '').split(/[/?#]/)[0] ?? name;
    }
  }
  name = name.replace(/^@+/, '').replace(/\.(com|io|xyz|app|co|net|org)$/i, '');
  name = name.replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim();
  if (!name) return raw.trim().slice(0, 120);
  return name
    .split(' ')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ')
    .slice(0, 120);
}

function slugify(text) {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^\w\s-]/g, '')
    .replace(/[\s_-]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

const prisma = new PrismaClient();

async function uniqueSlug(base, excludeId) {
  let slug = slugify(base);
  if (!slug) slug = 'project';
  let candidate = slug;
  let n = 2;
  while (true) {
    const existing = await prisma.project.findFirst({
      where: { slug: candidate, NOT: excludeId ? { id: excludeId } : undefined },
    });
    if (!existing) return candidate;
    candidate = `${slug}-${n++}`;
  }
}

async function main() {
  const projects = await prisma.project.findMany({
    select: { id: true, name: true, slug: true, ticker: true },
  });

  let updated = 0;
  for (const project of projects) {
    const normalized = normalizeProjectName(project.name);
    const looksLikeUrl =
      /^https?:\/\//i.test(project.name) ||
      project.name.includes('dexscreener.com') ||
      project.name.includes('pump.fun');
    if (!looksLikeUrl && normalized === project.name) continue;

    const newSlug = await uniqueSlug(normalized || project.slug, project.id);
    console.log(
      `${dryRun ? '[dry-run] ' : ''}${project.slug} → name="${normalized}" slug="${newSlug}"`,
    );

    if (!dryRun) {
      await prisma.project.update({
        where: { id: project.id },
        data: { name: normalized || project.name, slug: newSlug },
      });
    }
    updated += 1;
  }

  console.log(`Done. ${updated} project(s) ${dryRun ? 'would be ' : ''}updated.`);
}

main()
  .catch((err) => {
    console.error(err);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
