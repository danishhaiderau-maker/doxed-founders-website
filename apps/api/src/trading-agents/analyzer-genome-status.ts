export type AnalyzerGenomeStatus = {
  ok: boolean;
  source?: string;
  analyzer_mode?: string;
  architecture_frozen?: string;
  genome_stats?: Record<string, unknown> | null;
  discoveries_count?: number;
  library_count?: number;
  error?: string;
};

/** Preserve an upstream external/unavailable analyzer state; never invent ok:true. */
export function normalizeAnalyzerGenomeStatus(
  raw: Record<string, unknown> | null,
): AnalyzerGenomeStatus {
  if (!raw) {
    return {
      ok: false,
      source: 'external desktop analyzer',
      error: 'Analyzer genome unavailable. Fly intentionally has no localhost analyzer process.',
    };
  }

  const mode =
    typeof raw.analyzer_mode === 'string'
      ? raw.analyzer_mode
      : typeof raw.mode === 'string'
        ? raw.mode
        : undefined;
  const external = mode?.toLowerCase().includes('external_desktop') ?? false;
  if (raw.ok === false || (external && raw.ok !== true)) {
    return {
      ok: false,
      source:
        typeof raw.source === 'string'
          ? raw.source
          : 'external desktop analyzer',
      analyzer_mode: mode,
      architecture_frozen:
        typeof raw.architecture_frozen === 'string'
          ? raw.architecture_frozen
          : undefined,
      genome_stats:
        raw.genome_stats && typeof raw.genome_stats === 'object' && !Array.isArray(raw.genome_stats)
          ? (raw.genome_stats as Record<string, unknown>)
          : null,
      error:
        typeof raw.error === 'string'
          ? raw.error
          : typeof raw.note === 'string'
            ? raw.note
            : 'Genome reports are produced by the external desktop analyzer.',
    };
  }

  const stats =
    raw.genome_stats && typeof raw.genome_stats === 'object' && !Array.isArray(raw.genome_stats)
      ? (raw.genome_stats as Record<string, unknown>)
      : null;
  const discoveries = raw.discoveries;
  const library = raw.library;
  const libraryCount = Array.isArray(library)
    ? library.length
    : library && typeof library === 'object'
      ? Object.keys(library).length
      : 0;

  return {
    ok: true,
    source:
      typeof raw.source === 'string'
        ? raw.source
        : 'desktop analyzer :9001 via Fly mirror',
    analyzer_mode: mode,
    architecture_frozen:
      typeof raw.architecture_frozen === 'string'
        ? raw.architecture_frozen
        : undefined,
    genome_stats: stats,
    discoveries_count: Array.isArray(discoveries) ? discoveries.length : 0,
    library_count: libraryCount,
  };
}
