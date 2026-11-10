/* Read-only adapter for the SAME dashboard HTML and renderPathwayLab handler. */
(function () {
  'use strict';
  window.DASHBOARD_VIEW_ONLY = true;
  const allowed = new Set(['/api/state', '/health']);
  const originalFetch = window.fetch.bind(window);
  window.fetch = function (input, options) {
    const url = new URL(typeof input === 'string' ? input : input.url, window.location.href);
    const method = String((options && options.method) || (input && input.method) || 'GET').toUpperCase();
    if (url.origin !== window.location.origin || !allowed.has(url.pathname) || method !== 'GET') {
      return Promise.reject(new Error('READ_ONLY: network action blocked'));
    }
    return originalFetch(input, options);
  };
  function disableControls() {
    document.querySelectorAll('form, #dashboardToggles, #tradingParamsPanel').forEach(el => {
      el.hidden = true;
    });
    document.querySelectorAll('button, input, select').forEach(el => {
      const action = el.getAttribute('onclick') || '';
      if (action === 'refresh()' || action.startsWith('showPathwayTab(') || el.id === 'autoRefreshToggle') return;
      el.disabled = true;
      el.removeAttribute('onclick');
    });
  }
  window.refreshDashboardViewer = async function (renderTiles) {
    const banner = document.getElementById('viewerBanner');
    try {
      const response = await fetch('/api/state', {cache: 'no-store'});
      if (!response.ok) throw new Error('saved state refused / unavailable (HTTP ' + response.status + ')');
      const data = await response.json();
      renderTiles(data);
      const sources = Object.entries(data.viewer.sources).map(([name, source]) =>
        name + ': ' + (source.available ? 'saved ' + Math.round(source.age_sec) + ' seconds ago' : 'UNAVAILABLE'));
      banner.textContent = 'READ ONLY · Live OFF · ' + data.viewer.warning + ' ' + sources.join(' · ');
      document.getElementById('operatingMode').textContent = 'READ ONLY — engine and writers NOT RUNNING here';
      document.getElementById('operatingDetails').textContent = 'Saved revision: ' + (data.viewer.snapshot_revision || 'UNKNOWN');
      document.getElementById('serverBanner').textContent = 'Local viewer only — no live feed, paper execution, strategy PASS or arming.';
      document.getElementById('dataSource').textContent = 'SAVED EVIDENCE ONLY — not current market data';
      disableControls();
    } catch (error) {
      banner.textContent = 'READ ONLY · Live OFF · UNKNOWN: ' + error.message;
      document.getElementById('pathwayLaneTiles').textContent = 'Saved tiles unavailable; previous values are not current evidence.';
    }
  };
  window.initDashboardViewer = function (refresh) {
    const banner = document.createElement('div');
    banner.id = 'viewerBanner';
    banner.setAttribute('role', 'status');
    banner.style.cssText = 'position:sticky;top:0;z-index:100;background:#422006;color:#fef3c7;padding:16px;border:2px solid #f59e0b;';
    banner.textContent = 'READ ONLY · Live OFF · Saved evidence loading; engine and writers are not started.';
    document.body.prepend(banner);
    // Keep the original tiles and refresh/navigation handlers, not dead engine panels.
    const visible = new Set(['viewerBanner', 'operatingTruth', 'serverBanner', 'pathwayLab']);
    Array.from(document.body.children).forEach(el => {
      if (visible.has(el.id) || el.tagName === 'H1' || el.tagName === 'SCRIPT' || el.querySelector('#refreshStatus')) return;
      el.hidden = true;
    });
    disableControls();
    const toggle = document.getElementById('autoRefreshToggle');
    toggle.checked = false;
    let timer = null;
    toggle.addEventListener('change', function () {
      if (timer) clearInterval(timer);
      timer = toggle.checked ? setInterval(refresh, 180000) : null;
    });
    refresh();
  };
})();
