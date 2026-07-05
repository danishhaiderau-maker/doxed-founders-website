# Unified mobile app — Founder OS + Founder Node

**Status:** Android v0.4.2 ships today as one Capacitor app. Desktop Founder Node v0.7.11 remains the primary vault + Ollama runtime. iOS is Safari-first until TestFlight.

## Product direction

| Era | Experience |
|-----|------------|
| **Interim (now)** | Android APK = WebView shell → full Founder OS online. Desktop Founder Node = vault + IDE bridge + local AI. |
| **Target** | **One mobile app** with Founder OS UI + on-device vault sync (Node capabilities embedded), not two store listings. |
| **Desktop** | Founder Node stays the heavy runtime (Ollama, Cursor discovery, large vault files). |

The browser was the laptop→phone bridge during rollout. The APK already *is* the unified shell — it loads the same Founder OS routes as the website, including Builder, vault pairing (**Code for Android**), and mobile vault panels.

## Current architecture

```
┌─────────────────────────────────────────────────────────┐
│  Doxxed Crypto (Android APK, Capacitor)                 │
│  appId: digital.doxxedcrypto.app                        │
│  Entry: www/index.html → doxxedcrypto.digital?app=android│
│  Plugins: Filesystem, Preferences (Phase 3 vault)       │
└──────────────────────────┬──────────────────────────────┘
                           │ HTTPS
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Founder OS (Next.js on Vercel)                         │
│  mobile-vault-panel · mobile-vault-bootstrap            │
│  founder-node pairing · Builder settings                │
└──────────────────────────┬──────────────────────────────┘
                           │ sync API
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Founder Node (Electron, desktop) v0.7.11               │
│  ~/FounderVault/ · Ollama · Cursor IDE bridge           │
└─────────────────────────────────────────────────────────┘
```

## What to merge (roadmap)

1. **Branding** — one launcher icon, one store listing: “Doxxed Crypto” (Founder OS + mobile vault).
2. **Vault engine in APK** — already started (`mobile-vault-*` components); expand bidirectional merge (Phase 4 in `MOBILE_VAULT_ROADMAP.md`).
3. **No separate “Founder Node mobile app”** — Node *features* ship inside the existing Capacitor project (`apps/mobile-android`).
4. **iOS** — add `npx cap add ios` when TestFlight credentials are ready; same web bundle + native vault plugins.

## Launch checklist

| Platform | Listing | Install path |
|----------|---------|--------------|
| Android | Direct APK + future Play internal testing | `/mobile`, `/downloads/doxxedcrypto-android.apk` |
| iOS | Safari + Add to Home Screen now; TestFlight Q3 | `/mobile#ios` |
| Desktop | Founder Node v0.7.11 | `/founder-den#founder-node-download`, GitHub `founder-node-v0.7.11` |

## Build commands

```bash
npm run pack:android          # APK → apps/web/public/downloads/
npm run pack:founder-node:win # Windows installer
git tag founder-node-v0.7.11 && git push origin founder-node-v0.7.11
git tag android-app-v0.4.2 && git push origin android-app-v0.4.2
```
