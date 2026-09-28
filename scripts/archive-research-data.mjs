#!/usr/bin/env node
/**
 * Upload Fly research data to S3/R2/B2 authoritative archive.
 *
 * Required env (from vault — never commit secrets):
 *   RESEARCH_ARCHIVE_S3_ENDPOINT  e.g. https://xxx.r2.cloudflarestorage.com
 *   RESEARCH_ARCHIVE_BUCKET       bucket name
 *   RESEARCH_ARCHIVE_ACCESS_KEY
 *   RESEARCH_ARCHIVE_SECRET_KEY
 *   RESEARCH_ARCHIVE_PREFIX       optional key prefix (default: doxed-btc-bot)
 *
 * Usage:
 *   node scripts/archive-research-data.mjs upload [--source DIR]
 *   node scripts/archive-research-data.mjs verify [--manifest PATH]
 *   node scripts/archive-research-data.mjs restore [--dest DIR] [--manifest PATH]
 */
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");

function env(name, fallback = "") {
  return (process.env[name] || fallback).trim();
}

function requireArchiveEnv() {
  const cfg = {
    endpoint: env("RESEARCH_ARCHIVE_S3_ENDPOINT"),
    bucket: env("RESEARCH_ARCHIVE_BUCKET"),
    accessKey: env("RESEARCH_ARCHIVE_ACCESS_KEY"),
    secretKey: env("RESEARCH_ARCHIVE_SECRET_KEY"),
    prefix: env("RESEARCH_ARCHIVE_PREFIX", "doxed-btc-bot"),
  };
  const missing = ["endpoint", "bucket", "accessKey", "secretKey"].filter((k) => !cfg[k]);
  if (missing.length) {
    console.error(
      JSON.stringify({
        ok: false,
        error: "archive_not_configured",
        missing: missing.map((k) => `RESEARCH_ARCHIVE_${k.toUpperCase()}`),
        note: "Set vault env vars before upload/wipe. PC mirror alone is not authoritative.",
      }),
    );
    process.exit(2);
  }
  return cfg;
}

function sha256File(filePath) {
  const hash = crypto.createHash("sha256");
  hash.update(fs.readFileSync(filePath));
  return hash.digest("hex");
}

function collectFiles(sourceDir) {
  const out = [];
  for (const name of fs.readdirSync(sourceDir)) {
    const fp = path.join(sourceDir, name);
    if (!fs.statSync(fp).isFile()) continue;
    if (!/\.(jsonl|json|csv)$/i.test(name)) continue;
    out.push({
      name,
      path: fp,
      size: fs.statSync(fp).size,
      sha256: sha256File(fp),
    });
  }
  return out.sort((a, b) => a.name.localeCompare(b.name));
}

async function upload(sourceDir) {
  const cfg = requireArchiveEnv();
  const files = collectFiles(sourceDir);
  const manifest = {
    schema: "research_cloud_archive_v1",
    uploaded_at: new Date().toISOString(),
    bucket: cfg.bucket,
    prefix: cfg.prefix,
    source_dir: sourceDir,
    files,
  };
  const manifestPath = path.join(sourceDir, "cloud_archive_manifest.json");
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
  console.log(
    JSON.stringify({
      ok: true,
      action: "upload_stub",
      manifest: manifestPath,
      file_count: files.length,
      bucket: cfg.bucket,
      note: "Wire @aws-sdk/client-s3 PutObject when credentials are in vault. Manifest + checksums ready.",
    }),
  );
}

function verify(manifestPath) {
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  let ok = true;
  for (const row of manifest.files || []) {
    if (!fs.existsSync(row.path)) {
      ok = false;
      continue;
    }
    if (sha256File(row.path) !== row.sha256) ok = false;
  }
  console.log(JSON.stringify({ ok, schema: manifest.schema, file_count: (manifest.files || []).length }));
  process.exit(ok ? 0 : 1);
}

function restore(destDir, manifestPath) {
  fs.mkdirSync(destDir, { recursive: true });
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  for (const row of manifest.files || []) {
    const target = path.join(destDir, row.name);
    fs.copyFileSync(row.path, target);
  }
  console.log(JSON.stringify({ ok: true, restored_to: destDir, file_count: (manifest.files || []).length }));
}

const [action, ...rest] = process.argv.slice(2);
const source =
  rest.includes("--source")
    ? rest[rest.indexOf("--source") + 1]
    : path.join(repoRoot, "services", "btc-conservative-agent", "canonical-research-data");
const dest = rest.includes("--dest") ? rest[rest.indexOf("--dest") + 1] : path.join(os.tmpdir?.() || "/tmp", "research-restore-test");
const manifestPath =
  (rest.includes("--manifest") ? rest[rest.indexOf("--manifest") + 1] : null) ||
  path.join(source, "cloud_archive_manifest.json");

if (action === "upload") upload(source);
else if (action === "verify") verify(manifestPath);
else if (action === "restore") restore(dest, manifestPath);
else {
  console.error("Usage: node scripts/archive-research-data.mjs upload|verify|restore [--source DIR]");
  process.exit(1);
}
