/** Pair window renderer — loaded from pair.html (file://), no nodeIntegration. */
(function () {
  const api = document.getElementById('api');
  const code = document.getElementById('code');
  const label = document.getElementById('label');
  const pairBtn = document.getElementById('pair');
  const msg = document.getElementById('msg');
  const openWeb = document.getElementById('openWeb');

  if (!window.founderNodePair) {
    document.body.innerHTML =
      '<p style="color:#f87171;padding:24px">Pairing UI failed to load. Quit Founder Node from the tray and reinstall from a fresh build.</p>';
    return;
  }

  window.founderNodePair.getDefaults().then((defaults) => {
    api.value = defaults.apiBaseUrl;
    label.value = defaults.label;
  });

  openWeb.onclick = () => window.founderNodePair.openSettings();

  pairBtn.onclick = async () => {
    pairBtn.disabled = true;
    msg.textContent = '';
    msg.className = '';
    try {
      await window.founderNodePair.pair({
        apiBaseUrl: api.value.trim(),
        code: code.value.trim(),
        label: label.value.trim(),
      });
      msg.textContent =
        'Paired! This window will close — Founder Node keeps running in your system tray (icon near the clock). Do not click Quit in the tray menu.';
      msg.className = 'ok';
      setTimeout(() => window.close(), 5000);
    } catch (e) {
      msg.textContent = e?.message || String(e);
      msg.className = 'err';
      pairBtn.disabled = false;
    }
  };
})();
