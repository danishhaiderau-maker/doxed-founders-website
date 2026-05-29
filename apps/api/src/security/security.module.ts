import { Module, forwardRef } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { SecurityController } from './security.controller';
import { SecurityService } from './security.service';

@Module({
  imports: [forwardRef(() => AuthModule)],
  controllers: [SecurityController],
  providers: [SecurityService],
  exports: [SecurityService],
})
export class SecurityModule {}
