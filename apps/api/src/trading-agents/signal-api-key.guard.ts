import {
  CanActivate,
  ExecutionContext,
  Injectable,
} from '@nestjs/common';
import { SignalCyclesService } from './signal-cycles.service';

export const SIGNAL_API_KEY_HEADER = 'x-signal-api-key';

@Injectable()
export class SignalApiKeyGuard implements CanActivate {
  constructor(private readonly signalCycles: SignalCyclesService) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const req = context.switchToHttp().getRequest<{
      headers: Record<string, string | string[] | undefined>;
      signalApiKey?: { userId: string; agentId: string; keyId: string };
    }>();
    const raw = req.headers[SIGNAL_API_KEY_HEADER] ?? req.headers['X-Signal-Api-Key'];
    const key = Array.isArray(raw) ? raw[0] : raw;
    const ctx = await this.signalCycles.authenticateApiKey(key);
    if (ctx) req.signalApiKey = ctx;
    return true;
  }
}
