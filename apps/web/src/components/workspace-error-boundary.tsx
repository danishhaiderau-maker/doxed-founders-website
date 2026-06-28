'use client';

import { Component, type ReactNode } from 'react';

type Props = {
  children: ReactNode;
  fallback?: (error: Error, reset: () => void) => ReactNode;
};

type State = { error: Error | null };

export class WorkspaceErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    // eslint-disable-next-line no-console
    console.error('[Founder OS] workspace crashed:', error, info?.componentStack ?? '');
  }

  reset = () => this.setState({ error: null });

  render() {
    if (this.state.error) {
      if (this.props.fallback) return this.props.fallback(this.state.error, this.reset);
      return (
        <div className="flex min-h-[60vh] items-center justify-center p-6">
          <div className="max-w-md space-y-4 rounded-xl border border-red-500/30 bg-red-950/20 p-5 text-sm">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-red-400">
                Founder OS hit a runtime error
              </p>
              <h2 className="mt-1 text-lg font-bold text-white">Workspace failed to render</h2>
            </div>
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-black/40 p-3 text-xs text-red-200">
              {this.state.error.message || String(this.state.error)}
            </pre>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={this.reset}
                className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-500"
              >
                Retry
              </button>
              <button
                type="button"
                onClick={() => {
                  if (typeof window !== 'undefined') window.location.reload();
                }}
                className="rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:text-white"
              >
                Reload page
              </button>
            </div>
            <p className="text-[11px] text-zinc-500">
              The error has been logged to your browser console. Retrying keeps your session; reloading
              re-fetches workspace state.
            </p>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
