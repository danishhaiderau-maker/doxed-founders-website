import { NestFactory } from '@nestjs/core';
import { createServer } from 'node:http';
import { RelayExecutorWorkerModule } from './relay-executor-worker.module';
import { SignalSubscriberExecutionService } from './trading-agents/signal-subscriber-execution.service';

async function bootstrap() {
  if (process.env.RELAY_EXECUTOR_WORKER !== 'true') {
    throw new Error('RELAY_EXECUTOR_WORKER=true is required');
  }
  if (process.env.SUBSCRIBER_EXECUTION_ENABLED !== 'true') {
    throw new Error('SUBSCRIBER_EXECUTION_ENABLED=true is required');
  }

  const app = await NestFactory.createApplicationContext(RelayExecutorWorkerModule, {
    logger: ['error', 'warn', 'log'],
  });
  const execution = app.get(SignalSubscriberExecutionService);
  if (process.env.RELAY_EXECUTOR_BOOT_SMOKE === 'true') {
    console.log('Relay executor worker dependency graph smoke passed');
    await app.close();
    process.exit(0);
  }
  const port = Number(process.env.PORT ?? 4000);
  const server = createServer((req, res) => {
    if (req.url !== '/api/health/live' && req.url !== '/api/health/ready') {
      res.writeHead(404, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ status: 'not_found' }));
      return;
    }
    const health = execution.getHealthSnapshot();
    const readiness = req.url === '/api/health/ready';
    res.writeHead(readiness && !health.healthy ? 503 : 200, {
      'content-type': 'application/json',
      'cache-control': 'no-store',
    });
    res.end(JSON.stringify({
      status: readiness ? (health.healthy ? 'ready' : 'starting') : 'ok',
      role: 'relay-executor-worker',
      health,
    }));
  });
  server.listen(port, '0.0.0.0', () => {
    console.log(`Relay executor worker health listening on 0.0.0.0:${port}`);
  });

  const shutdown = async () => {
    server.close();
    await app.close();
    process.exit(0);
  };
  process.once('SIGTERM', () => void shutdown());
  process.once('SIGINT', () => void shutdown());
}

void bootstrap().catch((err) => {
  console.error(
    `Relay executor worker failed: ${err instanceof Error ? err.stack ?? err.message : err}`,
  );
  process.exit(1);
});
