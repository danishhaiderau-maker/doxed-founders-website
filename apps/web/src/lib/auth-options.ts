import type { NextAuthOptions } from 'next-auth';
import CredentialsProvider from 'next-auth/providers/credentials';
import GoogleProvider from 'next-auth/providers/google';
import { apiUrl } from './api-base';

async function syncOAuthWithApi(input: {
  email: string;
  name?: string | null;
  avatarUrl?: string | null;
  provider: 'google';
  providerId: string;
}) {
  const res = await fetch(apiUrl('/auth/oauth', true), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });

  if (!res.ok) {
    return null;
  }

  return res.json() as Promise<{
    accessToken: string;
    user: { id: string; email: string; name: string | null; role: string };
  }>;
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

export const authOptions: NextAuthOptions = {
  session: {
    strategy: 'jwt',
  },
  pages: {
    signIn: '/login',
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
  };
}
