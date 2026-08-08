# Unified mobile app — Capacitor complete package

**Status:** Android **v0.5.0** Capacitor shell (recreated after legacy APK retirement). Desktop Founder Node remains the primary vault + Ollama runtime. iOS is Safari / Add to Home Screen until TestFlight.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Doxxed Crypto (Android APK, Capacitor)                 │
│  appId: digital.doxxedcrypto.app                        │
│  Entry: www/index.html hub → doxxedcrypto.digital?app=  │
│  Surfaces: /chat · /agent-hub · /founder-ide · /account │
└──────────────────────────┬──────────────────────────────┘
                           │ HTTPS
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Production web (Next.js)                               │
│  Download hub: /mobile  ·  APK: /downloads/*.apk        │
└─────────────────────────────────────────────────────────┘
```

**Not in APK:** Founder Node, Void/Founder IDE binary, Ollama (desktop only; linked from `/founder-ide`).

## Build

```bash
npm run pack:android   # → apps/web/public/downloads/doxxedcrypto-android.apk
```

Nav **App** control → `/mobile#android` / `#ios`.
