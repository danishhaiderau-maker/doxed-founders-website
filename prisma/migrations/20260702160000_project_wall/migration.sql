-- CreateTable
CREATE TABLE "ProjectWallMessage" (
    "id" TEXT NOT NULL,
    "projectId" TEXT NOT NULL,
    "authorId" TEXT NOT NULL,
    "body" TEXT NOT NULL,
    "source" TEXT NOT NULL DEFAULT 'chat',
    "sourceRefId" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ProjectWallMessage_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ProjectWallPin" (
    "id" TEXT NOT NULL,
    "messageId" TEXT NOT NULL,
    "projectId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "kind" TEXT NOT NULL DEFAULT 'pin',
    "cost" INTEGER NOT NULL DEFAULT 0,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ProjectWallPin_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "ProjectWallMessage_projectId_createdAt_idx" ON "ProjectWallMessage"("projectId", "createdAt");

-- CreateIndex
CREATE INDEX "ProjectWallMessage_authorId_idx" ON "ProjectWallMessage"("authorId");

-- CreateIndex
CREATE INDEX "ProjectWallMessage_source_idx" ON "ProjectWallMessage"("source");

-- CreateIndex
CREATE INDEX "ProjectWallPin_projectId_idx" ON "ProjectWallPin"("projectId");

-- CreateIndex
CREATE INDEX "ProjectWallPin_userId_idx" ON "ProjectWallPin"("userId");

-- CreateIndex
CREATE UNIQUE INDEX "ProjectWallPin_messageId_key" ON "ProjectWallPin"("messageId");

-- AddForeignKey
ALTER TABLE "ProjectWallMessage" ADD CONSTRAINT "ProjectWallMessage_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "Project"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ProjectWallMessage" ADD CONSTRAINT "ProjectWallMessage_authorId_fkey" FOREIGN KEY ("authorId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ProjectWallPin" ADD CONSTRAINT "ProjectWallPin_messageId_fkey" FOREIGN KEY ("messageId") REFERENCES "ProjectWallMessage"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ProjectWallPin" ADD CONSTRAINT "ProjectWallPin_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "Project"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ProjectWallPin" ADD CONSTRAINT "ProjectWallPin_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
