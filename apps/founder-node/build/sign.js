// Custom electron-builder Windows signing script.
//
//  Path A -- OV cert (.fx file):
//     CSC_LINK          = path to the .pfx file
//     CSC_KEY_PASSWORD  = password for the .pfx
//
//  Path B -- EV cert (USB hardware token):
//     SIGNTOOL_SHA1     = SHA-1 thumbprint of the cert on the token
//
// If neither set is present, the file is left unsigned (dev/CI builds).
// signtool.exe is invoked with RFC 3161 timestamping (sha256).

const { execFileSync } = require("child_process");
const fs = require("fs");

const TIMESTAMP_URL = "http://timestamp.digicert.com";

function findSigntool() {
  const candidates = [
    process.env.SIGNTOOL_PATH,
    "C:\\Program Files (x86)\\Windows Kits\\10\\bin\\10.0.22621.0\\x64\\signtool.exe",
    "C:\\Program Files (x86)\\Windows Kits\\10\\bin\\10.0.22000.0\\x64\\signtool.exe",
    "C:\\Program Files (x86)\\Windows Kits\\10\\bin\\10.0.19041.0\\x64\\signtool.exe",
    "C:\\Program Files (x86)\\Windows Kits\\10\\bin\\x64\\signtool.exe",
  ].filter(Boolean);
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  throw new Error("signtool.exe not found. Set SIGNTOOL_PATH or install Windows SDK.");
}

function sign(file) {
  const sha1 = process.env.SIGNTOOL_SHA1;
  const pfx = process.env.CSC_LINK;
  const password = process.env.CSC_KEY_PASSWORD;
  if (!sha1 && !pfx) {
    console.log("[sign] No cert env vars set -- skipping " + file);
    return;
  }
  const signtool = findSigntool();
  const args = ["sign", "/tr", TIMESTAMP_URL, "/td", "sha256", "/fd", "sha256"];
  if (sha1) {
    args.push("/sha1", sha1);
    console.log("[sign] EV path -- signing " + file + " with thumbprint " + sha1);
  } else {
    args.push("/f", pfx);
    if (password) args.push("/p", password);
    console.log("[sign] OV path -- signing " + file + " with " + pfx);
  }
  args.push(file);
  try {
    execFileSync(signtool, args, { stdio: "inherit" });
    console.log("[sign] OK -- " + file);
  } catch (err) {
    console.error("[sign] FAILED for " + file);
    throw err;
  }
}

exports.default = function signHook(filePath) {
  return Promise.resolve(sign(filePath));
};

if (require.main === module) {
  const target = process.argv[2];
  if (!target) {
    console.error("usage: node build/sign.js FILE");
    process.exit(1);
  }
  sign(target);
}
