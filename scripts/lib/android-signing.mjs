/**
 * Release signing for Android APK (Play Protect–friendly vs debug builds).
 * Keystore lives in ../doxedcryptofounder-secrets/vault/ (never commit).
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, '../..');
const vault = path.join(path.dirname(root), 'doxedcryptofounder-secrets', 'vault');

const KEYSTORE_NAME = 'android-release.keystore';
const KEY_ALIAS = 'doxxedcrypto';

/** CI / GitHub Actions: decode ANDROID_RELEASE_KEYSTORE_B64 into a temp keystore. */
export function loadSigningFromEnv() {
  const b64 = process.env.ANDROID_RELEASE_KEYSTORE_B64?.trim();
  const storePass = process.env.ANDROID_KEYSTORE_PASSWORD?.trim();
  if (!b64 || !storePass) return null;

  const keystorePath =
    process.env.ANDROID_KEYSTORE_PATH?.trim() ||
    path.join(process.env.RUNNER_TEMP || vault, KEYSTORE_NAME);
  fs.mkdirSync(path.dirname(keystorePath), { recursive: true });
  fs.writeFileSync(keystorePath, Buffer.from(b64, 'base64'));

  return {
    keystorePath,
    storePass,
    keyPass: process.env.ANDROID_KEY_PASSWORD?.trim() || storePass,
    alias: process.env.ANDROID_KEY_ALIAS?.trim() || KEY_ALIAS,
  };
}

function readDotEnv(filePath) {
  const map = {};
  if (!fs.existsSync(filePath)) return map;
  for (const line of fs.readFileSync(filePath, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx < 1) continue;
    const k = trimmed.slice(0, idx).trim();
    let v = trimmed.slice(idx + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    if (v) map[k] = v;
  }
  return map;
}

function resolveJavaKeytool() {
  const toolsDir = path.join(root, '.tools');
  for (const jdk of ['jdk-21', 'jdk-17']) {
    const bin =
      process.platform === 'win32'
        ? path.join(toolsDir, jdk, 'bin', 'keytool.exe')
        : path.join(toolsDir, jdk, 'bin', 'keytool');
    if (fs.existsSync(bin)) return bin;
  }
  return 'keytool';
}

export function ensureAndroidReleaseKeystore() {
  const fromEnv = loadSigningFromEnv();
  if (fromEnv) return fromEnv;

  fs.mkdirSync(vault, { recursive: true });
  const envPath = path.join(vault, '.env.android');
  const env = readDotEnv(envPath);
  const keystorePath = env.ANDROID_KEYSTORE_PATH?.trim() || path.join(vault, KEYSTORE_NAME);
  let storePass = env.ANDROID_KEYSTORE_PASSWORD?.trim();
  let keyPass = env.ANDROID_KEY_PASSWORD?.trim() || storePass;
  const alias = env.ANDROID_KEY_ALIAS?.trim() || KEY_ALIAS;

  if (fs.existsSync(keystorePath) && storePass) {
    return { keystorePath, storePass, keyPass, alias };
  }

  if (!storePass) {
    storePass = `dcf-android-${Date.now().toString(36)}`;
    keyPass = storePass;
    const lines = [
      '# Android release signing — keep secret, back up the keystore file.',
      `ANDROID_KEYSTORE_PATH=${keystorePath.replace(/\\/g, '/')}`,
      `ANDROID_KEYSTORE_PASSWORD=${storePass}`,
      `ANDROID_KEY_PASSWORD=${keyPass}`,
      `ANDROID_KEY_ALIAS=${alias}`,
      '',
    ];
    fs.writeFileSync(envPath, lines.join('\n'), 'utf8');
    console.log(`Created ${envPath} — back up keystore + passwords securely.`);
  }

  if (!fs.existsSync(keystorePath)) {
    const keytool = resolveJavaKeytool();
    console.log(`Generating release keystore: ${keystorePath}`);
    const result = spawnSync(
      keytool,
      [
        '-genkeypair',
        '-v',
        '-keystore',
        keystorePath,
        '-alias',
        alias,
        '-keyalg',
        'RSA',
        '-keysize',
        '2048',
        '-validity',
        '10000',
        '-storepass',
        storePass,
        '-keypass',
        keyPass,
        '-dname',
        'CN=Doxxed Crypto Mobile, OU=Engineering, O=Doxxed Crypto, C=US',
      ],
      { stdio: 'inherit', shell: process.platform === 'win32' },
    );
    if (result.status !== 0) {
      throw new Error('keytool failed — install JDK or set ANDROID_KEYSTORE_PATH in vault/.env.android');
    }
  }

  return { keystorePath, storePass, keyPass, alias };
}

export function writeGradleKeystoreProperties(androidDir, signing) {
  const propsPath = path.join(androidDir, 'keystore.properties');
  const storeFile = path.relative(androidDir, signing.keystorePath).replace(/\\/g, '/');
  const content = [
    `storeFile=${storeFile}`,
    `storePassword=${signing.storePass}`,
    `keyAlias=${signing.alias}`,
    `keyPassword=${signing.keyPass}`,
    '',
  ].join('\n');
  fs.writeFileSync(propsPath, content, 'utf8');
  return propsPath;
}

export function versionCodeFromSemver(version) {
  const m = /^(\d+)\.(\d+)\.(\d+)/.exec(version);
  if (!m) return 1;
  return Number(m[1]) * 10_000 + Number(m[2]) * 100 + Number(m[3]);
}
