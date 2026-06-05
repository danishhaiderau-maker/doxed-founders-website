#!/usr/bin/env node
/**
 * Build Doxxed Crypto Android APK (Capacitor WebView → doxxedcrypto.digital).
 * Release-signed when vault/.env.android + keystore exist (Play Protect friendly).
 * Usage: node scripts/pack-android-app.mjs
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  ensureAndroidReleaseKeystore,
  versionCodeFromSemver,
  writeGradleKeystoreProperties,
} from './lib/android-signing.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, '..');
const mobileDir = path.join(root, 'apps/mobile-android');
const androidDir = path.join(mobileDir, 'android');
const releaseDir = path.join(mobileDir, 'release');
const publicApk = path.join(root, 'apps/web/public/downloads/doxxedcrypto-android.apk');
const version = JSON.parse(
  fs.readFileSync(path.join(mobileDir, 'package.json'), 'utf8'),
).version;
const toolsDir = path.join(root, '.tools');
const localJdk21 = path.join(toolsDir, 'jdk-21');
const localJdk17 = path.join(toolsDir, 'jdk-17');
const javaExe = process.platform === 'win32' ? 'java.exe' : 'java';
const localJdk = fs.existsSync(path.join(localJdk21, 'bin', javaExe))
  ? localJdk21
  : localJdk17;
const localSdk = path.join(toolsDir, 'android-sdk');

function applyLocalSdkEnv() {
  const javaBin =
    process.platform === 'win32'
      ? path.join(localJdk, 'bin', 'java.exe')
      : path.join(localJdk, 'bin', 'java');
  if (!fs.existsSync(javaBin)) return false;
  process.env.JAVA_HOME = localJdk;
  process.env.ANDROID_HOME = localSdk;
  process.env.ANDROID_SDK_ROOT = localSdk;
  const pathBits = [
    path.join(localJdk, 'bin'),
    path.join(localSdk, 'platform-tools'),
    path.join(localSdk, 'cmdline-tools', 'latest', 'bin'),
  ];
  process.env.PATH = [...pathBits, process.env.PATH].filter(Boolean).join(path.delimiter);
  return true;
}

function acceptAndroidLicenses() {
  const licensesScript = path.join(root, 'scripts/accept-android-licenses.ps1');
  if (process.platform === 'win32' && fs.existsSync(licensesScript)) {
    run('powershell', ['-ExecutionPolicy', 'Bypass', '-File', licensesScript]);
  }
}

function resolveSdkRoot() {
  const fromEnv = process.env.ANDROID_HOME || process.env.ANDROID_SDK_ROOT;
  if (fromEnv && fs.existsSync(fromEnv)) return fromEnv;
  if (fs.existsSync(localSdk)) return localSdk;
  return null;
}

function ensureLocalSdk() {
  const sdkRoot = resolveSdkRoot();
  if (sdkRoot && fs.existsSync(path.join(sdkRoot, 'platforms', 'android-35'))) {
    process.env.ANDROID_HOME = sdkRoot;
    process.env.ANDROID_SDK_ROOT = sdkRoot;
    return;
  }

  const sdkmanager =
    process.platform === 'win32'
      ? path.join(localSdk, 'cmdline-tools', 'latest', 'bin', 'sdkmanager.bat')
      : path.join(localSdk, 'cmdline-tools', 'latest', 'bin', 'sdkmanager');
  if (fs.existsSync(path.join(localSdk, 'platforms', 'android-35'))) return;
  if (fs.existsSync(sdkmanager) && !fs.existsSync(path.join(localSdk, 'platforms', 'android-35'))) {
    run('node', ['scripts/install-android-sdk.mjs'], { cwd: root });
    return;
  }
  if (fs.existsSync(sdkmanager)) return;
  if (sdkRoot) {
    console.warn(`ANDROID_HOME=${sdkRoot} but platform android-35 missing — install via sdkmanager on CI.`);
    return;
  }
  const bootstrap =
    process.platform === 'win32'
      ? path.join(root, 'scripts/bootstrap-android-sdk.ps1')
      : path.join(root, 'scripts/bootstrap-android-sdk.sh');
  if (!fs.existsSync(bootstrap)) {
    console.error('Android SDK missing. Set ANDROID_HOME or run scripts/bootstrap-android-sdk.ps1');
    process.exit(1);
  }
  if (process.platform === 'win32') {
    run('powershell', ['-ExecutionPolicy', 'Bypass', '-File', bootstrap]);
  } else {
    run('bash', [bootstrap]);
  }
  applyLocalSdkEnv();
}

function run(cmd, cmdArgs, opts = {}) {
  const result = spawnSync(cmd, cmdArgs, {
    stdio: 'inherit',
    shell: opts.shell ?? false,
    cwd: root,
    ...opts,
  });
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

function findApk(dir) {
  const hits = [];
  function walk(d) {
    if (!fs.existsSync(d)) return;
    for (const name of fs.readdirSync(d)) {
      const p = path.join(d, name);
      const st = fs.statSync(p);
      if (st.isDirectory()) walk(p);
      else if (/\.apk$/i.test(name) && !/unsigned|unaligned/i.test(name)) hits.push(p);
    }
  }
  walk(dir);
  return hits.sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs)[0];
}

function prepareAssets() {
  const assetsDir = path.join(mobileDir, 'assets');
  fs.mkdirSync(assetsDir, { recursive: true });
  const iconSrc = path.join(root, 'apps/founder-node/build/icon.png');
  if (!fs.existsSync(iconSrc)) {
    run('node', ['scripts/generate-founder-node-icon.mjs']);
  }
  fs.copyFileSync(iconSrc, path.join(assetsDir, 'icon.png'));
  fs.copyFileSync(iconSrc, path.join(assetsDir, 'splash.png'));
}

function ensureAndroidProject() {
  if (!fs.existsSync(androidDir)) {
    run('npx', ['cap', 'add', 'android'], { cwd: mobileDir });
  }
}

function patchGradleJvmArgs() {
  const props = path.join(androidDir, 'gradle.properties');
  if (!fs.existsSync(props)) return;
  let text = fs.readFileSync(props, 'utf8');
  if (!text.includes('org.gradle.jvmargs')) {
    text += '\norg.gradle.jvmargs=-Xmx2048m -XX:MaxMetaspaceSize=512m\n';
    fs.writeFileSync(props, text);
  }
}

function patchAppVersion() {
  const gradlePath = path.join(androidDir, 'app/build.gradle');
  let text = fs.readFileSync(gradlePath, 'utf8');
  const code = versionCodeFromSemver(version);
  text = text.replace(/versionCode\s+\d+/, `versionCode ${code}`);
  text = text.replace(/versionName\s+"[^"]*"/, `versionName "${version}"`);
  fs.writeFileSync(gradlePath, text);
}

prepareAssets();
if (!process.env.JAVA_HOME) applyLocalSdkEnv();
ensureLocalSdk();
if (!process.env.JAVA_HOME) applyLocalSdkEnv();
run('npm', ['install'], { cwd: mobileDir });
ensureAndroidProject();
run('npx', ['cap', 'sync', 'android'], { cwd: mobileDir });
patchGradleJvmArgs();
patchAppVersion();

let buildType = 'release';
try {
  const signing = ensureAndroidReleaseKeystore();
  writeGradleKeystoreProperties(androidDir, signing);
  console.log('Release signing configured (Play Protect–friendly build).');
} catch (err) {
  console.warn(
    `Release signing unavailable (${err instanceof Error ? err.message : err}) — falling back to debug APK.`,
  );
  buildType = 'debug';
}

const gradlew =
  process.platform === 'win32'
    ? path.join(androidDir, 'gradlew.bat')
    : path.join(androidDir, 'gradlew');
if (!fs.existsSync(gradlew)) {
  console.error('Gradle wrapper missing. Run: npx cap add android');
  process.exit(1);
}

fs.rmSync(releaseDir, { recursive: true, force: true });
fs.mkdirSync(releaseDir, { recursive: true });

const gradleTask = buildType === 'release' ? 'assembleRelease' : 'assembleDebug';
if (process.platform === 'win32') {
  run(`"${gradlew}"`, [gradleTask], { cwd: androidDir, shell: true });
} else {
  run(gradlew, [gradleTask], { cwd: androidDir });
}

const built = findApk(path.join(androidDir, 'app/build/outputs/apk'));
if (!built) {
  console.error('No APK found under android/app/build/outputs/apk');
  process.exit(1);
}

const outName = `Doxxed-Crypto-${version}-android-${buildType}.apk`;
const releaseApk = path.join(releaseDir, outName);
fs.copyFileSync(built, releaseApk);
fs.mkdirSync(path.dirname(publicApk), { recursive: true });
fs.copyFileSync(built, publicApk);

console.log(`\nAPK (${buildType}): ${releaseApk}`);
console.log(`Landing download: ${publicApk}`);
