import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const baseUrl = process.env.FOUNDER_WEB_QA_URL || 'http://127.0.0.1:3100';
const outputDir = path.resolve(
  process.env.FOUNDER_WEB_QA_DIR || 'apps/web/artifacts/founder-shell-visual-qa',
);

const viewports = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'tablet', width: 1024, height: 768 },
  { name: 'mobile', width: 390, height: 844 },
];

const routes = [
  { name: 'discover', path: '/discover' },
  { name: 'agents', path: '/agent-hub/conservative-btc' },
  { name: 'downloads', path: '/downloads' },
  { name: 'trade', path: '/paper-trading' },
  { name: 'account', path: '/account' },
];

fs.mkdirSync(outputDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
const evidence = [];

for (const viewport of viewports) {
  const context = await browser.newContext({ viewport });
  for (const route of routes) {
    const page = await context.newPage();
    const consoleErrors = [];
    const pageErrors = [];
    const badResponses = [];
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('response', (response) => {
      if (response.status() >= 400) {
        badResponses.push({ status: response.status(), url: response.url() });
      }
    });

    const response = await page.goto(`${baseUrl}${route.path}`, {
      waitUntil: 'domcontentloaded',
      timeout: 45_000,
    });
    await page.waitForTimeout(2_000);

    const shell = await page.evaluate(() => {
      const header = document.querySelector('header');
      const text = header?.innerText || document.body.innerText;
      return {
        title: document.title,
        bodyText: document.body.innerText.slice(0, 2_000),
        headerText: text.slice(0, 1_000),
        horizontalOverflow: document.documentElement.scrollWidth - window.innerWidth,
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
          .filter((rect) => rect.width > 0 && (rect.left < -2 || rect.right > window.innerWidth + 2))
          .slice(0, 12),
        bodyBackground: getComputedStyle(document.body).backgroundColor,
      };
    });

    const navLabels = ['Build', 'Discover', 'Trade'];
    const navVisible = Object.fromEntries(
      await Promise.all(navLabels.map(async (label) => [
        label,
        await page.getByRole('button', { name: new RegExp(`^${label}`) }).count() > 0,
      ])),
    );

    const buildButton = page.getByRole('button', { name: /^Build/ }).first();
    let buildMenu = {
      opened: false,
      workspace: false,
      founderIde: false,
      connections: false,
      insideViewport: false,
    };
    if (await buildButton.count()) {
      await buildButton.click();
      await page.waitForTimeout(180);
      const menu = page.locator('[role="menu"][aria-label="Build menu"]:visible').first();
      const menuBox = await menu.boundingBox();
      buildMenu = {
        opened: true,
        workspace: await page.getByRole('link', { name: /Workspace/ }).count() > 0,
        founderIde: await page.getByRole('link', { name: /Founder IDE/ }).count() > 0,
        connections: await page.getByRole('link', { name: /Connections/ }).count() > 0,
        insideViewport: Boolean(
          menuBox
          && menuBox.x >= -1
          && menuBox.y >= -1
          && menuBox.x + menuBox.width <= viewport.width + 1
          && menuBox.y + menuBox.height <= viewport.height + 1
        ),
      };
      if (route.name === 'discover') {
        await page.screenshot({
          path: path.join(outputDir, `${viewport.name}-discover-build-menu.png`),
          fullPage: false,
        });
      }
      await buildButton.click();
      await page.waitForTimeout(120);
    }

    const screenshot = `${viewport.name}-${route.name}.png`;
    await page.screenshot({ path: path.join(outputDir, screenshot), fullPage: false });
    const item = {
      viewport,
      route,
      status: response?.status() ?? null,
      shell,
      navVisible,
      buildMenu,
      consoleErrors,
      pageErrors,
      badResponses,
      screenshot,
    };
    evidence.push(item);
    await page.close();
  }
  await context.close();
}

await browser.close();
fs.writeFileSync(path.join(outputDir, 'evidence.json'), `${JSON.stringify(evidence, null, 2)}\n`);

const failures = evidence.filter((item) => (
  item.status !== 200
  || item.shell.horizontalOverflow > 2
  || Object.values(item.navVisible).some((visible) => !visible)
  || Object.values(item.buildMenu).some((ready) => !ready)
  || item.consoleErrors.length > 0
  || item.pageErrors.length > 0
));

process.stdout.write(`${JSON.stringify({
  screens: evidence.length,
  failures: failures.map((item) => ({
    viewport: item.viewport.name,
    route: item.route.path,
    status: item.status,
    overflow: item.shell.horizontalOverflow,
    navVisible: item.navVisible,
    buildMenu: item.buildMenu,
    consoleErrors: item.consoleErrors,
    pageErrors: item.pageErrors,
    badResponses: item.badResponses,
  })),
  networkFailures: evidence.flatMap((item) => item.badResponses.map((response) => ({
    viewport: item.viewport.name,
    route: item.route.path,
    ...response,
  }))),
}, null, 2)}\n`);

if (failures.length > 0) process.exitCode = 1;
