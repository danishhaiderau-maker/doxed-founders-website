import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const baseUrl = process.env.FOUNDER_WEB_QA_URL || 'http://127.0.0.1:3100';
const outputDir = path.resolve(
  process.env.FOUNDER_DOWNLOADS_QA_DIR
    || path.join(process.env.TEMP || process.cwd(), 'FounderIDE', 'visual-qa', 'founder-downloads'),
);

const viewports = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
];

const fixtures = new Map([
  ['/api/auth/session', {}],
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
]);

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

  await page.route(`${baseUrl}/api/**`, async (route) => {
    const requestUrl = new URL(route.request().url());
    const fixture = fixtures.get(requestUrl.pathname);
    if (fixture === undefined && !fixtures.has(requestUrl.pathname)) {
      unexpectedApiRequests.push({
        method: route.request().method(),
        path: requestUrl.pathname,
      });
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(fixture ?? {}),
    });
  });

  const response = await page.goto(`${baseUrl}/downloads`, {
    waitUntil: 'domcontentloaded',
    timeout: 120_000,
  });
  await page.getByRole('heading', { name: 'All downloads' }).waitFor({
    timeout: 30_000,
  });
  await page.getByRole('heading', { name: /Founder IDE.*one desktop app/ }).waitFor({
    timeout: 30_000,
  });
  await page.waitForTimeout(350);

  const layout = await page.evaluate(() => {
    const tolerance = 2;
    return {
      horizontalOverflow: document.documentElement.scrollWidth - window.innerWidth,
      matchingHeadings: [...document.querySelectorAll('h1, h2')]
        .map((element) => element.textContent?.trim())
        .filter(Boolean),
      bodyText: document.body.innerText.slice(0, 12_000),
      overflowElements: [...document.querySelectorAll('body *')]
        .map((element) => {
          const rect = element.getBoundingClientRect();
          return {
            tag: element.tagName.toLowerCase(),
            className: typeof element.className === 'string' ? element.className.slice(0, 160) : '',
            left: Math.round(rect.left),
            right: Math.round(rect.right),
            width: Math.round(rect.width),
          };
        })
        .filter((rect) => (
          rect.width > 0
          && (rect.left < -tolerance || rect.right > window.innerWidth + tolerance)
        ))
        .slice(0, 12),
    };
  });

  const requiredText = [
    'All downloads',
    'Founder OS Mobile',
    'Founder IDE \u2014 one desktop app',
    'Founder Node is an embedded background capability',
    'unsigned internal beta',
  ];
  const missingText = requiredText.filter((text) => !layout.bodyText.includes(text));
  const forbiddenText = [
    'Founder Stack',
    'Founder Copilot',
    'Cursor Pro saved',
  ];
  const forbiddenMatches = forbiddenText.filter((text) => layout.bodyText.includes(text));
  const screenshot = path.join(outputDir, `${viewport.name}-downloads.png`);
  await page.screenshot({ path: screenshot, fullPage: false });

  evidence.push({
    viewport,
    status: response?.status() ?? null,
    layout,
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

const failures = evidence.filter((item) => (
  item.status !== 200
  || item.layout.horizontalOverflow > 2
  || item.layout.overflowElements.length > 0
  || item.missingText.length > 0
  || item.forbiddenMatches.length > 0
  || item.consoleErrors.length > 0
  || item.pageErrors.length > 0
  || item.badResponses.length > 0
  || item.unexpectedApiRequests.length > 0
));

process.stdout.write(`${JSON.stringify({
  outputDir,
  screens: evidence.length,
  failures,
}, null, 2)}\n`);

if (failures.length > 0) process.exitCode = 1;
