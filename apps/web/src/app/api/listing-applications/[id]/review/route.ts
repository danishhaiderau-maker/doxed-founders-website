import { scoreFounderVerification } from '@dcf/utils';
import { ListingStatus } from '@prisma/client';
import { getServerSession } from 'next-auth';
import { NextRequest, NextResponse } from 'next/server';
import { authOptions } from '@/lib/auth-options';
import { getServerApiBase } from '@/lib/api-base';
import { buildListingApplicationUpdates, prisma } from '@/lib/prisma';

export async function PATCH(
  req: NextRequest,
  context: { params: Promise<{ id: string }> },
) {
  const session = await getServerSession(authOptions);
  if (!session?.user || session.user.role !== 'ADMIN') {
    return NextResponse.json({ message: 'Unauthorized', statusCode: 401 }, { status: 401 });
  }

  const { id } = await context.params;
  const body = (await req.json()) as Record<string, unknown>;
  const status = body.status;
  const reviewNotes = body.reviewNotes;

  if (status !== 'APPROVED' && status !== 'REJECTED') {
    return NextResponse.json({ message: 'Invalid review status' }, { status: 400 });
  }

  const { status: _s, reviewNotes: _n, ...fieldUpdates } = body;
  const updates = buildListingApplicationUpdates(fieldUpdates);

  if (Object.keys(updates).length > 0) {
    if (!process.env.DATABASE_URL) {
      return NextResponse.json(
        { message: 'DATABASE_URL is not configured for admin review updates' },
        { status: 500 },
      );
    }

    const existing = await prisma.listingApplication.findUnique({ where: { id } });
    if (!existing) {
      return NextResponse.json({ message: 'Listing application not found' }, { status: 404 });
    }
    if (existing.status !== ListingStatus.PENDING) {
      return NextResponse.json(
        { message: 'Application has already been reviewed' },
        { status: 400 },
      );
    }

    const merged = { ...existing, ...updates };
    const verification = scoreFounderVerification({
      founderName: merged.founderName as string | null,
      founderLinkedIn: merged.founderLinkedIn as string | null,
      founderGithub: merged.founderGithub as string | null,
      companyDetails: merged.companyDetails as string | null,
      founderVideoUrl: merged.founderVideoUrl as string | null,
      founderInterviewUrl: merged.founderInterviewUrl as string | null,
    });

    await prisma.listingApplication.update({
      where: { id },
      data: {
        ...updates,
        verificationScore: verification.score,
        verificationCriteria: verification.criteria,
      },
    });
  }

  const authHeader = req.headers.get('authorization');
  const apiBase = getServerApiBase();
  const upstream = await fetch(`${apiBase}/api/listing-applications/${id}/review`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      ...(authHeader ? { Authorization: authHeader } : {}),
    },
    body: JSON.stringify({
      status,
      reviewNotes: typeof reviewNotes === 'string' ? reviewNotes : undefined,
    }),
  });

  const payload = await upstream.text();
  return new NextResponse(payload, {
    status: upstream.status,
    headers: { 'Content-Type': 'application/json' },
  });
}
