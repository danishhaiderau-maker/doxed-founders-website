import { Module } from '@nestjs/common';
import { SecondBrainService } from './second-brain.service';

/**
 * Second Brain module.
 *
 * Houses the SOLE sanctioned GLM call site in the codebase
 * (SecondBrainService). See second-brain.service.ts for the hard cost rule.
 *
 * Note: SecondBrainService depends on FounderBrainProvidersService, which is
 * exported globally by FounderAiRuntimeModule, so this module does not need
 * to import it explicitly.
 */
@Module({
  providers: [SecondBrainService],
  exports: [SecondBrainService],
})
export class SecondBrainModule {}