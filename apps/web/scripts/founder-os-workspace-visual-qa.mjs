import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const baseUrl = process.env.FOUNDER_WEB_QA_URL || 'http://127.0.0.1:3100';
const outputDir = path.resolve(
  process.env.FOUNDER_OS_QA_DIR
    || path.join(process.env.TEMP || process.cwd(), 'FounderIDE', 'visual-qa', 'founder-os-workspace'),
);

const viewports = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'tablet', width: 1024, height: 768 },
  { name: 'mobile', width: 390, height: 844 },
];

const fixtures = new Map([
  ['/api/auth/session', {
    user: {
      id: 'qa-founder',
      name: 'Danish',
      email: 'founder@example.com',
      role: 'ADMIN',
    },
    accessToken: 'visual-qa-token',
    expires: '2099-01-01T00:00:00.000Z',
  }],
  ['/api/founder-os/dashboard', {
    founder: {
      id: 'qa-founder',
      tier: 'DOXXED',
      contributorLevel: 'VERIFIED_BUILDER',
      reputationPoints: 2840,
      lifetimeContributionEarned: 412,
    },
    user: {
      reputationPoints: 2840,
      contributorLevel: 'VERIFIED_BUILDER',
    },
    connectedApps: [
      { provider: 'github', label: 'GitHub', connectedAt: '2026-07-21T08:30:00.000Z' },
      { provider: 'vercel', label: 'Vercel', connectedAt: '2026-07-21T08:31:00.000Z' },
      { provider: 'railway', label: 'Railway', connectedAt: '2026-07-21T08:32:00.000Z' },
    ],
  }],
  ['/api/founder-plans', {
    currency: 'usd',
    plans: [
      {
        id: 'free',
        priceCentsMonthly: 0,
        weeklyWeightedUnits: 200000,
        checkoutAvailable: false,
      },
      {
        id: 'builder',
        priceCentsMonthly: 3500,
        weeklyWeightedUnits: 5000000,
        checkoutAvailable: true,
      },
      {
        id: 'team',
        priceCentsMonthly: null,
        weeklyWeightedUnits: null,
        checkoutAvailable: false,
        message: 'Contact Founder OS for pooled team access.',
      },
    ],
  }],
  ['/api/founder-plans/me', {
    plan: 'free',
    quotaOwnerKey: 'user:qa-founder',
    weeklyWeightedUnitCap: 200000,
    currentPeriodStart: '2026-07-20T00:00:00.000Z',
    currentPeriodEnd: '2026-07-27T00:00:00.000Z',
    priceCentsMonthly: 0,
    teamId: null,
    teamName: null,
    teamRole: null,
    coordination: false,
    remoteControl: false,
    rolesAndAudit: false,
    requiresXVerification: false,
  }],
  ['/api/account/founder-promo', {
    plan: 'free',
    priceCentsMonthly: 0,
    teamId: null,
    teamName: null,
    teamRole: null,
    coordination: false,
    remoteControl: false,
    rolesAndAudit: false,
    unit: 'weighted_tokens',
    weightsVersion: 'founder-wtu-v1',
    enabled: true,
    eligible: true,
    founderRegistered: true,
    promoStartedAt: '2026-07-20T00:00:00.000Z',
    expiresAt: '2026-07-27T00:00:00.000Z',
    daysRemaining: 2,
    tokenCap: 200000,
    tokensUsed: 46320,
    reservedWeightedUnits: 1200,
    tokensRemaining: 152480,
    exhausted: false,
    message: null,
    providers: ['deepseek'],
  }],
  ['/api/debug-squasher/latest', { run: null }],
  ['/api/debug-squasher/consent', { consent: 'accepted' }],
  ['/api/idea-validator/latest-for-user', null],
  ['/api/feed/flashes', []],
  ['/api/feed/hub', {
    category: 'all',
    terminalTab: 'all',
    projectSlug: null,
    pulse: [],
    hotQuestions: [],
    scoutListings: [],
    stream: [],
    terminal: null,
    counts: { unified: 0, terminal: 0, merged: 0 },
  }],
  ['/api/admin-control/share-footer', { footer: '' }],
  ['/api/messages/unread-count', { count: 0 }],
  ['/api/messages/threads', []],
  ['/api/notifications', []],
  ['/api/notifications/unread-count', { count: 0 }],
  ['/api/account/overview', {
    userId: 'qa-founder',
    username: 'Danish',
    platformHandle: 'danish',
    messagingAddress: '@danish',
    twitterHandle: null,
    twitterUrl: null,
    canEditPlatformHandle: true,
    hasTwitterConnected: true,
    xVerified: true,
    identityBadge: 'Doxxed founder',
    email: 'founder@example.com',
    avatarUrl: null,
    joinedAt: '2026-01-01T00:00:00.000Z',
    platformRole: 'ADMIN',
    gamifiedRole: {
      id: 'admin',
      label: 'Platform Admin',
      description: 'Full platform administration access.',
      color: 'rose',
    },
    isAdmin: true,
    adminBanner: null,
    authMethods: [],
    reputation: {
      userId: 'qa-founder',
      displayName: 'Danish',
      twitterHandle: null,
      reputationPoints: 2840,
      lifetimeContributionEarned: 412,
      contributorLevel: 4,
      rank: 1,
      totalParticipants: 128,
      totalPoints: 91000,
      airdropPoolPercent: 0,
      supplyPercent: 0,
      estimatedTokens: 0,
      estimatedUsd: 0,
    },
    builderStatus: {
      isFounder: true,
      badge: 'Doxxed founder',
      presenceLevel: 'VERIFIED_BUILDER',
      founderSlug: 'danish',
    },
    followingCount: 12,
    followersCount: 84,
  }],
]);

