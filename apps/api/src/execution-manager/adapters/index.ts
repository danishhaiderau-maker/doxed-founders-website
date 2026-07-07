/**
 * Adapter barrel. Re-exports every concrete ExecutionAdapter so the
 * module wiring (`onModuleInit`) can register them without reaching
 * into individual files.
 *
 * To add a new target: implement ExecutionAdapter in a new file here,
 * add it to this barrel, then register an instance in the
 * execution-manager.module.ts onModuleInit loop.
 */

export { CursorAdapter } from './cursor.adapter';
export { FilesystemAdapter } from './filesystem.adapter';
export { LocalShellAdapter } from './local-shell.adapter';
