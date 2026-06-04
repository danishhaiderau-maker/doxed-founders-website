# Doxxed Crypto — Android app

Capacitor shell for **phone-first** use: opens [Discover](https://doxxedcrypto.digital/discover?app=android) with explore, project rankings, Trust Center, agents, builder rewards, and community paper trading. Founder OS and build tools stay available via nav but are not the default focus. See [docs/MOBILE_APP.md](../../docs/MOBILE_APP.md).

## Build APK (Windows / macOS / Linux)

Requires **JDK 17+** and **Android SDK** (`ANDROID_HOME` or `ANDROID_SDK_ROOT`).

```bash
npm install
npm run pack:android
```

Output:

- `apps/mobile-android/release/Doxxed-Crypto-*.apk` — installable APK
- `apps/web/public/downloads/doxxedcrypto-android.apk` — served on the landing page

## CI

Push tag `android-app-v*` to run `.github/workflows/android-app-release.yml` (unsigned debug APK suitable for direct install).
