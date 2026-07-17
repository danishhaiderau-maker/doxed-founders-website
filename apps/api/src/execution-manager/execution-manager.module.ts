import { Injectable, Logger, Module, OnModuleInit, forwardRef } from '@nestjs/common';
import {
  CursorAdapter,
  FilesystemAdapter,
  FounderIdeAdapter,
  LocalShellAdapter,
} from './adapters';
import { ExecutionManagerController } from './execution-manager.controller';
import { ExecutionManagerService } from './execution-manager.service';
import type { ExecutionAdapter } from './execution-manager.types';
import { FounderNodeModule } from '../founder-node/founder-node.module';

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
    private readonly founderIde: FounderIdeAdapter,
  ) {}

  getAdapters(): ExecutionAdapter[] {
    return [this.localShell, this.filesystem, this.cursor, this.founderIde];
  }
}

/**
 * Execution Manager module — kernel service #4.
 *
 * Wires the service + the read-only controller, then on init registers
 * the adapters Phase 3 ships with (terminal, filesystem, cursor + Founder IDE).
 *
 * Phase 3 — FounderIdeAdapter now depends on FounderNodeService to look up
 * the install's IDE handshake state. We import FounderNodeModule with
 * `forwardRef` because the broader dependency graph already has cycles
 * (IdeBridge → Builder → FounderNode → IdeBridge); adding one more edge
 * in this direction is consistent with how the rest of the kernel composes.
 *
 * No Prisma / no application-code imports: this module is stateless.
 * Decision logging goes through Flight Recorder (a separate kernel
 * module the application layer composes with), not a new table here.
 */
@Module({
  imports: [forwardRef(() => FounderNodeModule)],
  controllers: [ExecutionManagerController],
  providers: [
    ExecutionManagerService,
    // Adapters are injectable so Nest manages their lifecycle. The
    // bootstrapper below pulls them out of the DI container to register.
    // LocalShell + Filesystem must be constructed before Cursor / FounderIde
    // so those adapters can inject them as local delegates.
    LocalShellAdapter,
    FilesystemAdapter,
    CursorAdapter,
    FounderIdeAdapter,
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
