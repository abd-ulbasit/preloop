/** Release links and fallback selection shared by the console and public blog. */
export function releaseBlogUrl(
  url: string | undefined,
  version: string
): string | undefined {
  if (!url?.includes('{version}')) return url;
  const match = /^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/.exec(version);
  // Without a known server release, the template's parent is the blog index.
  return match
    ? url.replace('{version}', match.slice(1, 4).join('-'))
    : url.slice(0, url.lastIndexOf('/'));
}

function releaseVersion(slug: string): number[] | null {
  const match = /^preloop-(\d+)-(\d+)-(\d+)$/.exec(slug);
  if (!match) return null;
  const parts = match.slice(1).map(Number);
  return parts.every(Number.isSafeInteger) ? parts : null;
}

function compareVersions(a: number[], b: number[]): number {
  return a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
}

/** Select directly, avoiding requests or redirect chains through unpublished releases. */
export function releaseBlogFallback(
  slug: string,
  publishedSlugs: string[]
): string | null {
  const requested = releaseVersion(slug);
  if (!requested || publishedSlugs.includes(slug)) return null;
  const candidates = publishedSlugs
    .map((candidate) => ({
      slug: candidate,
      version: releaseVersion(candidate),
    }))
    .filter(
      (candidate) =>
        candidate.version && compareVersions(candidate.version, requested) <= 0
    )
    .sort((a, b) => compareVersions(b.version!, a.version!));
  return candidates.length ? `/blog/${candidates[0].slug}` : '/blog';
}
