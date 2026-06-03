/**
 * Build retina / UHD assets from landing-value-grid-v2.png (1536×1024 source).
 * Run: node scripts/optimize-landing-value-grid.mjs
 */
import sharp from 'sharp';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const outDir = join(root, 'apps', 'web', 'public', 'images');
const source = join(outDir, 'landing-value-grid-v2.png');

const widths = [
  { w: 1536, suffix: '1536', primary: true },
  { w: 2560, suffix: '2560' },
  { w: 3840, suffix: '3840' },
];

for (const { w, suffix, primary } of widths) {
  const pipeline = sharp(source)
    .resize(w, null, {
      fit: 'inside',
      withoutEnlargement: false,
      kernel: sharp.kernel.lanczos3,
    })
    .sharpen({ sigma: w > 1536 ? 0.8 : 0.4 });

  const webpPath = join(outDir, `landing-value-grid-${suffix}.webp`);

  await pipeline.clone().webp({ quality: 90, effort: 6 }).toFile(webpPath);

  const meta = await sharp(webpPath).metadata();
  console.log(`✓ ${suffix}: ${meta.width}×${meta.height} → ${webpPath.split(/[/\\]/).pop()}`);

  if (primary) {
    await sharp(source)
      .resize(1536, null, { fit: 'inside', kernel: sharp.kernel.lanczos3 })
      .png({ compressionLevel: 6 })
      .toFile(join(outDir, 'landing-value-grid-primary.png'));
    console.log('✓ primary PNG replaced from v2 source');
  }
}

console.log('\nDone. Point LandingValueGrid at landing-value-grid-3840.webp for large screens.');
