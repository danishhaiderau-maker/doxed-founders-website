const DEFAULT_TOLERANCE = 2;

export function runtimeErrorIssues(item) {
  return [
    ...(item.consoleErrors || []).map((message) => ({
      code: 'console-error',
      detail: message,
    })),
    ...(item.pageErrors || []).map((message) => ({
      code: 'page-error',
      detail: message,
    })),
  ];
}

export function networkErrorIssues(item) {
  return (item.badResponses || [])
    .filter((response) => response.status >= 400)
    .map((response) => ({
      code: 'bad-response',
      detail: `${response.status} ${response.url}`,
    }));
}

export function layoutIssues(item, tolerance = DEFAULT_TOLERANCE) {
  const issues = [];
  const overflow = Number(item.shell?.horizontalOverflow || 0);
  if (overflow > tolerance) {
    issues.push({
      code: 'horizontal-overflow',
      detail: `${overflow}px beyond viewport`,
    });
  }

  for (const control of item.keyControls || []) {
    if (!control.found) {
      issues.push({ code: 'key-control-missing', detail: control.name });
      continue;
    }
    if (!control.visible) {
      issues.push({ code: 'key-control-hidden', detail: control.name });
    }
    if (control.offscreen) {
      issues.push({ code: 'key-control-offscreen', detail: control.name });
    }
    if (control.clippedByViewport || control.clippedByAncestor || control.contentClipped) {
      issues.push({ code: 'key-control-clipped', detail: control.name });
    }
  }

  return issues;
}

export function itemIssues(item, tolerance = DEFAULT_TOLERANCE) {
  const issues = [];
  if (item.status !== 200) {
    issues.push({
      code: 'document-status',
      detail: item.status === null ? 'no document response' : String(item.status),
    });
  }

  for (const [label, visible] of Object.entries(item.navVisible || {})) {
    if (!visible) issues.push({ code: 'navigation-missing', detail: label });
  }
  for (const [check, ready] of Object.entries(item.buildMenu || {})) {
    if (!ready) issues.push({ code: 'build-menu-invalid', detail: check });
  }

  return [
    ...issues,
    ...networkErrorIssues(item),
    ...runtimeErrorIssues(item),
    ...layoutIssues(item, tolerance),
  ];
}

export function evidenceCoverageIssues(evidence, viewports, routes) {
  const issues = [];
  const byKey = new Map();
  for (const item of evidence) {
    const key = `${item.viewport?.name || ''}:${item.route?.name || ''}`;
    const matches = byKey.get(key) || [];
    matches.push(item);
    byKey.set(key, matches);
  }

  for (const viewport of viewports) {
    for (const route of routes) {
      const key = `${viewport.name}:${route.name}`;
      const matches = byKey.get(key) || [];
      if (matches.length === 0) {
        issues.push({ code: 'evidence-missing', detail: key });
        continue;
      }
      if (matches.length > 1) {
        issues.push({ code: 'evidence-duplicate', detail: key });
      }

      const item = matches[0];
      if (!item.screenshot || !(item.screenshotBytes > 0)) {
        issues.push({ code: 'screenshot-missing', detail: key });
      }
      if (
        route.name === 'discover'
        && (!item.buildMenuScreenshot || !(item.buildMenuScreenshotBytes > 0))
      ) {
        issues.push({ code: 'menu-screenshot-missing', detail: key });
      }
    }
  }

  return issues;
}

export function auditEvidence(evidence, viewports, routes, tolerance = DEFAULT_TOLERANCE) {
  const screens = evidence.map((item) => ({
    viewport: item.viewport?.name || 'unknown',
    route: item.route?.path || item.route?.name || 'unknown',
    issues: itemIssues(item, tolerance),
  }));
  const coverageIssues = evidenceCoverageIssues(evidence, viewports, routes);
  return {
    screens,
    coverageIssues,
    failed: coverageIssues.length > 0 || screens.some((screen) => screen.issues.length > 0),
  };
}
