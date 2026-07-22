'use client';

import { useEffect, useRef, useState } from 'react';
import { ChevronDown, Check } from 'lucide-react';
import type { ConnectedNode } from './types';

type Props = {
  /** All connected nodes (from /api/founder-node/status). */
  nodes: ConnectedNode[];
  /** The currently active node (what the phone is controlling). */
  activeNode: ConnectedNode | null;
  /** Switch the phone context to a different connected IDE. */
  onSelect: (node: ConnectedNode) => void;
};

/**
 * The switch button at the top of the Phone Remote UI.
 *
 * Per the user's vision: "press a button on top and switch to Founder IDE… it
 * reloads and becomes Founder IDE. Same page, just press the switch button."
 * Clicking opens a dropdown of connected IDEs; selecting one switches the
 * phone UI context to that IDE (updates the chat header + active node).
 * Unsupported / offline IDEs are greyed out.
 */
export function IdeSwitchButton({ nodes, activeNode, onSelect }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('click', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('click', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, []);

  const onlineNodes = nodes.filter((n) => n.status === 'online');
  const offlineNodes = nodes.filter((n) => n.status !== 'online');

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2.5 text-left transition hover:border-zinc-600"
      >
        <span
          className={[
            'inline-block h-2 w-2 shrink-0 rounded-full',
            activeNode?.status === 'online' ? 'bg-emerald-400' : 'bg-zinc-600',
          ].join(' ')}
          aria-hidden
        />
        <span className="min-w-0 flex-1">
          <span className="block text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
            Controlling
          </span>
          <span className="block truncate text-sm font-semibold text-white">
            {activeNode ? activeNode.label || 'Unnamed machine' : 'No IDE selected'}
          </span>
        </span>
        <ChevronDown
          className={['h-4 w-4 shrink-0 text-zinc-400 transition', open && 'rotate-180'].join(' ')}
        />
      </button>

      {open && (
        <div
          className="absolute left-0 right-0 top-full z-50 mt-1.5 overflow-hidden rounded-xl border border-zinc-700/80 bg-zinc-950/95 shadow-2xl backdrop-blur-md"
          role="menu"
          aria-label="Switch connected IDE"
        >
          <div className="border-b border-zinc-800/80 px-3 py-2">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-zinc-500">
              Switch IDE
            </p>
          </div>

          {onlineNodes.length === 0 && offlineNodes.length === 0 ? (
            <div className="px-3 py-3 text-xs text-zinc-500">
              <p>Connect another editor from Founder IDE on your desktop.</p>
              <p className="mt-1 text-zinc-600">
                Pair Founder Node from your machine and it will show up here.
              </p>
            </div>
          ) : (
            <div className="max-h-72 overflow-y-auto p-1.5">
              {onlineNodes.length > 0 && (
                <ul className="space-y-0.5">
                  {onlineNodes.map((node) => (
                    <SwitchRow
                      key={node.nodeId}
                      node={node}
                      active={activeNode?.nodeId === node.nodeId}
                      onSelect={(n) => {
                        onSelect(n);
                        setOpen(false);
                      }}
                    />
                  ))}
                </ul>
              )}

              {offlineNodes.length > 0 && (
                <>
                  <p className="px-2 pt-3 pb-1 text-[9px] font-bold uppercase tracking-[0.18em] text-zinc-600">
                    Offline
                  </p>
                  <ul className="space-y-0.5">
                    {offlineNodes.map((node) => (
                      <SwitchRow
                        key={node.nodeId}
                        node={node}
                        active={activeNode?.nodeId === node.nodeId}
                        disabled
                        onSelect={(n) => {
                          onSelect(n);
                          setOpen(false);
                        }}
                      />
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SwitchRow({
  node,
  active,
  disabled,
  onSelect,
}: {
  node: ConnectedNode;
  active: boolean;
  disabled?: boolean;
  onSelect: (node: ConnectedNode) => void;
}) {
  return (
    <li>
      <button
        type="button"
        disabled={disabled}
        onClick={() => onSelect(node)}
        className={[
          'flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition',
          disabled
            ? 'cursor-not-allowed text-zinc-600'
            : active
              ? 'bg-emerald-500/15 text-emerald-50'
              : 'text-zinc-200 hover:bg-zinc-800/80',
        ].join(' ')}
        title={disabled ? 'Offline — open Founder Node on this machine to switch to it' : undefined}
      >
        <span
          className={[
            'inline-block h-1.5 w-1.5 shrink-0 rounded-full',
            node.status === 'online' ? 'bg-emerald-400' : 'bg-zinc-600',
          ].join(' ')}
          aria-hidden
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{node.label || 'Unnamed machine'}</span>
          <span className="block truncate text-[10px] text-zinc-500">
            {node.platform ?? 'desktop'}
            {node.appVersion ? ` · v${node.appVersion}` : ''}
          </span>
        </span>
        {active && <Check className="h-4 w-4 shrink-0 text-emerald-300" aria-hidden />}
      </button>
    </li>
  );
}
