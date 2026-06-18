import type { NextAuthOptions } from 'next-auth';
import CredentialsProvider from 'next-auth/providers/credentials';
import GoogleProvider from 'next-auth/providers/google';
import TwitterProvider from 'next-auth/providers/twitter';
import { cookies } from 'next/headers';
import { apiUrl } from './api-base';

async function syncOAuthWithApi(input: {
  email: string;
  name?: string | null;
  avatarUrl?: string | null;
  provider: 'google' | 'twitter';
  providerId: string;
  twitterHandle?: string | null;
  oauthAccessToken?: string | null;
  oauthAccessTokenSecret?: string | null;
  xVerified?: boolean;
  referralCode?: string | null;
}) {
  const payload: Record<string, string | boolean | null | undefined> = {
    email: input.email,
    name: input.name ?? undefined,
    avatarUrl: input.avatarUrl ?? undefined,
    provider: input.provider,
    providerId: input.providerId,
  };
  const handle = input.twitterHandle?.replace(/^@/, '').trim();
  if (handle) payload.twitterHandle = handle;
  if (input.oauthAccessToken) payload.oauthAccessToken = input.oauthAccessToken;
  if (input.oauthAccessTokenSecret) payload.oauthAccessTokenSecret = input.oauthAccessTokenSecret;
  if (input.xVerified) payload.xVerified = true;
  if (input.referralCode) payload.referralCode = input.referralCode;

  const res = await fetch(apiUrl('/auth/oauth', true), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const body = await res.text().catch(() => '');
    console.error(
      `[auth] OAuth sync failed (${input.provider}): ${res.status} ${body.slice(0, 400)}`,
    );
    return null;
  }

  return res.json() as Promise<{
    accessToken: string;
    user: { id: string; email: string; name: string | null; role: string };
  }>;
}

async function fetchTwitterProfile(
  accessToken: string,
): Promise<{ username: string | null; verified: boolean }> {
  try {
    const res = await fetch(
      'https://api.twitter.com/2/users/me?user.fields=username,profile_image_url,verified,verified_type',
      { headers: { Authorization: `Bearer ${accessToken}` } },
    );
    if (!res.ok) return { username: null, verified: false };
    const data = (await res.json()) as { data?: { username?: string; verified?: boolean } };
    return {
      username: data.data?.username?.replace(/^@/, '') ?? null,
      verified: Boolean(data.data?.verified),
    };
  } catch {
    return { username: null, verified: false };
  }
}

const providers: NextAuthOptions['providers'] = [
  CredentialsProvider({
    name: 'Credentials',
    credentials: {
      email: { label: 'Email', type: 'email' },
      password: { label: 'Password', type: 'password' },
      accessToken: { label: 'Access Token', type: 'text' },
    },
    async authorize(credentials) {
      if (credentials?.accessToken) {
        const res = await fetch(apiUrl('/auth/me', true), {
          headers: { Authorization: `Bearer ${credentials.accessToken}` },
        });
        if (!res.ok) return null;
        const user = (await res.json()) as {
          id: string;
          email: string;
          name: string | null;
          role: string;
        };
        return {
          id: user.id,
          email: user.email,
          name: user.name,
          role: user.role,
          accessToken: credentials.accessToken,
        };
      }

      if (!credentials?.email || !credentials?.password) {
        return null;
      }

      const res = await fetch(apiUrl('/auth/login', true), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: credentials.email,
          password: credentials.password,
        }),
      });

      if (!res.ok) {
        return null;
      }

      const data = (await res.json()) as {
        accessToken: string;
        user: { id: string; email: string; name: string | null; role: string };
      };

      return {
        id: data.user.id,
        email: data.user.email,
        name: data.user.name,
        role: data.user.role,
        accessToken: data.accessToken,
      };
    },
  }),
];

if (process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET) {
  providers.push(
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    }),
  );
}

const twitterApiKey = process.env.TWITTER_API_KEY?.trim();
const twitterApiSecret = process.env.TWITTER_API_SECRET?.trim();
const twitterClientId = process.env.TWITTER_CLIENT_ID?.trim();
const twitterClientSecret = process.env.TWITTER_CLIENT_SECRET?.trim();

/** OAuth 1.0a works with API Key + Secret; OAuth 2.0 needs matching Client ID + Client Secret pair. */
const useTwitterOAuth1 = Boolean(twitterApiKey && twitterApiSecret);

