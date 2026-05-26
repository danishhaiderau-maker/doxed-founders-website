function formatUsd(value: number, decimals = 2): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}

export function buildPortfolioPath(userId: string): string {
  return `/portfolio/${userId}`;
}

export function buildPortfolioShareUrl(origin: string, userId: string): string {
  const base = origin.replace(/\/$/, '');
  return `${base}${buildPortfolioPath(userId)}`;
}

export function buildPortfolioShareMessage(
  displayName: string,
  roi: number,
  totalValue: number,
): string {
  const sign = roi >= 0 ? '+' : '';
  return `${displayName} is paper trading on DoxedCryptoFounder — ${sign}${roi.toFixed(2)}% ROI · ${formatUsd(totalValue)} portfolio.`;
}

export function buildTwitterIntentUrl(text: string, url?: string): string {
  const params = new URLSearchParams({ text });
  if (url) {
    params.set('url', url);
  }
  return `https://twitter.com/intent/tweet?${params.toString()}`;
}
