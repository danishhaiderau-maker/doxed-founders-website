/**
 * Probe production Twitter sign-in: OAuth 1.0a vs 2.0 from redirect URL.
 */
const base = process.env.SITE ?? 'https://doxxedcrypto.digital';

const csrfRes = await fetch(`${base}/api/auth/csrf`);
const { csrfToken } = await csrfRes.json();
const cookies = csrfRes.headers.getSetCookie?.() ?? [];

const body = new URLSearchParams({
  csrfToken,
  callbackUrl: `${base}/`,
  json: 'true',
});

const signInRes = await fetch(`${base}/api/auth/signin/twitter`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/x-www-form-urlencoded',
    Cookie: cookies.map((c) => c.split(';')[0]).join('; '),
  },
  body,
  redirect: 'manual',
});

const loc = signInRes.headers.get('location') ?? '';
console.log('Sign-in status:', signInRes.status);
console.log('Redirect:', loc.slice(0, 200));

if (loc.includes('oauth2/authorize')) {
  console.log('Flow: OAuth 2.0 (OLD — may fail token exchange)');
} else if (loc.includes('oauth/authenticate') || loc.includes('oauth/authorize')) {
  console.log('Flow: OAuth 1.0a (EXPECTED fix)');
} else {
  console.log('Flow: unknown');
}
