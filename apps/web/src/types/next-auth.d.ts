import type { DefaultSession } from 'next-auth';

declare module 'next-auth' {
  interface Session {
    accessToken?: string;
    user: DefaultSession['user'] & {
      id?: string;
      role?: string;
    };
  }

  interface User {
    id: string;
    role: string;
    accessToken: string;
  }
}

declare module 'next-auth/jwt' {
  interface JWT {
    accessToken?: string;
    role?: string;
    id?: string;
  }
}

export {};
