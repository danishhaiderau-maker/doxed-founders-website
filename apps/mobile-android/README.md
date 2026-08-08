# Doxxed Crypto — unified mobile app (complete package)

Capacitor **Android** shell for landing, chat, agent hub, account, and Founder IDE (web).
Loads [doxxedcrypto.digital](https://doxxedcrypto.digital) after a local hub + WebView compat gate.

**Not in the APK:** desktop Founder Node, Void/Founder IDE binary, or Ollama.

**iOS:** Safari → Add to Home Screen for now ([/mobile#ios](https://doxxedcrypto.digital/mobile#ios)).

## Build APK

```bash
npm install
npm run pack:android
```

Outputs:

| Path | Purpose |
|------|---------|
| `apps/mobile-android/release/Doxxed-Crypto-*-android-*.apk` | Sideload / archive |
| `apps/web/public/downloads/doxxedcrypto-android.apk` | Site download (`/mobile#android`) |

## First-load hub

`www/index.html` → Chat / Agent Hub / Founder IDE / Account / Home (not Discover-only).

## Dev sync

```bash
npm run sync
npm run open
```

## CI

Tag `android-app-v*` → `.github/workflows/android-app-release.yml`.
