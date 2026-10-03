/**
 * @deprecated The legacy Founder desktop setup surface. New user-facing flows
 * should use /founder-ide directly. This component remains for onboarding and
 * settings callers that have not migrated yet.
 */
'use client';

import React from 'react';
import { FOUNDER_IDE_WINDOWS_RELEASE } from '@/app/founder-ide/founder-ide-download';

type Props = {
  /** Show numbered setup steps below the release status */
  showInstallGuide?: boolean;
  /** Anchor id for deep-linking from workspace connect wizard */
  sectionId?: string;
  /** Explicit release metadata; primarily useful for deterministic rendering and tests. */
  release?: typeof FOUNDER_IDE_WINDOWS_RELEASE;
};

export function FounderNodeInstallGuide() {
  return (
    <div className='text-xs text-zinc-300'>
      <ol className='list-decimal space-y-2 pl-5'>
        <li>
          Open <strong className='text-white'>Founder IDE setup</strong>. Only use a download when that page shows an
          approved, verified public installer.
        </li>
        <li>
          If Founder IDE is already installed, choose <strong className='text-white'>Pair my device</strong>{' '}
          on the setup page and follow its pairing instructions.
        </li>
        <li>
          In the installed desktop app, enter the pairing code and keep Founder Node running while the initial sync
          completes.
        </li>
        <li>
          Select the intended project and inspect the reported device status. Remote Builder requests require local
          approval; pairing alone does not prove that a build or its Quality checks passed.
        </li>
      </ol>
      <p className='mt-3 text-[11px] text-zinc-500'>
        No installer is linked from this legacy panel unless approved public release metadata is configured.
      </p>
    </div>
  );
}

export function FounderNodeDownloads({
  showInstallGuide = false,
  sectionId = 'founder-node-download',
  release = FOUNDER_IDE_WINDOWS_RELEASE,
}: Props) {
  return (
    <div id={sectionId} className='scroll-mt-24 space-y-4'>
      <div className='rounded-xl border border-emerald-500/30 bg-emerald-950/15 p-4'>
        <div className='flex flex-wrap items-center justify-between gap-3'>
          <div className='min-w-0'>
            <p className='text-sm font-semibold text-emerald-100'>Founder IDE setup</p>
            {release.status === 'available' ? (
              <p className='mt-1 text-xs text-zinc-400'>
                A verified public Windows installer is available from the approved release metadata.
              </p>
            ) : (
              <>
                <p className='mt-1 text-xs text-zinc-400'>
                  A verified public Founder IDE installer has not been published yet.
                </p>
                <p className='mt-1 text-[11px] text-zinc-500'>
                  Existing approved installations can still be paired. Setup guidance remains available while the
                  public download is unavailable.
                </p>
              </>
            )}
          </div>

          {release.status === 'available' ? (
            <a
              href={release.url}
              target='_blank'
              rel='noopener noreferrer'
              className='inline-flex items-center gap-2 rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-black hover:bg-emerald-400'
            >
              Download Founder IDE for Windows · v{release.version}
            </a>
          ) : (
            <a
              href='/founder-ide'
              className='inline-flex items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-950/20 px-4 py-2 text-sm font-semibold text-amber-100 hover:border-amber-400/60'
            >
              Open Founder IDE setup
            </a>
          )}
        </div>
      </div>

      {showInstallGuide && (
        <div className='rounded-lg border border-cyan-500/25 bg-cyan-950/15 p-4'>
          <p className='text-sm font-medium text-cyan-100'>Setup guidance</p>
          <div className='mt-3'>
            <FounderNodeInstallGuide />
          </div>
        </div>
      )}
    </div>
  );
}