function respondJson(route, payload) {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  });
}

fs.mkdirSync(outputDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
const evidence = [];

for (const viewport of viewports) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const consoleErrors = [];
  const pageErrors = [];
  const badResponses = [];
  const unexpectedApiRequests = [];

  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('response', (response) => {
    if (response.status() >= 400) {
      badResponses.push({ status: response.status(), url: response.url() });
    }
  });

  await page.route('**/api/**', async (route) => {
    const requestUrl = new URL(route.request().url());
    const fixture = fixtures.get(requestUrl.pathname);
    if (fixture !== undefined || fixtures.has(requestUrl.pathname)) {
      await respondJson(route, fixture);
      return;
    }

    unexpectedApiRequests.push({
      method: route.request().method(),
      path: requestUrl.pathname,
    });
    await respondJson(route, {});
  });

  const response = await page.goto(`${baseUrl}/founder-os`, {
    waitUntil: 'domcontentloaded',
    timeout: 45_000,
  });
  await page.getByRole('heading', { name: 'Build, connect, and ship' }).waitFor({
    timeout: 20_000,
  });
  await page.getByText('Founder Free', { exact: true }).first().waitFor({
    timeout: 20_000,
  });
  await page.waitForTimeout(350);

  const layout = await page.evaluate(() => {
    const tolerance = 2;
    const overflowElements = [...document.querySelectorAll('body *')]
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return {
          tag: element.tagName.toLowerCase(),
          className: typeof element.className === 'string' ? element.className.slice(0, 180) : '',
          text: (element.textContent || '').trim().slice(0, 80),
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          width: Math.round(rect.width),
        };
      })
      .filter((rect) => (
        rect.width > 0
        && (rect.left < -tolerance || rect.right > window.innerWidth + tolerance)
      ))
      .slice(0, 16);

    return {
      title: document.title,
      horizontalOverflow: document.documentElement.scrollWidth - window.innerWidth,
      overflowElements,
      bodyText: document.body.innerText.slice(0, 6000),
    };
  });

  const requiredText = [
    'Founder workspace',
    'Build, connect, and ship',
    'Control desktop',
    'Connect services',
    'Plan and usage',
    'Founder Free',
    '200,000 managed weighted units each week',
    'Founder Free usage',
    'Personal and local AI',
    'No daily quality review is available yet.',
    'GitHub',
    'Vercel',
    'Railway',
  ];
  const missingText = requiredText.filter((text) => !layout.bodyText.includes(text));
  const forbiddenText = [
    'Void',
    'Phase ',
    'unlimited AI',
    'Join Token Launch',
    'debug-squasher',
    '\u00c2',
    '\u00e2',
  ];
  const forbiddenMatches = forbiddenText.filter((text) => layout.bodyText.includes(text));
  const controls = {
    continueBuilding: await page.getByRole('link', { name: /Continue building/ }).isVisible(),
    controlDesktop: await page.getByRole('link', { name: /Control desktop/ }).isVisible(),
    connectServices: await page.getByRole('link', { name: /Connect services/ }).isVisible(),
    manageAi: await page.getByRole('link', { name: /Manage AI/ }).isVisible(),
    planUsage: await page.getByRole('link', { name: /Plan and usage/ }).isVisible(),
  };

  const screenshot = path.join(outputDir, `${viewport.name}-founder-workspace.png`);
  await page.screenshot({ path: screenshot, fullPage: true });
  evidence.push({
    viewport,
    status: response?.status() ?? null,
    layout,
    controls,
    missingText,
    forbiddenMatches,
    consoleErrors,
    pageErrors,
    badResponses,
    unexpectedApiRequests,
    screenshot,
    screenshotBytes: fs.statSync(screenshot).size,
  });

  await context.close();
}

await browser.close();
fs.writeFileSync(
  path.join(outputDir, 'evidence.json'),
  `${JSON.stringify(evidence, null, 2)}\n`,
);

const failures = evidence.filter((screen) => (
  screen.status !== 200
  || screen.layout.horizontalOverflow > 2
  || screen.layout.overflowElements.length > 0
  || screen.missingText.length > 0
  || screen.forbiddenMatches.length > 0
  || screen.consoleErrors.length > 0
  || screen.pageErrors.length > 0
  || screen.badResponses.length > 0
  || screen.unexpectedApiRequests.length > 0
  || Object.values(screen.controls).some((visible) => !visible)
  || screen.screenshotBytes < 30_000
));

process.stdout.write(`${JSON.stringify({
  outputDir,
  screens: evidence.length,
  failures,
}, null, 2)}\n`);

if (failures.length > 0) process.exitCode = 1;
