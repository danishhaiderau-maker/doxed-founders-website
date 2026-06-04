import { NestFactory } from '@nestjs/core';
import { ValidationPipe } from '@nestjs/common';
import type { NextFunction, Request, Response } from 'express';
import { AppModule } from './app.module';

async function bootstrap() {
  if (process.env.NODE_ENV === 'production') {
    const secret = process.env.JWT_SECRET?.trim();
    if (!secret || secret.length < 32 || secret === 'dev-secret-change-in-production') {
      console.error(
        'FATAL: Set a strong JWT_SECRET (32+ chars) in production. Refusing to start.',
      );
      process.exit(1);
    }
  }

  const app = await NestFactory.create(AppModule, { rawBody: true });
  const http = app.getHttpAdapter().getInstance();
  if (typeof http?.disable === 'function') {
    http.disable('x-powered-by');
  }

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

  app.use((req: Request, res: Response, next: NextFunction) => {
    res.setHeader('Strict-Transport-Security', 'max-age=31536000; includeSubDomains');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('X-Frame-Options', 'DENY');
    res.setHeader('Referrer-Policy', 'strict-origin-when-cross-origin');
    next();
  });

  const port = Number(process.env.PORT ?? process.env.API_PORT ?? 4000);
  const host = process.env.API_BIND_HOST ?? '0.0.0.0';
  await app.listen(port, host);
  console.log(`API running on http://${host}:${port} (/api/health)`);
}

bootstrap();
