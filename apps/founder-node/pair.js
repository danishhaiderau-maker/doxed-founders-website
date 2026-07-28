/**
 * Pair window renderer — loaded from pair.html (file://), no nodeIntegration.
 *
 * Phase 2 device-code flow (RFC 8628):
 *   1. On load, ask the main process to start a device-code grant
 *      (start-device-code). Main creates installId + ipcSecret if missing,
 *      POSTs to /api/founder-node/device-code, returns the grant shape.
 *   2. Display userCode prominently + show "Open browser" button.
 *   3. Poll poll-device-code every `interval` seconds. Main handles the
 *      HTTP codes (202 pending, 200 authorized, 400 expired, 403 denied,
 *      429 slow_down with Retry-After). Renderer just reacts to the
 *      high-level status the main process returns.
 *   4. On 'authorized': display success and let main close the window.
 *
 * Legacy 8-char paste flow stays available under "Advanced" for users on
 * deployments that haven't shipped device-code yet.
 */
(function () {
  /** @typedef {{ status: 'pending'|'authorized'|'expired'|'denied'|'slow_down', interval?:number, error?:string }} PollResult */
  /** @typedef {{ userCode:string, verificationUri:string, verificationUriComplete:string, expiresAt:string, interval:number, installFingerprint:string }} DeviceGrant */

  if (!window.founderNodePair) {
    document.body.innerHTML =
      '<p style="color:#f87171;padding:24px">Pairing UI failed to load. Quit Founder Node from the tray and reinstall from a fresh build.</p>';
    return;
  }

  // ─── Device-code first-run screen ──────────────────────────────────────
  /** @type {HTMLElement|null} */
  const userCodeEl = document.getElementById('userCode');
  /** @type {HTMLElement|null} */
  const installFingerprintEl = document.getElementById('installFingerprint');
  /** @type {HTMLElement|null} */
  const statusEl = document.getElementById('dcStatus');
  const statusMsg = statusEl?.querySelector('.msg');
  const openBrowserBtn = /** @type {HTMLButtonElement|null} */ (document.getElementById('openBrowser'));
  const copyCodeBtn = /** @type {HTMLButtonElement|null} */ (document.getElementById('copyCode'));
  const restartBtn = /** @type {HTMLButtonElement|null} */ (document.getElementById('restart'));

  // ─── Legacy / advanced screen ──────────────────────────────────────────
  const api = /** @type {HTMLInputElement|null} */ (document.getElementById('api'));
  const code = /** @type {HTMLInputElement|null} */ (document.getElementById('code'));
  const label = /** @type {HTMLInputElement|null} */ (document.getElementById('label'));
  const pairBtn = /** @type {HTMLButtonElement|null} */ (document.getElementById('pair'));
  const msg = document.getElementById('msg');

  let pollTimer = null;
  let currentGrant = null;

  window.founderNodePair.getDefaults().then((defaults) => {
    if (api) api.value = defaults.apiBaseUrl;
    if (label) label.value = defaults.label;
  });

  function setStatus(text, kind) {
    if (!statusEl || !statusMsg) return;
    statusMsg.textContent = text;
    statusEl.classList.remove('ok', 'err');
    if (kind === 'ok') statusEl.classList.add('ok');
    else if (kind === 'err') statusEl.classList.add('err');
  }

  async function startDeviceCode() {
    if (!userCodeEl) return;
    userCodeEl.textContent = '····-····';
    if (installFingerprintEl) installFingerprintEl.textContent = '---- ---- ----';
    setStatus('Requesting code…', '');
    try {
      /** @type {DeviceGrant} */
      const grant = await window.founderNodePair.startDeviceCode();
      currentGrant = grant;
      userCodeEl.textContent = grant.userCode;
      if (installFingerprintEl) installFingerprintEl.textContent = grant.installFingerprint;
      setStatus('Waiting for authorization…', '');
      // Begin polling loop after the first interval.
      schedulePoll(grant.interval * 1000);
    } catch (e) {
      setStatus(e?.message || String(e), 'err');
      showRestart();
    }
  }

  function schedulePoll(delayMs) {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(() => {
      pollOnce().catch((e) => {
        setStatus(e?.message || String(e), 'err');
        showRestart();
      });
    }, Math.max(1000, delayMs));
  }

  async function pollOnce() {
    if (!currentGrant) return;
    /** @type {PollResult} */
    const result = await window.founderNodePair.pollDeviceCode(currentGrant);
    if (result.status === 'pending') {
      setStatus('Waiting for authorization…', '');
      schedulePoll((result.interval ?? currentGrant.interval) * 1000);
    } else if (result.status === 'slow_down') {
      // RFC 8628 §3.5 — server said we polled too fast; back off.
      setStatus('Slowing down…', '');
      schedulePoll((result.interval ?? currentGrant.interval + 5) * 1000);
    } else if (result.status === 'authorized') {
      setStatus('Authorized! Writing credentials…', 'ok');
      // Main process writes node-config.json on success then closes the window.
      // No further action needed here.
      if (pollTimer) clearTimeout(pollTimer);
    } else if (result.status === 'expired') {
      setStatus('Code expired. Click "Start over" to try again.', 'err');
      showRestart();
      if (pollTimer) clearTimeout(pollTimer);
    } else if (result.status === 'denied') {
      setStatus('Authorization denied in browser.', 'err');
      showRestart();
      if (pollTimer) clearTimeout(pollTimer);
    }
  }

  function showRestart() {
    restartBtn?.classList.remove('hidden');
  }

  if (openBrowserBtn) {
    openBrowserBtn.onclick = () => {
      if (currentGrant?.verificationUriComplete) {
        window.founderNodePair.openUrl(currentGrant.verificationUriComplete);
      }
    };
  }

  if (copyCodeBtn) {
    copyCodeBtn.onclick = () => {
      if (currentGrant?.userCode) {
        navigator.clipboard?.writeText(currentGrant.userCode).catch(() => {});
        setStatus('Code copied to clipboard', 'ok');
      }
    };
  }

  if (restartBtn) {
    restartBtn.onclick = () => {
      restartBtn.classList.add('hidden');
      currentGrant = null;
      if (pollTimer) clearTimeout(pollTimer);
      startDeviceCode();
    };
  }

  // ─── Legacy pairing (kept under "Advanced") ────────────────────────────
  if (pairBtn) {
    pairBtn.onclick = async () => {
      pairBtn.disabled = true;
      if (msg) {
        msg.textContent = '';
        msg.className = '';
      }
      try {
        await window.founderNodePair.pair({
          apiBaseUrl: api?.value.trim() || '',
          code: code?.value.trim() || '',
          label: label?.value.trim() || '',
        });
        if (msg) {
          msg.textContent = 'Paired! This window will close shortly.';
          msg.className = 'ok';
        }
        setTimeout(() => window.close(), 4000);
      } catch (e) {
        if (msg) {
          msg.textContent = e?.message || String(e);
          msg.className = 'err';
        }
        pairBtn.disabled = false;
      }
    };
  }

  // Kick off the device-code flow as soon as the renderer is ready.
  startDeviceCode();
})();
