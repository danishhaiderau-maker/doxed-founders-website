import { Injectable, Logger, Module, OnModuleInit } from '@nestjs/common';
import { CursorAdapter, FilesystemAdapter, LocalShellAdapter } from './adapters';
import { ExecutionManagerController } from './execution-manager.controller';
import { ExecutionManagerService } from './execution-manager.service';
import type { ExecutionAdapter } from './execution-manager.types';

/**
 * Internal helper that holds the adapter instances. Lives in the
 * module's providers so the DI container constructs them once, and the
 * module's onModuleInit can iterate them without knowing their concrete
 * types.
 */
@Injectable()
class Bootstrapper {
  constructor(
    private readonly localShell: LocalShellAdapter,
    private readonly filesystem: FilesystemAdapter,
    private readonly cursor: CursorAdapter,
  ) {}

  getAdapters(): ExecutionAdapter[] {
    return [this.localShell, this.filesystem, this.cursor];
  }
}

/**
 * Execution Manager module — kernel service #4.
 *
 * Wires the service + the read-only controller, then on init registers
 * the adapters Phase 3 ships with (terminal, filesystem, cursor stub).
 * Later phases add more adapters here without touching the service —
 * the registry is the seam.
 *
 * No Prisma / no application-code imports: this module is stateless.
 * Decision logging goes through Flight Recorder (a separate kernel
 * module the application layer composes with), not a new table here.
 */
@Module({
  controllers: [ExecutionManagerController],
  providers: [
    ExecutionManagerService,
    // Adapters are injectable so Nest manages their lifecycle. The
    // bootstrapper below pulls them out of the DI container to register.
    LocalShellAdapter,
    FilesystemAdapter,
    CursorAdapter,
    Bootstrapper,
  ],
  exports: [ExecutionManagerService],
})
export class ExecutionManagerModule implements OnModuleInit {
  private readonly logger = new Logger(ExecutionManagerModule.name);

  constructor(
    private readonly executionManager: ExecutionManagerService,
    private readonly bootstrapper: Bootstrapper,
  ) {}

  async onModuleInit(): Promise<void> {
    // Register the adapters that ship with this module. Bootstrapper
    // owns the connect() calls so failures in one adapter don't block
    // the others from registering.
    const adapters = this.bootstrapper.getAdapters();
    for (const adapter of adapters) {
      this.executionManager.registerAdapter(adapter);
      try {
        await adapter.connect();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.logger.warn(
          `adapter "${adapter.target}" failed to connect: ${msg}`,
        );
      }
    }
  }
}
