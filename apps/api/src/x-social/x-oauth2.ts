/** OAuth 2.0 user-context helpers for X posting (when login used OAuth 2.0, not 1.0a). */

export type OAuth2TokenPair = {
  accessToken: string;
  refreshToken?: string | null;
};

function oauth2ClientCredentials(): { clientId: string; clientSecret: string } | null {
  const clientId = process.env.TWITTER_CLIENT_ID?.trim();
  const clientSecret = process.env.TWITTER_CLIENT_SECRET?.trim();
  if (!clientId || !clientSecret) return null;
  return { clientId, clientSecret };
}

export function looksLikeOAuth2RefreshToken(secret: string | null | undefined): boolean {
  if (!secret?.trim()) return false;
  const s = secret.trim();
  if (s.startsWith('fn_')) return false;
  return s.length > 40 && !s.includes('&');
}

export async function verifyOAuth2AccessToken(
  accessToken: string,
): Promise<{ ok: boolean; username?: string; expired?: boolean }> {
  try {
    const res = await fetch('https://api.twitter.com/2/users/me?user.fields=username', {
      headers: { Authorization: `Bearer ${accessToken}` },
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

export async function refreshOAuth2AccessToken(
  refreshToken: string,
): Promise<{ ok: true; accessToken: string; refreshToken?: string } | { ok: false }> {
  const creds = oauth2ClientCredentials();
  if (!creds) return { ok: false };

  const body = new URLSearchParams({
    grant_type: 'refresh_token',
    refresh_token: refreshToken,
    client_id: creds.clientId,
  });

  const basic = Buffer.from(`${creds.clientId}:${creds.clientSecret}`).toString('base64');
  const res = await fetch('https://api.twitter.com/2/oauth2/token', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      Authorization: `Basic ${basic}`,
    },
    body: body.toString(),
  });

  if (!res.ok) return { ok: false };
  const data = (await res.json()) as { access_token?: string; refresh_token?: string };
  if (!data.access_token) return { ok: false };
  return {
    ok: true,
    accessToken: data.access_token,
    refreshToken: data.refresh_token ?? refreshToken,
  };
}

export async function postTweetOAuth2(
  accessToken: string,
  text: string,
  mediaIds?: string[],
): Promise<{ ok: true; tweetId: string } | { ok: false; status?: number; body?: string }> {
  const body: { text: string; media?: { media_ids: string[] } } = {
    text: text.slice(0, 280),
  };
  if (mediaIds?.length) body.media = { media_ids: mediaIds };

  const res = await fetch('https://api.twitter.com/2/tweets', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const errBody = await res.text().catch(() => '');
    return { ok: false, status: res.status, body: errBody };
  }

  const data = (await res.json()) as { data?: { id: string } };
  if (!data.data?.id) return { ok: false };
  return { ok: true, tweetId: data.data.id };
}
