const ALLOWED_FOUNDER_METADATA_PATHS = new Set([
  '.github/founder-os/project-context.md',
  '.github/founder-os/tasks.json',
]);

export function onlyAllowedFounderMetadataChanged(changedFiles) {
  return (
    Array.isArray(changedFiles)
    && changedFiles.length > 0
    && changedFiles.every((file) => ALLOWED_FOUNDER_METADATA_PATHS.has(file))
  );
}

export async function classifyRelaySourceRevision({
  expected,
  observed,
  inspectDescendant,
}) {
  if (observed === expected) {
    return { accepted: true, mode: 'exact', changedFiles: [] };
  }
  if (
    !/^[0-9a-f]{40}$/i.test(String(expected))
    || !/^[0-9a-f]{40}$/i.test(String(observed))
  ) {
    return { accepted: false, mode: 'invalid', changedFiles: [] };
  }

  const proof = await inspectDescendant(expected, observed);
  const changedFiles = Array.isArray(proof?.changedFiles)
    ? proof.changedFiles
    : [];
  if (
    proof?.isDescendant === true
    && onlyAllowedFounderMetadataChanged(changedFiles)
  ) {
    return {
      accepted: true,
      mode: 'founder-metadata-descendant',
      changedFiles,
    };
  }
  return { accepted: false, mode: 'unapproved', changedFiles };
}
