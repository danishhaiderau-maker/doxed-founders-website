import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { RegulatoryClass } from '@prisma/client';
import {
  BLOCKED_REGULATORY_CLASSES,
  CLEARED_REGULATORY_CLASSES,
  isPhase15TrustLayerEnabled,
  isRegulatoryClassBlocked,
} from '../phase15/phase15.constants';
import { PrismaService } from '../prisma/prisma.service';

export type RegulatoryQuestionnaireAnswers = {
  tokenPurpose: 'COMMUNITY' | 'UTILITY' | 'GOVERNANCE' | 'FUNDRAISING' | 'UNKNOWN';
  hasEquityComponent?: boolean;
  offersReturns?: boolean;
  investorGeography?: string;
  acknowledgesNotLegalAdvice?: boolean;
  acknowledgesCounselForCapitalRaise?: boolean;
};

export type RegulatoryFeatureMatrix = {
  proofRaise: boolean;
  metadataPreview: boolean;
  capitalRaiseUi: boolean;
  governanceDisclosure: boolean;
};

const QUESTIONNAIRE_FIELDS: (keyof RegulatoryQuestionnaireAnswers)[] = [
  'tokenPurpose',
  'hasEquityComponent',
  'offersReturns',
  'investorGeography',
  'acknowledgesNotLegalAdvice',
  'acknowledgesCounselForCapitalRaise',
];

@Injectable()
export class RegulatoryService {
  constructor(private readonly prisma: PrismaService) {}

  isEnabled(): boolean {
    return isPhase15TrustLayerEnabled();
  }

  classifyFromAnswers(answers: RegulatoryQuestionnaireAnswers): RegulatoryClass {
    if (answers.hasEquityComponent || answers.offersReturns) {
      return RegulatoryClass.RESTRICTED;
    }
    if (answers.tokenPurpose === 'FUNDRAISING') {
      return RegulatoryClass.CAPITAL_RAISE;
    }
    if (answers.tokenPurpose === 'GOVERNANCE') {
      return RegulatoryClass.GOVERNANCE;
    }
    if (answers.tokenPurpose === 'UTILITY') {
      return RegulatoryClass.UTILITY;
    }
    if (answers.tokenPurpose === 'COMMUNITY') {
      return RegulatoryClass.COMMUNITY;
    }
    return RegulatoryClass.RESTRICTED;
  }

  featureMatrix(regulatoryClass: RegulatoryClass): RegulatoryFeatureMatrix {
    const blocked = isRegulatoryClassBlocked(regulatoryClass);
    return {
      proofRaise: !blocked,
      metadataPreview: !blocked,
      capitalRaiseUi: regulatoryClass === RegulatoryClass.CAPITAL_RAISE,
      governanceDisclosure: regulatoryClass === RegulatoryClass.GOVERNANCE,
    };
  }

  async getClassification(projectId: string) {
    const project = await this.prisma.project.findUnique({
      where: { id: projectId },
      include: { regulatoryQuestionnaire: true },
    });
    if (!project) throw new NotFoundException('Project not found');

    return {
      enabled: this.isEnabled(),
      regulatoryClass: project.regulatoryClass,
      classifiedAt: project.regulatoryClassifiedAt,
      questionnaireCompletedAt: project.regulatoryQuestionnaireCompletedAt,
      features: this.featureMatrix(project.regulatoryClass),
      blocked: isRegulatoryClassBlocked(project.regulatoryClass),
      cleared: (CLEARED_REGULATORY_CLASSES as readonly string[]).includes(project.regulatoryClass),
      questionnaire: project.regulatoryQuestionnaire
        ? {
            completedAt: project.regulatoryQuestionnaire.completedAt,
            answers: project.regulatoryQuestionnaire.answers,
          }
        : null,
    };
  }

  async getBySlug(slug: string) {
    const project = await this.prisma.project.findUnique({ where: { slug } });
    if (!project) throw new NotFoundException('Project not found');
    return this.getClassification(project.id);
  }

  async submitQuestionnaire(
    projectSlug: string,
    userId: string,
    answers: RegulatoryQuestionnaireAnswers,
  ) {
    if (!this.isEnabled()) {
      throw new BadRequestException('Phase 1.5 Trust Layer is not enabled');
    }

    if (!answers.acknowledgesNotLegalAdvice) {
      throw new BadRequestException('You must acknowledge this is not legal advice');
    }

    const project = await this.prisma.project.findUnique({
      where: { slug: projectSlug },
      include: { founder: true },
    });
    if (!project) throw new NotFoundException('Project not found');
    if (project.founder?.userId !== userId) {
      throw new ForbiddenException('Only the project founder can submit the regulatory questionnaire');
    }

    const regulatoryClass = this.classifyFromAnswers(answers);
    const now = new Date();

    await this.prisma.$transaction(async (tx) => {
      await tx.regulatoryQuestionnaire.upsert({
        where: { projectId: project.id },
        create: {
          projectId: project.id,
          answers: answers as object,
          completedAt: now,
        },
        update: {
          answers: answers as object,
          completedAt: now,
        },
      });

      await tx.project.update({
        where: { id: project.id },
        data: {
          regulatoryClass,
          regulatoryClassifiedAt: now,
          regulatoryQuestionnaireCompletedAt: now,
        },
      });
    });

    return {
      success: true,
      regulatoryClass,
      features: this.featureMatrix(regulatoryClass),
      blocked: (BLOCKED_REGULATORY_CLASSES as readonly string[]).includes(regulatoryClass),
    };
  }

  getQuestionnaireTemplate() {
    return {
      enabled: this.isEnabled(),
      fields: QUESTIONNAIRE_FIELDS,
      purposes: ['COMMUNITY', 'UTILITY', 'GOVERNANCE', 'FUNDRAISING', 'UNKNOWN'],
      disclaimer:
        'This questionnaire helps classify your project for platform feature gates. It is not legal advice.',
    };
  }
}
