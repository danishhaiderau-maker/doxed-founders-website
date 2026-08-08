# Mobile app

**Status (Aug 2026):** The previous Capacitor Android shell and public APK were removed from the repo. `/mobile` is a **coming soon** download hub (no APK link). Pair desktop tools from [/founder-ide](https://doxxedcrypto.digital/founder-ide). Old APK URL `/downloads/doxxedcrypto-android.apk` redirects to `/mobile#android`.

## Removed from the repo

- `apps/mobile-android/` — Capacitor 7 WebView shell (`@dcf/mobile-android` v0.4.x)
- `apps/web/public/downloads/doxxedcrypto-android.apk`
- `scripts/pack-android-app.mjs`, `scripts/print-android-signing-secrets.mjs`
- `.github/workflows/android-app-release.yml`
- npm scripts `pack:android` / `print:android-signing`

Web-side mobile vault helpers under `apps/web/src/lib/mobile-vault/` remain for a future native shell.

## Related docs (historical)

- [MOBILE_UNIFIED_APP.md](./MOBILE_UNIFIED_APP.md)
- [MOBILE_VAULT_ROADMAP.md](./MOBILE_VAULT_ROADMAP.md)
