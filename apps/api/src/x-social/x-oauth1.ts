import * as crypto from 'crypto';

export type OAuth1Credentials = {
  consumerKey: string;
  consumerSecret: string;
  accessToken: string;
  accessTokenSecret: string;
};

function percentEncode(value: string): string {
  return encodeURIComponent(value).replace(/[!'()*]/g, (char) =>
    `%${char.charCodeAt(0).toString(16).toUpperCase()}`,
  );
}

export function buildOAuth1Header(
  method: string,
  url: string,
  creds: OAuth1Credentials,
  extraParams: Record<string, string> = {},
): string {
  const oauthParams: Record<string, string> = {
    oauth_consumer_key: creds.consumerKey,
    oauth_nonce: crypto.randomBytes(16).toString('hex'),
    oauth_signature_method: 'HMAC-SHA1',
    oauth_timestamp: Math.floor(Date.now() / 1000).toString(),
    oauth_token: creds.accessToken,
    oauth_version: '1.0',
    ...extraParams,
  };

  const paramString = Object.keys(oauthParams)
    .sort()
    .map((key) => `${percentEncode(key)}=${percentEncode(oauthParams[key])}`)
    .join('&');

  const baseString = [
    method.toUpperCase(),
    percentEncode(url),
    percentEncode(paramString),
  ].join('&');

  const signingKey = `${percentEncode(creds.consumerSecret)}&${percentEncode(creds.accessTokenSecret)}`;
  const signature = crypto.createHmac('sha1', signingKey).update(baseString).digest('base64');

  const headerParams: Record<string, string> = { ...oauthParams, oauth_signature: signature };
  const authHeader = Object.keys(headerParams)
    .sort()
    .map((key) => `${percentEncode(key)}="${percentEncode(headerParams[key])}"`)
    .join(', ');

  return `OAuth ${authHeader}`;
}

/** Live check — returns false when user revoked access or tokens expired. */
export async function verifyOAuth1Credentials(
  creds: OAuth1Credentials,
): Promise<{ ok: boolean; username?: string; expired?: boolean }> {
  const url = 'https://api.twitter.com/2/users/me';
  const authorization = buildOAuth1Header('GET', url, creds);
  try {
    const res = await fetch(`${url}?user.fields=username`, {
      headers: { Authorization: authorization },
    });
    if (res.ok) {
      const data = (await res.json()) as { data?: { username?: string } };
      return { ok: true, username: data.data?.username };
    }
    if (res.status === 401 || res.status === 403) {
      return { ok: false, expired: true };
    }
    return { ok: false };
  } catch {
    return { ok: false };
  }
}

export function readOAuth1Credentials():
  | { ok: true; creds: OAuth1Credentials; brandHandle: string }
  | { ok: false; reason: string } {
  const consumerKey = process.env.TWITTER_API_KEY?.trim();
  const consumerSecret = process.env.TWITTER_API_SECRET?.trim();
  const accessToken = process.env.TWITTER_ACCESS_TOKEN?.trim();
  const accessTokenSecret = process.env.TWITTER_ACCESS_TOKEN_SECRET?.trim();
  const brandHandle = (process.env.X_BRAND_HANDLE ?? 'Bitbro4crypto').replace(/^@/, '');

  if (!consumerKey || !consumerSecret || !accessToken || !accessTokenSecret) {
    return {
      ok: false,
      reason:
        'Set TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET for @Bitbro4crypto posting.',
    };
  }

  return {
    ok: true,
    creds: { consumerKey, consumerSecret, accessToken, accessTokenSecret },
    brandHandle,
  };
}

export function siteUrl(): string {
  return (process.env.PUBLIC_SITE_URL ?? 'https://doxxedcrypto.digital').replace(/\/$/, '');
}
