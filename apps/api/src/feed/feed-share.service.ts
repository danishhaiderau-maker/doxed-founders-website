import { Injectable, NotFoundException } from '@nestjs/common';
import { ListingStatus } from '@prisma/client';
import { buildHotBuyShareMessage } from '@dcf/utils';
import { BuilderService } from '../builder/builder.service';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class FeedShareService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly builder: BuilderService,
  ) {}

  async loadProjectShareContext(projectId: string) {
    const project = await this.prisma.project.findUnique({
      where: { id: projectId },
      select: {
        id: true,
        name: true,
        ticker: true,
        summary: true,
      },
    });
    if (!project) return null;

    const listing = await this.prisma.listingApplication.findFirst({
      where: { ticker: project.ticker, status: ListingStatus.APPROVED },
      orderBy: { createdAt: 'desc' },
      select: {
        scoutHighlightNote: true,
        whyList: true,
        whyDoxxed: true,
        founderDoxxedStatus: true,
      },
    });

    let scoutHighlight: string | null = null;
    let scoutThesis: string | null = null;
    if (listing?.scoutHighlightNote?.trim()) {
      scoutHighlight = listing.scoutHighlightNote.trim();
    } else if (listing?.whyList?.trim()) {
      scoutThesis = listing.whyList.trim();
    } else if (listing?.whyDoxxed?.trim()) {
      scoutThesis = listing.whyDoxxed.trim();
    }

    const posts = await this.prisma.feedPost.findMany({
      where: { projectId },
      orderBy: { createdAt: 'desc' },
      take: 12,
      select: {
        initialComment: true,
        comments: {
          orderBy: { createdAt: 'desc' },
          take: 4,
          select: { body: true },
        },
      },
    });

    const snippets: string[] = [];
    for (const post of posts) {
      if (post.initialComment?.trim() && post.initialComment.trim().length >= 16) {
        snippets.push(post.initialComment.trim());
      }
      for (const c of post.comments) {
        if (c.body.trim().length >= 16) snippets.push(c.body.trim());
      }
    }

    return {
      projectName: project.name,
      scoutHighlight,
      scoutThesis,
      summary: project.summary,
      communitySnippets: [...new Set(snippets)].slice(0, 5),
    };
  }

  /** Uses only the requesting user's LLM key — never cross-account data or keys. */
  async enrichHotBuyShare(
    userId: string,
    input: {
      projectSlug: string;
      buyerNames?: string[];
      pctOfActive?: number;
      detailLine?: string;
    },
  ) {
    const project = await this.prisma.project.findFirst({
      where: { slug: input.projectSlug },
      select: { id: true, slug: true, ticker: true, name: true },
    });
    if (!project) throw new NotFoundException('Project not found');

    const ctx = await this.loadProjectShareContext(project.id);
    const template = buildHotBuyShareMessage({
      ticker: project.ticker,
      projectName: project.name,
      buyerNames: input.buyerNames ?? [],
      pctOfActive: input.pctOfActive,
      detailLine: input.detailLine,
      scoutHighlight: ctx?.scoutHighlight,
      scoutThesis: ctx?.scoutThesis,
      summary: ctx?.summary,
      communitySnippets: ctx?.communitySnippets,
    });

    const publicContext = [
      `Project: ${project.name} (${project.ticker})`,
      ctx?.scoutThesis ? `Scout thesis: ${ctx.scoutThesis.slice(0, 200)}` : null,
      ctx?.scoutHighlight ? `Highlight: ${ctx.scoutHighlight.slice(0, 160)}` : null,
      ctx?.summary ? `Summary: ${ctx.summary.slice(0, 160)}` : null,
      ctx?.communitySnippets?.length
        ? `Public trader comments (paraphrase, no names):\n${ctx.communitySnippets.slice(0, 4).join('\n')}`
        : null,
    ]
      .filter(Boolean)
      .join('\n');

    const aiText = await this.builder.tryAiCompletion(
      userId,
      'You write concise Twitter posts for crypto traders. Paraphrase community comments — never quote verbatim or expose private emails. Max 230 characters for the main body (hashtags are added separately). Include thesis + why traders care. No API keys, no cross-user data.',
      `Improve this hot-buy share post. Keep ticker ${project.ticker} and buyer momentum.\n\nDraft:\n${template}\n\nPublic context:\n${publicContext}`,
    );

    if (!aiText?.trim()) {
      return { text: template, source: 'template' as const };
    }

    const body = aiText.trim().slice(0, 240);
    const withFooter = body.includes('#Crypto')
      ? body
      : `${body}\nLive on Doxxed Crypto 👇\n#Crypto #FounderOS #ProofOfConviction @DoxxedCrypto`;

    return { text: withFooter.slice(0, 280), source: 'ai' as const };
  }
}
