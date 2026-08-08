import { Body, Controller, Get, Param, Post, Query, Res } from '@nestjs/common';
import { Response } from 'express';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { MessagesService } from './messages.service';
import { ChatEventsService, ChatLiveEvent } from './chat-events.service';

@Controller('messages')
export class MessagesController {
  constructor(
    private readonly messages: MessagesService,
    private readonly chatEvents: ChatEventsService,
  ) {}

  @Get('threads')
  threads(@CurrentUser() user: AuthUser) {
    return this.messages.listThreads(user.id);
  }

  @Get('unread-count')
  unreadCount(@CurrentUser() user: AuthUser) {
    return this.messages.countUnread(user.id).then((count) => ({ count }));
  }

  @Post('mark-all-read')
  markAllRead(@CurrentUser() user: AuthUser) {
    return this.messages.markAllRead(user.id);
  }

  @Get('recipient')
  resolveRecipient(@CurrentUser() user: AuthUser, @Query('query') query?: string) {
    return this.messages.resolveRecipient(user.id, query ?? '');
  }

  @Get('with/:otherUserId')
  conversation(
    @CurrentUser() user: AuthUser,
    @Param('otherUserId') otherUserId: string,
    @Query('limit') limit?: string,
  ) {
    const parsed = limit ? Math.min(200, Math.max(1, parseInt(limit, 10) || 80)) : 80;
    return this.messages.getConversation(user.id, otherUserId, parsed);
  }

  @Post('send')
  send(
    @CurrentUser() user: AuthUser,
    @Body()
    body: {
      toUserId: string;
      message: string;
      applicationId?: string;
      replyToId?: string;
    },
  ) {
    return this.messages.sendMessage(user.id, body.toUserId, body.message, {
      applicationId: body.applicationId,
      replyToId: body.replyToId,
    });
  }

  @Post('reactions/:messageId')
  react(
    @CurrentUser() user: AuthUser,
    @Param('messageId') messageId: string,
    @Body() body: { emoji: string },
  ) {
    return this.messages.toggleReaction(user.id, messageId, body.emoji ?? '');
  }

  @Get('prefs')
  prefs(@CurrentUser() user: AuthUser) {
    return this.messages.listThreadPrefs(user.id);
  }

  @Post('prefs')
  setPref(
    @CurrentUser() user: AuthUser,
    @Body()
    body: {
      scope: 'dm' | 'wall';
      targetId: string;
      pinned?: boolean;
      muted?: boolean;
      archived?: boolean;
    },
  ) {
    return this.messages.upsertThreadPref(user.id, body.scope, body.targetId, {
      pinned: body.pinned,
      muted: body.muted,
      archived: body.archived,
    });
  }

  @Post('presence')
  heartbeat(@CurrentUser() user: AuthUser) {
    return this.messages.heartbeat(user.id);
  }

  @Get('presence')
  presence(@CurrentUser() user: AuthUser, @Query('ids') ids?: string) {
    void user;
    const list = (ids ?? '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    return this.messages.getPresence(list);
  }

  /**
   * Lightweight SSE: refresh hints only (not full message payloads).
   * Clients refetch the active thread on `chat` events.
   */
  @Get('stream')
  stream(@CurrentUser() user: AuthUser, @Res() res: Response) {
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache, no-transform');
    res.setHeader('Connection', 'keep-alive');
    res.flushHeaders?.();

    const write = (event: ChatLiveEvent) => {
      res.write(`event: chat\ndata: ${JSON.stringify(event)}\n\n`);
    };

    write({ type: 'ping' });
    const unsub = this.chatEvents.subscribe(user.id, write);
    const ping = setInterval(() => write({ type: 'ping' }), 25_000);

    const cleanup = () => {
      clearInterval(ping);
      unsub();
    };
    res.on('close', cleanup);
    res.on('finish', cleanup);
  }
}
