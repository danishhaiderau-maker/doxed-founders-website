import type { INestApplication } from '@nestjs/common';
import type { NextFunction, Request, Response } from 'express';
import { resolveEvmTreasuryAddress } from './platform-treasury';
import { PrismaService } from '../prisma/prisma.service';
import { SignalCyclesService } from '../trading-agents/signal-cycles.service';
import { SIGNAL_API_KEY_HEADER } from '../trading-agents/signal-api-key.guard';

export const X402_SIGNAL_INTENT_PRICE = process.env.X402_SIGNAL_INTENT_PRICE ?? '$0.10';
export const X402_SIGNAL_NETWORK = process.env.X402_SIGNAL_NETWORK ?? 'eip155:8453';
export const X402_FACILITATOR_URL =
  process.env.X402_FACILITATOR_URL ?? 'https://facilitator.x402.org';

const INTENT_SUFFIX = '/trading-agents/conservative-btc/signals/intent';
const INTENT_PATH_API = `/api${INTENT_SUFFIX}`;

function intentPathMatch(path: string): boolean {
  return path === INTENT_PATH_API || path === INTENT_SUFFIX || path.endsWith('/signals/intent');
}

function hasPaymentHeader(req: Request): boolean {
  return Boolean(
    req.headers['payment-signature'] ||
      req.headers['x-payment'] ||
      req.headers['X-PAYMENT'],
  );
}

export function isX402SignalIntentEnabled(payTo?: string | null): boolean {
  const addr = payTo?.trim() || process.env.X402_EVM_PAY_TO?.trim() || null;
  return process.env.X402_SIGNAL_ENABLED !== 'false' && Boolean(addr);
}

type ExpressMiddleware = (req: Request, res: Response, next: NextFunction) => void | Promise<void>;

function routeConfig(payTo: string) {
  return {
    accepts: {
      scheme: 'exact',
      price: X402_SIGNAL_INTENT_PRICE,
      network: X402_SIGNAL_NETWORK,
      payTo,
    },
    description: 'Conservative BTC Agent — full ENSE signal intent (exchange-neutral)',
    mimeType: 'application/json',
  };
}

function sendManual402(res: Response, payTo: string): void {
  res.status(402).json({
    x402Version: 2,
    error: 'Payment required',
    accepts: [
      {
        scheme: 'exact',
        network: X402_SIGNAL_NETWORK,
        price: X402_SIGNAL_INTENT_PRICE,
        payTo,
      },
    ],
  });
}

function loadX402Paywall(payTo: string): ExpressMiddleware {
  // CJS require — @x402 packages ship ESM types that break Nest's Node resolution.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { paymentMiddleware, x402ResourceServer } = require('@x402/express');
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { ExactEvmScheme } = require('@x402/evm/exact/server');
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { HTTPFacilitatorClient } = require('@x402/core/server');

  const facilitatorClient = new HTTPFacilitatorClient({ url: X402_FACILITATOR_URL });
  const resourceServer = new x402ResourceServer(facilitatorClient).register(
    X402_SIGNAL_NETWORK,
    new ExactEvmScheme(),
  );

  const routes = {
    [`GET ${INTENT_PATH_API}`]: routeConfig(payTo),
    [`GET ${INTENT_SUFFIX}`]: routeConfig(payTo),
  };

  return paymentMiddleware(routes, resourceServer, undefined, undefined, false);
}

export async function attachX402SignalIntentMiddleware(app: INestApplication): Promise<void> {
  const prisma = app.get(PrismaService);
  const payTo = await resolveEvmTreasuryAddress(prisma);

  if (!isX402SignalIntentEnabled(payTo) || !payTo) {
    console.log(
      '[x402] Signal intent payments disabled — set admin EVM treasury (Admin → Platform) or X402_EVM_PAY_TO.',
    );
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

    if (!hasPaymentHeader(req)) {
      sendManual402(res, payTo);
      return;
    }

    return x402Paywall(req, res, (err?: unknown) => {
      if (err) return next(err);
      (req as Request & { x402SignalPaid?: boolean }).x402SignalPaid = true;
      return next();
    });
  });

  console.log(
    `[x402] Signal intent enabled — ${X402_SIGNAL_INTENT_PRICE} on ${X402_SIGNAL_NETWORK} → ${payTo.slice(0, 10)}… (${X402_FACILITATOR_URL})`,
  );
}
