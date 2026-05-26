export const config = {
  api: {
    port: parseInt(process.env.API_PORT ?? '4000', 10),
    url: process.env.API_URL ?? 'http://localhost:4000',
  },
  web: {
    url: process.env.NEXTAUTH_URL ?? 'http://localhost:3000',
  },
  redis: {
    url: process.env.REDIS_URL ?? 'redis://localhost:6379',
  },
  jwt: {
    secret: process.env.JWT_SECRET ?? 'dev-secret-change-in-production',
    expiresIn: process.env.JWT_EXPIRES_IN ?? '7d',
  },
  paperTrading: {
    startingBalance: 10_000,
  },
} as const;
