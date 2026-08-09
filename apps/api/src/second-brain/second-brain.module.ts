import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { FounderNodeModule } from '../founder-node/founder-node.module';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { SecondBrainCallerGuard } from './second-brain-caller.guard';
import { SecondBrainController } from './second-brain.controller';
import { SecondBrainService } from './second-brain.service';

/**
 * Second Brain — cheap expert cascade for Founder IDE consults.
 * Gemini Flash primary → OpenAI gpt-4o-mini / Luna-class if keyed → GLM last-resort.
 * Never DeepSeek (Builder / Platform Brain only).
 *
 * Desktop Founder Next calls POST /api/second-brain/critique with Founder Node
 * or JWT auth; platform keys never leave Railway / encrypted admin storage.
 */
@Module({
  imports: [FounderOsModule, FounderNodeModule, AuthModule],
  controllers: [SecondBrainController],
  providers: [SecondBrainService, SecondBrainCallerGuard],
  exports: [SecondBrainService],
})
export class SecondBrainModule {}
