import { BadRequestException, Injectable, Logger } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { XPostingResolverService } from './x-posting-resolver.service';

export type UserTweetResult =
  | { ok: true; tweetId: string; tweetUrl: string }
  | { ok: false; reason: string };

@Injectable()
export class UserXPostingService {
  private readonly logger = new Logger(UserXPostingService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly xPosting: XPostingResolverService,
  ) {}

  async canUserPost(userId: string) {
    const status = await this.xPosting.getConnectionStatus(userId);
    return {
      connected: status.connected,
      canPost: status.canPostInstantly,
      twitterHandle: status.twitterHandle,
    };
  }

  async postTweet(userId: string, text: string, mediaIds?: string[]): Promise<UserTweetResult> {
    const result = await this.xPosting.postTweet(userId, text, mediaIds);
    if (!result.ok) {
      this.logger.warn(`User tweet failed: ${result.reason}`);
      throw new BadRequestException(result.reason);
    }
    return { ok: true, tweetId: result.tweetId, tweetUrl: result.tweetUrl };
  }
}
