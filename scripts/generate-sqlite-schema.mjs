import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.join(root, 'prisma', 'schema.prisma');
const target = path.join(root, 'prisma', 'schema.sqlite.prisma');
const dbFile = process.env.SQLITE_DB?.trim() || 'dev.db';

let schema = fs.readFileSync(source, 'utf8');
schema = schema.replace(
  /provider = "postgresql"\s*\n\s*url\s*=\s*env\("DATABASE_URL"\)/,
  `provider = "sqlite"\n  url      = "file:./${dbFile}"`,
);
schema = schema.replace(/\s@db\.\w+(?:\([^)]*\))?/g, '');

fs.writeFileSync(target, schema);
console.log(`Generated prisma/schema.sqlite.prisma (SQLite file: ${dbFile})`);
