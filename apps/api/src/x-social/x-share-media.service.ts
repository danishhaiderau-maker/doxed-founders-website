import { Injectable, Logger } from '@nestjs/common';
import * as fs from 'fs';
import * as path from 'path';

export type ShareMediaSide = 'pump' | 'dump';

@Injectable()
export class XShareMediaService {
  private readonly logger = new Logger(XShareMediaService.name);
  private pumpFiles: string[] | null = null;
  private dumpFiles: string[] | null = null;

  private assetsDir(): string {
    return path.join(__dirname, '..', 'assets', 'x-share');
  }

  private listImages(side: ShareMediaSide): string[] {
    const cached = side === 'pump' ? this.pumpFiles : this.dumpFiles;
    if (cached) return cached;

    const dir = path.join(this.assetsDir(), side);
    if (!fs.existsSync(dir)) {
      this.logger.warn(`X share media folder missing: ${dir}`);
      return [];
    }

    const files = fs
      .readdirSync(dir)
      .filter((f) => f.endsWith('.png'))
      .map((f) => path.join(dir, f));

    if (side === 'pump') this.pumpFiles = files;
    else this.dumpFiles = files;

    return files;
  }

  /** Random pump (gain) or dump (loss) image with corner numbers already masked. */
  pickImageBuffer(side: ShareMediaSide): Buffer | null {
    const files = this.listImages(side);
    if (!files.length) return null;

    const pick = files[Math.floor(Math.random() * files.length)]!;
    try {
      return fs.readFileSync(pick);
    } catch (err) {
      this.logger.warn(`Failed to read share image ${pick}: ${err}`);
      return null;
    }
  }

  imageCount(): { pump: number; dump: number } {
    return {
      pump: this.listImages('pump').length,
      dump: this.listImages('dump').length,
    };
  }
}
