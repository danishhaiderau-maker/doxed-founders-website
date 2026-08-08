import { Module } from '@nestjs/common';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { SecondBrainController } from './second-brain.controller';
import { SecondBrainService } from './second-brain.service';

/**
 * Second Brain — cheap expert cascade for Founder IDE consults.
 * Gemini Flash primary → OpenAI gpt-4o-mini / Luna-class if keyed → GLM last-resort.
 * Never DeepSeek (Builder / Platform Brain only).
 */
@Module({
  imports: [FounderOsModule],
  controllers: [SecondBrainController],
  providers: [SecondBrainService],
  exports: [SecondBrainService],
})
export class SecondBrainModule {}
