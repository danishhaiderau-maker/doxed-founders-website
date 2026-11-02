import { NextResponse } from 'next/server';
import {
  backendApiGonePayload,
  isBackendApiRetired,
} from '@/lib/backend-api-retired';

export const dynamic = 'force-static';
export const revalidate = false;

function goneResponse() {
  return NextResponse.json(backendApiGonePayload(), {
    status: 410,
    headers: {
      'Cache-Control': 'public, max-age=300',
      'X-Backend-Api': 'retired',
    },
  });
}

function notRetiredPassthrough() {
  return NextResponse.json(
    {
      ok: false,
      code: 'BACKEND_PROXY_DISABLED',
      message:
        'Nest API proxy is not active on this Vercel deployment. Start the local API or set NEXT_PUBLIC_BACKEND_API_RETIRED=0 only for full-stack lab use.',
    },
    { status: 503 },
  );
}

async function handle() {
  if (isBackendApiRetired()) return goneResponse();
  return notRetiredPassthrough();
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
export const HEAD = handle;
export const OPTIONS = handle;
