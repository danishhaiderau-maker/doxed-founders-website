import { getServerSession } from 'next-auth';
import { NextRequest, NextResponse } from 'next/server';
import { authOptions } from '@/lib/auth-options';
import { prisma } from '@/lib/prisma';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const MAX_BYTES = 8 * 1024 * 1024; // 8 MB per file

// The FounderVaultItem model is new; some CI build environments don't re-resolve
// the regenerated Prisma types during `next build`'s typecheck pass. Cast to a
// minimal delegate so compilation doesn't depend on the freshly-generated types
// while the runtime client (regenerated on every build) still serves the model.
type VaultItem = {
  id: string;
  name: string;
  mime: string;
  sizeBytes: number;
  category: string;
  dataUrl: string;
  width: number | null;
  height: number | null;
  createdAt: Date;
  indexedForAi: boolean;
};
type VaultDelegate = {
  create(args: { data: Record<string, unknown> }): Promise<VaultItem>;
  findMany(args: {
    where: Record<string, unknown>;
    orderBy: Record<string, unknown>;
    take: number;
    select: Record<string, boolean>;
  }): Promise<VaultItem[]>;
};
const vault = () => (prisma as unknown as { founderVaultItem: VaultDelegate }).founderVaultItem;

function inferCategory(name: string, mime: string): string {
  if (mime.startsWith('image/')) return 'screenshot';
  if (mime === 'application/pdf' || name.endsWith('.pdf')) return 'pdf';
  if (mime.startsWith('audio/')) return 'voice';
  if (mime.startsWith('video/')) return 'video';
  if (name.endsWith('.log') || name.endsWith('.txt')) return 'log';
  return 'document';
}

export async function POST(req: NextRequest) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.id) {
    return NextResponse.json({ message: 'Unauthorized' }, { status: 401 });
  }
  if (!process.env.DATABASE_URL) {
    return NextResponse.json({ message: 'DATABASE_URL not configured' }, { status: 500 });
  }

  let form: FormData;
  try {
    form = await req.formData();
  } catch {
    return NextResponse.json({ message: 'Expected multipart/form-data' }, { status: 400 });
  }

  const files = form.getAll('file');
  if (files.length === 0) {
    return NextResponse.json({ message: 'No file provided' }, { status: 400 });
  }

  const saved: { id: string; name: string; category: string; sizeBytes: number }[] = [];

  for (const entry of files) {
    if (!(entry instanceof File)) continue;
    if (entry.size > MAX_BYTES) {
      return NextResponse.json(
        { message: `File ${entry.name} exceeds 8MB limit` },
        { status: 413 },
      );
    }
    const buf = Buffer.from(await entry.arrayBuffer());
    const dataUrl = `data:${entry.type || 'application/octet-stream'};base64,${buf.toString('base64')}`;
    const category = inferCategory(entry.name, entry.type);
    const item = await vault().create({
      data: {
        userId: session.user.id,
        name: entry.name,
        mime: entry.type || 'application/octet-stream',
        sizeBytes: entry.size,
        category,
        dataUrl,
      },
    });
    saved.push({ id: item.id, name: item.name, category: item.category, sizeBytes: item.sizeBytes });
  }

  return NextResponse.json({ saved, count: saved.length });
}

export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session?.user?.id) {
    return NextResponse.json({ message: 'Unauthorized' }, { status: 401 });
  }
  if (!process.env.DATABASE_URL) {
    return NextResponse.json({ message: 'DATABASE_URL not configured' }, { status: 500 });
  }
  const items = await vault().findMany({
    where: { userId: session.user.id },
    orderBy: { createdAt: 'desc' },
    take: 50,
    select: {
      id: true,
      name: true,
      mime: true,
      category: true,
      sizeBytes: true,
      width: true,
      height: true,
      createdAt: true,
      indexedForAi: true,
    },
  });
  return NextResponse.json({ items, count: items.length });
}
