import { NestFactory } from '@nestjs/core';
import { ValidationPipe } from '@nestjs/common';
import { AppModule } from './app.module';

async function bootstrap() {
  const app = await NestFactory.create(AppModule, { rawBody: true });

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
