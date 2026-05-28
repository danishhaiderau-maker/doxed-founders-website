/**
 * Rotate the platform admin password in the database.
 * Usage: SEED_ADMIN_PASSWORD='your-new-password' DATABASE_URL='...' node scripts/rotate-admin-password.mjs
 */
import bcrypt from 'bcrypt';
import { PrismaClient } from '@prisma/client';

const ADMIN_EMAIL = 'admin@doxedcryptofounder.local';
const newPassword = process.env.SEED_ADMIN_PASSWORD?.trim();

if (!newPassword || newPassword.length < 12) {
  console.error('Set SEED_ADMIN_PASSWORD (min 12 characters).');
  process.exit(1);
}

if (!process.env.DATABASE_URL) {
  console.error('Set DATABASE_URL to your production Neon connection string.');
  process.exit(1);
}

const prisma = new PrismaClient();
const passwordHash = await bcrypt.hash(newPassword, 12);

const user = await prisma.user.update({
  where: { email: ADMIN_EMAIL },
  data: { passwordHash, role: 'ADMIN' },
  select: { email: true, role: true },
});

console.log(`Admin password updated for ${user.email} (${user.role}).`);
await prisma.$disconnect();
