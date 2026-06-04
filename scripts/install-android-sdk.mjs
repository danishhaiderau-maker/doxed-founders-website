#!/usr/bin/env node
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const sdkRoot = path.join(root, '.tools/android-sdk');
const jdk21 = path.join(root, '.tools/jdk-21');
const jdk17 = path.join(root, '.tools/jdk-17');
const jdkHome = fs.existsSync(path.join(jdk21, 'bin', 'java.exe')) ? jdk21 : jdk17;
const sdkmanager =
  process.platform === 'win32'
    ? path.join(sdkRoot, 'cmdline-tools/latest/bin/sdkmanager.bat')
    : path.join(sdkRoot, 'cmdline-tools/latest/bin/sdkmanager');

if (!fs.existsSync(sdkmanager)) {
  console.error('sdkmanager not found. Run bootstrap-android-sdk.ps1 first.');
  process.exit(1);
}

const env = {
  ...process.env,
  JAVA_HOME: jdkHome,
  ANDROID_HOME: sdkRoot,
  ANDROID_SDK_ROOT: sdkRoot,
};

function runWithYes(args) {
  return new Promise((resolve, reject) => {
    const quotedArgs = args.map((a) => (/\s/.test(a) ? `"${a}"` : a)).join(' ');
    const command =
      process.platform === 'win32'
        ? `"${sdkmanager}" ${quotedArgs}`
        : `${sdkmanager} ${quotedArgs}`;
    const child = spawn(command, [], {
      env,
      shell: true,
      stdio: ['pipe', 'inherit', 'inherit'],
    });
    const yes = Buffer.from('y\n'.repeat(80));
    child.stdin.write(yes);
    child.stdin.end();
    child.on('close', (code) => (code === 0 ? resolve() : reject(new Error(`exit ${code}`))));
  });
}

try {
  await runWithYes([`--sdk_root=${sdkRoot}`, '--licenses']);
} catch {
  // continue — package install also accepts licenses interactively
}
await runWithYes([
  `--sdk_root=${sdkRoot}`,
  'platform-tools',
  'platforms;android-35',
  'build-tools;35.0.0',
]);

console.log('Android SDK packages installed.');
