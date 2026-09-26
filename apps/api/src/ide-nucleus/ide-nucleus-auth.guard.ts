import {
  CanActivate,
  ExecutionContext,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';
import { FounderNodeService } from '../founder-node/founder-node.service';
import type { FounderNodeRequestUser } from '../founder-node/founder-node.guard';
import {
  NUCLEUS_AUTH_HELP,
  classifyNucleusAuthorization,
} from './ide-nucleus-auth';

export type NucleusCaller = {
  userId: string;
  auth: 'jwt' | 'founder-node';
};

export type NucleusAuthedRequest = {
  headers: Record<string, string | string[] | undefined>;
  nucleusCaller?: NucleusCaller;
  founderNode?: FounderNodeRequestUser;
  user?: { id?: string; sub?: string; userId?: string };
};

@Injectable()
export class IdeNucleusAuthGuard implements CanActivate {
  constructor(
    private readonly nodes: FounderNodeService,
    private readonly jwt: JwtService,
  ) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const request = context.switchToHttp().getRequest<NucleusAuthedRequest>();
    const raw = request.headers.authorization;
    const header = Array.isArray(raw) ? raw[0] : raw;
    const classified = classifyNucleusAuthorization(header);

    if (classified.kind === 'founder-node') {
      const node = await this.nodes.validateNodeToken(
        classified.nodeId,
        classified.nodeToken,
      );
      request.founderNode = {
        kind: 'founder-node',
        userId: node.userId,
        nodeId: node.nodeId,
        nodeDbId: node.id,
      };
      request.nucleusCaller = { userId: node.userId, auth: 'founder-node' };
      return true;
    }

    if (classified.kind === 'jwt') {
      try {
        const payload = await this.jwt.verifyAsync<{
          sub?: string;
          id?: string;
          userId?: string;
        }>(classified.token);
        const userId = payload.sub ?? payload.id ?? payload.userId;
        if (!userId) throw new UnauthorizedException(NUCLEUS_AUTH_HELP);
        request.user = { ...payload, id: userId };
        request.nucleusCaller = { userId, auth: 'jwt' };
        return true;
      } catch (err) {
        if (err instanceof UnauthorizedException) throw err;
        throw new UnauthorizedException(NUCLEUS_AUTH_HELP);
      }
    }

    throw new UnauthorizedException(NUCLEUS_AUTH_HELP);
  }
}