if (useTwitterOAuth1) {
  providers.push(
    TwitterProvider({
      clientId: twitterApiKey!,
      clientSecret: twitterApiSecret!,
      profile(profile) {
        const p = profile as {
          id_str: string;
          name: string;
          screen_name?: string;
          email?: string;
          profile_image_url_https?: string;
          verified?: boolean;
        };
        const handle = p.screen_name?.replace(/^@/, '');
        return {
          id: p.id_str,
          name: handle ? `@${handle}` : p.name,
          email: p.email,
          image:
            p.profile_image_url_https?.replace(/_normal\.(jpg|png|gif)$/, '.$1'),
          verified: Boolean(p.verified),
        };
      },
    }),
  );
} else if (twitterClientId && twitterClientSecret) {
  providers.push(
    TwitterProvider({
      clientId: twitterClientId,
      clientSecret: twitterClientSecret,
      version: '2.0',
      authorization: {
        params: {
          scope: 'users.read tweet.read tweet.write offline.access',
        },
      },
      userinfo: {
        params: {
          'user.fields': 'profile_image_url,username,verified,verified_type',
        },
      },
    }),
  );
}

export const authOptions: NextAuthOptions = {
  session: {
    strategy: 'jwt',
  },
  pages: {
    signIn: '/login',
    error: '/login',
  },
  providers,
  callbacks: {
    async signIn({ user, account }) {
      const cookieStore = await cookies();
      const referralCode = cookieStore.get('dcf_ref')?.value?.trim().toUpperCase() || undefined;

      if (account?.provider === 'google') {
        if (!user.email || !account.providerAccountId) {
          return false;
        }

        const data = await syncOAuthWithApi({
          email: user.email,
          name: user.name,
          avatarUrl: user.image,
          provider: 'google',
          providerId: account.providerAccountId,
        });

        if (!data) {
          return false;
        }

        user.id = data.user.id;
        user.role = data.user.role;
        user.accessToken = data.accessToken;
      }

      if (account?.provider === 'twitter') {
        if (!account.providerAccountId) {
          return false;
        }

        let handle: string | null = null;
        let xVerified = Boolean((user as { verified?: boolean }).verified);
        if (user.name?.startsWith('@')) {
          handle = user.name.slice(1);
        } else if (account.access_token) {
          const profile = await fetchTwitterProfile(account.access_token);
          handle = profile.username;
          xVerified = xVerified || profile.verified;
        }

        const email =
          user.email?.trim() ||
          `twitter-${account.providerAccountId}@users.doxedcryptofounder.local`;

        const data = await syncOAuthWithApi({
          email,
          name: user.name ?? (handle ? `@${handle}` : null),
          avatarUrl: user.image ?? null,
          provider: 'twitter',
          providerId: account.providerAccountId,
          twitterHandle: handle,
          oauthAccessToken:
            (account as { oauth_token?: string }).oauth_token ?? account.access_token ?? null,
          oauthAccessTokenSecret:
            (account as { oauth_token_secret?: string }).oauth_token_secret ??
            (account as { refresh_token?: string }).refresh_token ??
            null,
          xVerified,
          referralCode,
        });

        if (!data) {
          return false;
        }

        user.id = data.user.id;
        user.role = data.user.role;
        user.accessToken = data.accessToken;
        user.email = data.user.email;
      }

      return true;
    },
    async jwt({ token, user }) {
      if (user) {
        token.accessToken = user.accessToken;
        token.role = user.role;
        token.id = user.id;
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        session.user.id = token.id;
        session.user.role = token.role;
      }
      session.accessToken = token.accessToken;
      return session;
    },
  },
  secret: process.env.NEXTAUTH_SECRET,
};

export function getEnabledOAuthProviders() {
  return {
    google: Boolean(process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET),
    twitter: useTwitterOAuth1 || Boolean(twitterClientId && twitterClientSecret),
  };
}

/** True when API keys exist but OAuth 2.0 login keys are missing (common misconfiguration). */
export function getTwitterOAuthMisconfig() {
  const hasApiKeys = Boolean(
    process.env.TWITTER_API_KEY?.trim() && process.env.TWITTER_API_SECRET?.trim(),
  );
  const hasOAuth2 = Boolean(twitterClientId && twitterClientSecret);
  return hasApiKeys && !hasOAuth2;
}
