import { BadRequestException, Injectable, NotFoundException } from '@nestjs/common';
import { TownHallCategory } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class TownHallService {
  constructor(private readonly prisma: PrismaService) {}

  async listPublic(limit = 30) {
    return this.prisma.townHallPost.findMany({
      orderBy: [{ pinned: 'desc' }, { publishedAt: 'desc' }],
      take: Math.min(limit, 50),
      include: {
        author: { select: { id: true, name: true, role: true } },
      },
    });
  }

  async create(
    authorId: string,
    input: {
      title: string;
      body: string;
      category?: TownHallCategory;
      pinned?: boolean;
      featured?: boolean;
    },
  ) {
    const title = input.title?.trim();
    const body = input.body?.trim();
    if (!title || title.length < 4) throw new BadRequestException('Title required');
    if (!body || body.length < 20) throw new BadRequestException('Body must be at least 20 characters');

    return this.prisma.townHallPost.create({
      data: {
        authorId,
        title,
        body,
        category: input.category ?? TownHallCategory.ANNOUNCEMENT,
        pinned: input.pinned ?? false,
        featured: input.featured ?? false,
      },
      include: {
        author: { select: { id: true, name: true, role: true } },
      },
    });
  }

  async remove(id: string) {
    const post = await this.prisma.townHallPost.findUnique({ where: { id } });
    if (!post) throw new NotFoundException('Post not found');
    await this.prisma.townHallPost.delete({ where: { id } });
    return { deleted: true };
  }
}
