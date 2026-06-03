import { Body, Controller, Get, Param, Post, Query } from '@nestjs/common';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { MessagesService } from './messages.service';

@Controller('messages')
export class MessagesController {
  constructor(private readonly messages: MessagesService) {}

  @Get('threads')
  threads(@CurrentUser() user: AuthUser) {
    return this.messages.listThreads(user.id);
  }

  @Get('unread-count')
  unreadCount(@CurrentUser() user: AuthUser) {
    return this.messages.countUnread(user.id).then((count) => ({ count }));
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
    @Body() body: { toUserId: string; message: string; applicationId?: string },
  ) {
    return this.messages.sendMessage(user.id, body.toUserId, body.message, {
      applicationId: body.applicationId,
    });
  }
}
