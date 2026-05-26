import { Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { PrismaClient } from '@prisma/client';

@Injectable()
export class PrismaService
  extends PrismaClient
  implements OnModuleInit, OnModuleDestroy
{
  private readonly logger = new Logger(PrismaService.name);
  private connected = false;

  async connect(): Promise<boolean> {
    if (this.connected) {
      return true;
    }

    try {
      await this.$connect();
      this.connected = true;
      this.logger.log('Database connected');
      return true;
    } catch (error) {
      this.connected = false;
      const message =
        error instanceof Error ? error.message : 'Unknown database error';
      this.logger.warn(
        `Database unavailable; API running in degraded mode (${message})`,
      );
      return false;
    }
  }

  isConnected(): boolean {
    return this.connected;
  }

  async onModuleInit() {
    await this.connect();
  }

  async onModuleDestroy() {
    if (this.connected) {
      await this.$disconnect();
    }
  }
}
