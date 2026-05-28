/**
 * Copies pump/dump meme images into the API bundle for @Bitbro4crypto X posts.
 *
 * Prefer clean source folders (numbers already removed):
 *   ~/Downloads/PUMP  and  ~/Downloads/dump
 *
 * Usage: node scripts/prepare-x-share-images.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const downloads = path.join(process.env.USERPROFILE ?? process.env.HOME ?? '', 'Downloads');
const outPump = path.join(root, 'apps', 'api', 'src', 'assets', 'x-share', 'pump');
const outDump = path.join(root, 'apps', 'api', 'src', 'assets', 'x-share', 'dump');
const publicPump = path.join(root, 'apps', 'web', 'public', 'share', 'pump');
const publicDump = path.join(root, 'apps', 'web', 'public', 'share', 'dump');

const sources = [
  {
    dirs: [
      path.join(downloads, 'PUMP'),
      path.join(downloads, 'crypto_pump_20_images'),
    ],
    outs: [outPump, publicPump],
    prefix: 'pump',
  },
  {
    dirs: [
      path.join(downloads, 'dump'),
      path.join(downloads, 'crypto_dump_20_images'),
    ],
    outs: [outDump, publicDump],
    prefix: 'dump',
  },
];

function resolveSourceDir(candidates) {
  for (const dir of candidates) {
    if (!fs.existsSync(dir)) continue;
    const hasImages = fs.readdirSync(dir).some((f) => /\.(png|jpe?g|webp)$/i.test(f));
    if (hasImages) return dir;
  }
  return null;
}

function processFolder({ dirs, outs, prefix }) {
  const dir = resolveSourceDir(dirs);
  if (!dir) {
    console.warn(`[prepare-x-share] Skip — no folder found among:\n  ${dirs.join('\n  ')}`);
    return 0;
  }

  for (const out of outs) {
    fs.mkdirSync(out, { recursive: true });
    for (const file of fs.readdirSync(out)) {
      if (/\.(png|jpe?g|webp)$/i.test(file)) fs.unlinkSync(path.join(out, file));
    }
  }

  const files = fs
    .readdirSync(dir)
    .filter((f) => /\.(png|jpe?g|webp)$/i.test(f))
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));

  let index = 0;
  for (const file of files) {
    index += 1;
    const src = path.join(dir, file);
    const ext = path.extname(file).toLowerCase() || '.png';
    const destName = `${prefix}_${String(index).padStart(2, '0')}${ext}`;
    for (const out of outs) {
      fs.copyFileSync(src, path.join(out, destName));
    }
    console.log(`  ${destName} ← ${file}`);
  }

  console.log(`  (source: ${dir} → ${outs.length} destinations)`);
  return index;
}

console.log('[prepare-x-share] Building X share image library…');
let total = 0;
for (const source of sources) {
  console.log(`\n→ ${source.prefix}`);
  total += processFolder(source);
}
console.log(`\n[prepare-x-share] Done — ${total} clean images in apps/api/src/assets/x-share/`);
