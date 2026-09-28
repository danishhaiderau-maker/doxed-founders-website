import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { FOUNDER_IDE_WINDOWS_DOWNLOAD_URL } from '../lib/founder-ide-download';

const read = (name: string) => readFileSync(new URL(name, import.meta.url), 'utf8');

test('site nav wraps instead of overflowing and only shows desktop groups above 1024px', () => {
  const nav = read('./site-nav.tsx');

  assert.match(nav, /ref=\{navRef\} className="relative flex min-w-0 max-w-full flex-auto flex-col/);
  assert.doesNotMatch(nav, /ref=\{navRef\} className="[^"]*\bflex-1\b/);
  assert.match(nav, /<nav className="[^"]*\bflex-wrap\b[^"]*\bjustify-end\b/);

  assert.match(nav, /hidden items-center gap-0\.5 min-\[1025px\]:flex/);
  assert.match(nav, /hidden items-center gap-2 pl-2 min-\[1025px\]:flex/);
  assert.doesNotMatch(nav, /\b(md|lg):flex\b/);
  assert.match(nav, /fixed inset-0 z-\[120\] min-\[1025px\]:hidden/);
  assert.match(nav, /overflow-x-auto pb-1 min-\[1025px\]:hidden/);
});

test('section dropdown panels anchor to the trigger right edge and clamp to the viewport', () => {
  const nav = read('./site-nav.tsx');
  assert.match(nav, /'absolute right-0 top-full z-\[110\] mt-1\.5 w-\[min\(22rem,calc\(100vw-2rem\)\)\]/);
  assert.doesNotMatch(nav, /absolute left-0 top-full z-\[110\]/);
});

test('download app menu offers the public Founder IDE Windows installer', () => {
  const launcher = read('./download-app-launcher.tsx');
  const founderIde = read('../app/founder-ide/page.tsx');

  assert.equal(
    FOUNDER_IDE_WINDOWS_DOWNLOAD_URL,
    'https://github.com/danishhaiderau-maker/founder-ide-releases/releases/latest',
  );
  assert.match(launcher, /href=\{FOUNDER_IDE_WINDOWS_DOWNLOAD_URL\}/);
  assert.match(launcher, /Windows desktop/);
  assert.match(launcher, /absolute right-0 top-full[^"]*w-\[min\(15rem,calc\(100vw-2rem\)\)\]/);
  assert.match(founderIde, /href=\{FOUNDER_IDE_WINDOWS_DOWNLOAD_URL\}/);

  for (const source of [launcher, founderIde]) {
    assert.doesNotMatch(source, /founder-next\/releases/);
  }
});
