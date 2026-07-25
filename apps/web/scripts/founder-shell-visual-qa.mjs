import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';
import { auditEvidence } from './founder-shell-visual-qa-lib.mjs';

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
  { name: 'founder-os', path: '/founder-os' },
];

fs.mkdirSync(outputDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
const evidence = [];

async function measureKeyControl(locator, name, viewport) {
  const count = await locator.count();
  if (count === 0) return { name, found: false, visible: false };

  let candidate = locator.first();
  for (let index = 0; index < count; index += 1) {
    const current = locator.nth(index);
    if (await current.isVisible().catch(() => false)) {
      candidate = current;
      break;
    }
  }

  return candidate.evaluate((element, dimensions) => {
    const tolerance = 2;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    let visibleLeft = Math.max(0, rect.left);
    let visibleTop = Math.max(0, rect.top);
    let visibleRight = Math.min(dimensions.width, rect.right);
    let visibleBottom = Math.min(dimensions.height, rect.bottom);

    for (let ancestor = element.parentElement; ancestor; ancestor = ancestor.parentElement) {
      const ancestorStyle = getComputedStyle(ancestor);
      const ancestorRect = ancestor.getBoundingClientRect();
      if (/(auto|clip|hidden|scroll)/.test(ancestorStyle.overflowX)) {
        visibleLeft = Math.max(visibleLeft, ancestorRect.left);
        visibleRight = Math.min(visibleRight, ancestorRect.right);
      }
      if (/(auto|clip|hidden|scroll)/.test(ancestorStyle.overflowY)) {
        visibleTop = Math.max(visibleTop, ancestorRect.top);
        visibleBottom = Math.min(visibleBottom, ancestorRect.bottom);
      }
    }

    const visibleWidth = Math.max(0, visibleRight - visibleLeft);
    const visibleHeight = Math.max(0, visibleBottom - visibleTop);
    const clippedByViewport = (
      Math.max(0, Math.min(dimensions.width, rect.right) - Math.max(0, rect.left))
        < rect.width - tolerance
      || Math.max(0, Math.min(dimensions.height, rect.bottom) - Math.max(0, rect.top))
        < rect.height - tolerance
    );
    return {
      name: dimensions.name,
      found: true,
      visible: (
        rect.width > 0
        && rect.height > 0
        && style.display !== 'none'
        && style.visibility !== 'hidden'
        && Number(style.opacity) > 0
      ),
      rect: {
        left: Math.round(rect.left),
        top: Math.round(rect.top),
        right: Math.round(rect.right),
        bottom: Math.round(rect.bottom),
      },
      offscreen: (
        rect.left < -tolerance
        || rect.top < -tolerance
        || rect.right > dimensions.width + tolerance
        || rect.bottom > dimensions.height + tolerance
      ),
      clippedByViewport,
      clippedByAncestor: (
        visibleWidth < rect.width - tolerance
        || visibleHeight < rect.height - tolerance
      ) && !clippedByViewport,
      contentClipped: (
        element.scrollWidth > element.clientWidth + tolerance
        || element.scrollHeight > element.clientHeight + tolerance
      ),
    };
  }, { ...viewport, name });
}

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
    const keyControls = await Promise.all(navLabels.map((label) => measureKeyControl(
      page.getByRole('button', { name: new RegExp(`^${label}`) }),
      `navigation:${label}`,
      viewport,
    )));

    const buildButton = page.getByRole('button', { name: /^Build/ }).first();
    let buildMenu = {
      opened: false,
      workspace: false,
      founderIde: false,
      connections: false,
      insideViewport: false,
    };
    let buildMenuScreenshot = null;
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
      keyControls.push(
        await measureKeyControl(
          menu.getByRole('link', { name: /Workspace/ }),
          'build-menu:Workspace',
          viewport,
        ),
        await measureKeyControl(
          menu.getByRole('link', { name: /Founder IDE/ }),
          'build-menu:Founder IDE',
          viewport,
        ),
        await measureKeyControl(
          menu.getByRole('link', { name: /Connections/ }),
          'build-menu:Connections',
          viewport,
        ),
      );
      if (route.name === 'discover') {
        buildMenuScreenshot = `${viewport.name}-discover-build-menu.png`;
        await page.screenshot({
          path: path.join(outputDir, buildMenuScreenshot),
          fullPage: false,
        });
      }
      await buildButton.click();
      await page.waitForTimeout(120);
    }

    const screenshot = `${viewport.name}-${route.name}.png`;
    const screenshotPath = path.join(outputDir, screenshot);
    await page.screenshot({ path: screenshotPath, fullPage: false });
    const item = {
      viewport,
      route,
      status: response?.status() ?? null,
      shell,
      navVisible,
      buildMenu,
      keyControls,
      consoleErrors,
      pageErrors,
      badResponses,
      screenshot,
      screenshotBytes: fs.statSync(screenshotPath).size,
      buildMenuScreenshot,
      buildMenuScreenshotBytes: buildMenuScreenshot
        ? fs.statSync(path.join(outputDir, buildMenuScreenshot)).size
        : 0,
    };
    evidence.push(item);
    await page.close();
  }
  await context.close();
}

await browser.close();
fs.writeFileSync(path.join(outputDir, 'evidence.json'), `${JSON.stringify(evidence, null, 2)}\n`);

const audit = auditEvidence(evidence, viewports, routes);

process.stdout.write(`${JSON.stringify({
  screens: evidence.length,
  failures: audit.screens.filter((screen) => screen.issues.length > 0),
  coverageFailures: audit.coverageIssues,
  networkFailures: evidence.flatMap((item) => item.badResponses.map((response) => ({
    viewport: item.viewport.name,
    route: item.route.path,
    ...response,
  }))),
}, null, 2)}\n`);

if (audit.failed) process.exitCode = 1;
