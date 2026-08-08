import { Injectable } from '@nestjs/common';
import { EventEmitter } from 'events';

export type ChatLiveEvent =
  | { type: 'dm'; otherUserId: string }
  | { type: 'wall'; projectId: string; slug?: string }
  | { type: 'presence'; userId: string }
  | { type: 'prefs' }
  | { type: 'ping' };

/**
 * In-process fan-out for chat SSE. Single-replica Railway is the current
 * assumption (same as showcase relay). Multi-replica would need Redis pub/sub.
 */
@Injectable()
export class ChatEventsService {
  private readonly bus = new EventEmitter();

  constructor() {
    this.bus.setMaxListeners(200);
  }

  emitToUser(userId: string, event: ChatLiveEvent) {
    this.bus.emit(userId, event);
  }

  emitToUsers(userIds: string[], event: ChatLiveEvent) {
    const unique = [...new Set(userIds.filter(Boolean))];
    for (const id of unique) this.emitToUser(id, event);
  }

  subscribe(userId: string, handler: (event: ChatLiveEvent) => void): () => void {
    this.bus.on(userId, handler);
    return () => this.bus.off(userId, handler);
  }
}
