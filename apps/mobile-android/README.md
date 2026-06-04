# Doxxed Crypto — Android app

Capacitor shell that loads [doxxedcrypto.digital](https://doxxedcrypto.digital) in a full-screen WebView (paper trading, scout votes, Founder OS, etc.).

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
