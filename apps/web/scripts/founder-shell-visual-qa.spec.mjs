import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  auditEvidence,
  evidenceCoverageIssues,
  itemIssues,
  layoutIssues,
  networkErrorIssues,
  runtimeErrorIssues,
} from './founder-shell-visual-qa-lib.mjs';

const viewports = [
  { name: 'desktop' },
  { name: 'mobile' },
];
const routes = [
  { name: 'discover', path: '/discover' },
  { name: 'account', path: '/account' },
];

function passingItem(viewport = 'desktop', route = 'account') {
  return {
    viewport: { name: viewport },
    route: { name: route, path: `/${route}` },
    status: 200,
    shell: { horizontalOverflow: 0 },
    navVisible: { Build: true, Discover: true, Trade: true },
    buildMenu: {
      opened: true,
      workspace: true,
      founderIde: true,
      connections: true,
      insideViewport: true,
    },
    keyControls: [
      {
        name: 'Build',
        found: true,
        visible: true,
        offscreen: false,
        clippedByViewport: false,
        clippedByAncestor: false,
        contentClipped: false,
      },
    ],
    consoleErrors: [],
    pageErrors: [],
    badResponses: [],
    screenshot: `${viewport}-${route}.png`,
    screenshotBytes: 1_024,
    ...(route === 'discover'
      ? {
          buildMenuScreenshot: `${viewport}-discover-build-menu.png`,
          buildMenuScreenshotBytes: 512,
        }
      : {}),
  };
}

describe('Founder shell visual QA predicates', () => {
  it('fails every HTTP response at or above 400', () => {
    const issues = networkErrorIssues({
      badResponses: [
        { status: 399, url: 'https://example.test/redirect' },
        { status: 401, url: 'https://example.test/private' },
        { status: 503, url: 'https://example.test/api' },
      ],
    });
    assert.deepEqual(issues.map((issue) => issue.code), ['bad-response', 'bad-response']);
    assert.match(issues[0].detail, /401/);
  });

  it('reports console and uncaught page errors independently', () => {
    assert.deepEqual(
      runtimeErrorIssues({ consoleErrors: ['console failed'], pageErrors: ['page failed'] }),
      [
        { code: 'console-error', detail: 'console failed' },
        { code: 'page-error', detail: 'page failed' },
      ],
    );
  });

  it('allows subpixel drift but rejects overflow and clipped key controls', () => {
    assert.deepEqual(layoutIssues({
      shell: { horizontalOverflow: 2 },
      keyControls: [],
    }), []);

    const issues = layoutIssues({
      shell: { horizontalOverflow: 12 },
      keyControls: [
        { name: 'Build', found: false },
        {
          name: 'Trade',
          found: true,
          visible: true,
          offscreen: true,
          clippedByViewport: true,
          clippedByAncestor: false,
          contentClipped: false,
        },
      ],
    });
    assert.deepEqual(issues.map((issue) => issue.code), [
      'horizontal-overflow',
      'key-control-missing',
      'key-control-offscreen',
      'key-control-clipped',
    ]);
  });

  it('combines document, navigation, menu, network, runtime, and layout failures', () => {
    const item = passingItem();
    item.status = 500;
    item.navVisible.Trade = false;
    item.buildMenu.insideViewport = false;
    item.badResponses.push({ status: 404, url: 'https://example.test/missing' });
    item.consoleErrors.push('boom');
    item.shell.horizontalOverflow = 9;
    assert.deepEqual(itemIssues(item).map((issue) => issue.code), [
      'document-status',
      'navigation-missing',
      'build-menu-invalid',
      'bad-response',
      'console-error',
      'horizontal-overflow',
    ]);
  });

  it('requires one nonempty screenshot per viewport and route plus Discover menu evidence', () => {
    const evidence = [
      passingItem('desktop', 'discover'),
      passingItem('desktop', 'account'),
      passingItem('mobile', 'discover'),
      passingItem('mobile', 'account'),
    ];
    assert.deepEqual(evidenceCoverageIssues(evidence, viewports, routes), []);

    evidence[0].buildMenuScreenshotBytes = 0;
    evidence[3].screenshotBytes = 0;
    evidence.pop();
    const codes = evidenceCoverageIssues(evidence, viewports, routes).map((issue) => issue.code);
    assert.deepEqual(codes, ['menu-screenshot-missing', 'evidence-missing']);
  });

  it('returns one release-gate verdict for the complete evidence matrix', () => {
    const evidence = [
      passingItem('desktop', 'discover'),
      passingItem('desktop', 'account'),
      passingItem('mobile', 'discover'),
      passingItem('mobile', 'account'),
    ];
    assert.equal(auditEvidence(evidence, viewports, routes).failed, false);
    evidence[1].badResponses.push({ status: 502, url: 'https://example.test/api' });
    assert.equal(auditEvidence(evidence, viewports, routes).failed, true);
  });
});
