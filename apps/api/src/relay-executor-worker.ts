import { NestFactory } from '@nestjs/core';
import { createServer } from 'node:http';
import { RelayExecutorWorkerModule } from './relay-executor-worker.module';
import { SignalSubscriberExecutionService } from './trading-agents/signal-subscriber-execution.service';
import { executorWakeAuthorized, parseExecutorWakeRequest } from './relay-executor-wake-http';

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
    if (req.url === '/api/wake' && req.method === 'POST') {
      const supplied = Array.isArray(req.headers['x-bot-control-secret'])
        ? req.headers['x-bot-control-secret'][0]
        : req.headers['x-bot-control-secret'];
      if (!executorWakeAuthorized(supplied, process.env.BOT_CONTROL_SECRET?.trim() ?? '')) {
        res.writeHead(401, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ status: 'unauthorized' }));
        return;
      }
      let size = 0;
      const chunks: Buffer[] = [];
      req.on('data', (chunk: Buffer) => {
        size += chunk.length;
        if (size > 16_384) req.destroy();
        else chunks.push(chunk);
      });
      req.on('end', () => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(Buffer.concat(chunks).toString('utf8'));
        } catch {
          parsed = null;
        }
        const wake = parseExecutorWakeRequest(parsed);
        if (!wake) {
          res.writeHead(400, { 'content-type': 'application/json' });
          res.end(JSON.stringify({ status: 'invalid_wake' }));
          return;
        }
        void execution.acceptDirectExecutorWake(wake).then((accepted) => {
          res.writeHead(accepted ? 202 : 409, {
            'content-type': 'application/json',
            'cache-control': 'no-store',
          });
          res.end(JSON.stringify({ status: accepted ? 'accepted' : 'busy' }));
        }).catch(() => {
          res.writeHead(500, { 'content-type': 'application/json' });
          res.end(JSON.stringify({ status: 'failed' }));
        });
      });
      return;
    }
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
