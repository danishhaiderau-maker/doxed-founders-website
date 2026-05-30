import {
  CanActivate,
  ExecutionContext,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';
import { parseFounderNodeAuthHeader } from '@dcf/founder-vault';
import { FounderNodeService } from './founder-node.service';

export type FounderNodeRequestUser = {
  kind: 'founder-node';
  userId: string;
  nodeId: string;
  nodeDbId: string;
};

@Injectable()
export class FounderNodeGuard implements CanActivate {
  constructor(private readonly nodes: FounderNodeService) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const request = context.switchToHttp().getRequest<{
      headers: Record<string, string | string[] | undefined>;
      founderNode?: FounderNodeRequestUser;
    }>();

    const raw = request.headers.authorization;
    const header = Array.isArray(raw) ? raw[0] : raw;
    const parsed = parseFounderNodeAuthHeader(header);
    if (!parsed) {
      throw new UnauthorizedException('Founder Node credentials required');
    }

    const node = await this.nodes.validateNodeToken(parsed.nodeId, parsed.nodeToken);
    request.founderNode = {
      kind: 'founder-node',
      userId: node.userId,
      nodeId: node.nodeId,
      nodeDbId: node.id,
    };
    return true;
  }
}
