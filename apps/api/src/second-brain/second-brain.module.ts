import { Module } from '@nestjs/common';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { SecondBrainService } from './second-brain.service';

/**
 * Second Brain — cheap expert cascade for Founder IDE consults.
 * Gemini Flash primary, OpenAI mini / DeepSeek fallback, GLM last-resort only.
 */
@Module({
  imports: [FounderOsModule],
  providers: [SecondBrainService],
  exports: [SecondBrainService],
})
export class SecondBrainModule {}
