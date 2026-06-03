import { NestFactory } from '@nestjs/core';
import { ValidationPipe } from '@nestjs/common';
import { NestExpressApplication } from '@nestjs/platform-express';
import type { NextFunction, Request, Response } from 'express';
import { AppModule } from './app.module';
import { assertProductionJwtSecret } from './security/jwt-secret.util';

const HSTS_HEADER = 'max-age=63072000; includeSubDomains; preload';

function securityHeaders(_req: Request, res: Response, next: NextFunction) {
  res.setHeader('Strict-Transport-Security', HSTS_HEADER);
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  res.setHeader('Referrer-Policy', 'strict-origin-when-cross-origin');
  res.setHeader(
    'Permissions-Policy',
    'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
  );
  next();
}

async function bootstrap() {
  try {
    assertProductionJwtSecret();
  } catch (err) {
    console.error(`FATAL: ${err instanceof Error ? err.message : 'Invalid JWT_SECRET'}`);
    process.exit(1);
  }

  const app = await NestFactory.create<NestExpressApplication>(AppModule, { rawBody: true });
  app.disable('x-powered-by');
  app.use(securityHeaders);

  app.setGlobalPrefix('api');

  const corsOrigins = (process.env.CORS_ORIGINS ?? process.env.NEXTAUTH_URL ?? 'http://localhost:3000')
    .split(',')
    .map((origin) => origin.trim())
    .filter(Boolean);

  app.enableCors({
    origin: corsOrigins.length === 1 ? corsOrigins[0] : corsOrigins,
    credentials: true,
  });
  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      transform: true,
      forbidNonWhitelisted: true,
    }),
  );

  const port = Number(process.env.PORT ?? process.env.API_PORT ?? 4000);
  const host = process.env.API_BIND_HOST ?? '0.0.0.0';
  await app.listen(port, host);
  console.log(`API running on http://${host}:${port} (/api/health)`);
}

bootstrap();
