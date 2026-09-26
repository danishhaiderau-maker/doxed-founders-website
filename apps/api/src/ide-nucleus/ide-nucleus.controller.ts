import { Controller, Get, Req, UnauthorizedException, UseGuards } from '@nestjs/common';
import { formatFounderGraphForPrompt, projectLiveNucleusGraph } from '@dcf/utils';
import { Public } from '../auth/public.decorator';
import { FounderCopilotService } from '../events/founder-copilot.service';
import { FounderGraphService } from '../founder-graph/founder-graph.service';
import { NUCLEUS_AUTH_HELP } from './ide-nucleus-auth';
import {
  IdeNucleusAuthGuard,
  type NucleusAuthedRequest,
} from './ide-nucleus-auth.guard';

const LIVE_GRAPH_MAX_AGE_MS = 60_000;

/**
 * Founder Graph read for Nucleus.
 *
 * `@Public()` skips the global JWT guard so a Founder Node header is not
 * rejected as a JWT before this guard runs. The route still requires one of:
 *
 * - Browser session JWT: `Authorization: Bearer <accessToken>`
 * - Founder Node: `Authorization: FounderNode {nodeId}:{nodeToken}`
 * - OpenAI-compat node bearer: `Authorization: Bearer fos_{nodeId}:{nodeToken}`
 *
 * `GET /copilot/founder-graph` remains session-JWT only and still returns the
 * historical chain. This route returns the live projection: closed PRs, old
 * commits, deploys, and other finished nodes are dropped, and edges that
 * touched them die. A stored graph older than a minute is rebuilt so new live
 * edges can appear. The payload does not include node tokens or vault bodies.
 */
@Public()
@UseGuards(IdeNucleusAuthGuard)
@Controller('ide')
export class IdeNucleusController {
  constructor(
    private readonly copilot: FounderCopilotService,
    private readonly founderGraph: FounderGraphService,
  ) {}

  @Get('nucleus')
  async nucleus(@Req() req: NucleusAuthedRequest) {
    const caller = req.nucleusCaller;
    if (!caller?.userId) {
      throw new UnauthorizedException(NUCLEUS_AUTH_HELP);
    }
    const payload = await this.copilot.getFounderGraphForUser(caller.userId);
    let source = payload.graph;
    const ageMs = Date.now() - Date.parse(source?.updatedAt ?? '');
    if (!Number.isFinite(ageMs) || ageMs > LIVE_GRAPH_MAX_AGE_MS) {
      try {
        source = await this.founderGraph.rebuildForUser(caller.userId);
      } catch {
        /* Keep the stored chain. Live projection still drops finished nodes. */
      }
    }
    const liveGraph = projectLiveNucleusGraph(source);
    return {
      graph: liveGraph,
      liveGraph,
      miniChain: liveGraph.nodes.slice(0, 8),
      excerpt: formatFounderGraphForPrompt(liveGraph),
      updatedAt: liveGraph.updatedAt,
      nodeCount: liveGraph.nodes.length,
      auth: caller.auth,
    };
  }
}
