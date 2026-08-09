import {
  CanActivate,
  ExecutionContext,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';
import { parseFounderNodeAuthHeader } from '@dcf/founder-vault';
import { FounderNodeService } from '../founder-node/founder-node.service';
import type { FounderNodeRequestUser } from '../founder-node/founder-node.guard';

/**
 * Hosted Second Brain spends platform Gemini/OpenAI keys — require a signed-in
 * user (JWT) or a paired Founder Node. Never accept anonymous critique calls.
 */
@Injectable()
export class SecondBrainCallerGuard implements CanActivate {
  constructor(
    private readonly nodes: FounderNodeService,
    private readonly jwt: JwtService,
  ) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const request = context.switchToHttp().getRequest<{
      headers: Record<string, string | string[] | undefined>;
      founderNode?: FounderNodeRequestUser;
      user?: { id?: string; sub?: string; userId?: string };
    }>();

    const raw = request.headers.authorization;
    const header = (Array.isArray(raw) ? raw[0] : raw)?.trim() ?? '';

    const parsed = parseFounderNodeAuthHeader(header);
    if (parsed) {
      const node = await this.nodes.validateNodeToken(
        parsed.nodeId,
        parsed.nodeToken,
      );
      request.founderNode = {
        kind: 'founder-node',
        userId: node.userId,
        nodeId: node.nodeId,
        nodeDbId: node.id,
      };
      return true;
    }

    if (header.toLowerCase().startsWith('bearer ')) {
      const token = header.slice(7).trim();
      if (!token) {
        throw new UnauthorizedException(
          'Sign in or pair a Founder Node to use hosted Second Brain',
        );
      }
      try {
        const payload = await this.jwt.verifyAsync<{
          sub?: string;
          id?: string;
          userId?: string;
        }>(token);
        const userId = payload.sub ?? payload.id ?? payload.userId;
        if (!userId) {
          throw new UnauthorizedException('Invalid session for Second Brain');
        }
        request.user = { ...payload, id: userId };
        return true;
      } catch {
        throw new UnauthorizedException(
          'Sign in or pair a Founder Node to use hosted Second Brain',
        );
      }
    }

    throw new UnauthorizedException(
      'Sign in or pair a Founder Node to use hosted Second Brain',
    );
  }
}
