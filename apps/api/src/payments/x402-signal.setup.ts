import type { INestApplication } from '@nestjs/common';
import type { NextFunction, Request, Response } from 'express';
import { SignalCyclesService } from '../trading-agents/signal-cycles.service';
import { SIGNAL_API_KEY_HEADER } from '../trading-agents/signal-api-key.guard';

export const X402_SIGNAL_INTENT_PRICE = process.env.X402_SIGNAL_INTENT_PRICE ?? '$0.10';
export const X402_SIGNAL_NETWORK = process.env.X402_SIGNAL_NETWORK ?? 'eip155:8453';
export const X402_FACILITATOR_URL =
  process.env.X402_FACILITATOR_URL ?? 'https://x402.org/facilitator';

const INTENT_PATH = '/api/trading-agents/conservative-btc/signals/intent';

function intentPathMatch(path: string): boolean {
  return path === INTENT_PATH || path.endsWith('/signals/intent');
}

export function isX402SignalIntentEnabled(): boolean {
  const payTo = process.env.X402_EVM_PAY_TO?.trim();
  return process.env.X402_SIGNAL_ENABLED !== 'false' && Boolean(payTo);
}

type ExpressMiddleware = (req: Request, res: Response, next: NextFunction) => void | Promise<void>;

function loadX402Paywall(payTo: string): ExpressMiddleware {
  // CJS require — @x402 packages ship ESM types that break Nest's Node resolution.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { HTTPFacilitatorClient } = require('@x402/core/server');
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { ExactEvmScheme } = require('@x402/evm/exact/server');
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { paymentMiddlewareFromConfig } = require('@x402/express');

  const facilitatorClient = new HTTPFacilitatorClient({ url: X402_FACILITATOR_URL });
  return paymentMiddlewareFromConfig(
    {
      [`GET ${INTENT_PATH}`]: {
        accepts: [
          {
            scheme: 'exact',
            price: X402_SIGNAL_INTENT_PRICE,
            network: X402_SIGNAL_NETWORK,
            payTo,
          },
        ],
        description: 'Conservative BTC Agent — full ENSE signal intent (exchange-neutral)',
        mimeType: 'application/json',
      },
    },
    facilitatorClient,
    [{ network: X402_SIGNAL_NETWORK, server: new ExactEvmScheme() }],
    undefined,
    undefined,
    false,
  );
}

export function attachX402SignalIntentMiddleware(app: INestApplication): void {
  const payTo = process.env.X402_EVM_PAY_TO?.trim();
  if (!isX402SignalIntentEnabled() || !payTo) {
    console.log('[x402] Signal intent payments disabled — set X402_EVM_PAY_TO to enable.');
    return;
  }

  const cycles = app.get(SignalCyclesService);
  const x402Paywall = loadX402Paywall(payTo);

  const expressApp = app.getHttpAdapter().getInstance();
  expressApp.use(async (req: Request, res: Response, next: NextFunction) => {
    if (req.method !== 'GET' || !intentPathMatch(req.path)) {
      return next();
    }

    const raw = req.headers[SIGNAL_API_KEY_HEADER] ?? req.headers['X-Signal-Api-Key'];
    const key = Array.isArray(raw) ? raw[0] : raw;
    const ctx = await cycles.authenticateApiKey(typeof key === 'string' ? key : undefined);
    if (ctx) {
      (req as Request & { signalApiKey?: typeof ctx }).signalApiKey = ctx;
      return next();
    }

    return x402Paywall(req, res, (err?: unknown) => {
      if (err) return next(err);
      (req as Request & { x402SignalPaid?: boolean }).x402SignalPaid = true;
      return next();
    });
  });

  console.log(
    `[x402] Signal intent enabled — ${X402_SIGNAL_INTENT_PRICE} on ${X402_SIGNAL_NETWORK} → ${payTo.slice(0, 10)}…`,
  );
}
