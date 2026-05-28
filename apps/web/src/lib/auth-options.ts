import type { NextAuthOptions } from 'next-auth';
import CredentialsProvider from 'next-auth/providers/credentials';
import GoogleProvider from 'next-auth/providers/google';
import TwitterProvider from 'next-auth/providers/twitter';
import { apiUrl } from './api-base';

async function syncOAuthWithApi(input: {
  email: string;
  name?: string | null;
  avatarUrl?: string | null;
  provider: 'google' | 'twitter';
  providerId: string;
  twitterHandle?: string | null;
}) {
  const payload: Record<string, string | null | undefined> = {
    email: input.email,
    name: input.name ?? undefined,
    avatarUrl: input.avatarUrl ?? undefined,
    provider: input.provider,
    providerId: input.providerId,
  };
  const handle = input.twitterHandle?.replace(/^@/, '').trim();
  if (handle) payload.twitterHandle = handle;

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

async function fetchTwitterHandle(accessToken: string): Promise<string | null> {
  try {
    const res = await fetch(
      'https://api.twitter.com/2/users/me?user.fields=username,profile_image_url',
      { headers: { Authorization: `Bearer ${accessToken}` } },
    );
    if (!res.ok) return null;
    const data = (await res.json()) as { data?: { username?: string } };
    return data.data?.username?.replace(/^@/, '') ?? null;
  } catch {
    return null;
  }
}

const providers: NextAuthOptions['providers'] = [
  CredentialsProvider({
    name: 'Credentials',
    credentials: {
      email: { label: 'Email', type: 'email' },
      password: { label: 'Password', type: 'password' },
    },
    async authorize(credentials) {
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

const twitterClientId = process.env.TWITTER_CLIENT_ID?.trim();
const twitterClientSecret = process.env.TWITTER_CLIENT_SECRET?.trim();

if (twitterClientId && twitterClientSecret) {
  providers.push(
    TwitterProvider({
      clientId: twitterClientId,
      clientSecret: twitterClientSecret,
      version: '2.0',
      authorization: {
        params: {
          scope: 'users.read tweet.read offline.access',
        },
      },
      userinfo: {
        params: {
          'user.fields': 'profile_image_url,username',
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
        if (account.access_token) {
          handle = await fetchTwitterHandle(account.access_token);
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
    twitter: Boolean(twitterClientId && twitterClientSecret),
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
