/** Parse GitHub profile or repository URLs into owner/repo for tracking. */
export type GithubRepoReference = {
  repoFullName: string;
  githubUrl: string;
  isProfileOnly: boolean;
};

export function parseGithubRepoReference(
  input: string | null | undefined,
): GithubRepoReference | null {
  const raw = input?.trim();
  if (!raw) return null;

  try {
    const withProto = raw.startsWith('http') ? raw : `https://${raw}`;
    const url = new URL(withProto);
    const host = url.hostname.replace(/^www\./, '');
    if (host !== 'github.com') return null;

    const parts = url.pathname.split('/').filter(Boolean);
    if (parts.length < 1) return null;

    const owner = parts[0]!;
    if (['orgs', 'organizations', 'settings', 'marketplace', 'login', 'signup'].includes(owner)) {
      return null;
    }

    if (parts.length === 1) {
      const profileUrl = `https://github.com/${owner}`;
      return { repoFullName: owner, githubUrl: profileUrl, isProfileOnly: true };
    }

    const repo = parts[1]!.replace(/\.git$/i, '');
    if (['tree', 'blob', 'commits', 'pull', 'issues', 'wiki', 'actions'].includes(repo)) {
      return null;
    }

    const repoFullName = `${owner}/${repo}`;
    return {
      repoFullName,
      githubUrl: `https://github.com/${repoFullName}`,
      isProfileOnly: false,
    };
  } catch {
    return null;
  }
}

export function resolveListingGithubRepo(
  projectGithubUrl?: string | null,
  founderGithub?: string | null,
): GithubRepoReference | null {
  const project = parseGithubRepoReference(projectGithubUrl);
  if (project && !project.isProfileOnly) return project;
  const founder = parseGithubRepoReference(founderGithub);
  if (founder && !founder.isProfileOnly) return founder;
  return project ?? founder;
}
