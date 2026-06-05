# Android app — phone-first scope

The APK is a **WebView shell** around [doxxedcrypto.digital](https://doxxedcrypto.digital). It opens **Discover** with `?app=android` so navigation emphasizes what works well on a phone.

**File size (~5–8 MB debug)** is expected: WebView shell + Capacitor plugins (filesystem, preferences). It still does **not** bundle the website or your notes — vault files appear **after pairing** under app-private storage. Website features ship on **Vercel**; reinstall the APK when native plugins change (v0.2 → v0.3).

## Download (Phase 1)

| Channel | URL |
|---------|-----|
| **Website (primary)** | [doxxedcrypto.digital/mobile](https://doxxedcrypto.digital/mobile) |
| **Direct APK** | [doxxedcrypto.digital/downloads/doxxedcrypto-android.apk](https://doxxedcrypto.digital/downloads/doxxedcrypto-android.apk) |
| **GitHub release** | Tag `android-app-v*` → [Releases](https://github.com/danishhaiderau-maker/doxed-founders-website/releases) |

Build locally: `npm run pack:android` (writes `apps/web/public/downloads/doxxedcrypto-android.apk`).

**v0.4.1+:** Local **compat gate** on launch — old WebView (common on **Android 6**) gets setup steps instead of a white screen. **Recommended: Android 8+** with updated **Android System WebView** (Play Store).

**v0.4.0+:** On-device `FounderVault/` + **bidirectional merge** with desktop (LWW patches for goals/tasks/roadmap). Pair with **Code for Android** in Settings. APK stays ~4 MB; full site + vault sync after login.

**Roadmap (PC↔phone merge):** [MOBILE_VAULT_ROADMAP.md](./MOBILE_VAULT_ROADMAP.md)

## In scope (default mobile experience)

| Area | Routes |
|------|--------|
| **Discover & explore** | `/discover`, `/projects` |
| **Project rankings** | Discover table, `/leaderboard` |
| **Trust Center** | `/trust-center`, scout voting |
| **Agents** | `/agent-hub`, `/agents` |
| **Builder reviews / rewards** | `/builder-rewards`, `/airdrop` |
| **Community trading** | `/paper-trading`, `/predict`, `/watchlist`, `/portfolio`, `/ddollar` |
| **Feed & social** | `/feed`, notifications |

## Optional on phone (same app, not hidden)

Founder OS, Founder Node, Raise Room, and List Project stay available via **Build & Founder OS →** in the nav. Heavy build workflows are easier on desktop; founders can still open them in the app WebView if they choose.

## Out of scope for the APK shell

- **Founder Node** desktop vault (Windows / macOS / Linux installer)
- **Local Ollama** (runs on the paired PC, not the phone)

Cloud AI and Cursor Cloud still work through the API when keys are configured in Builder settings.

## Rebuild APK after URL change

```bash
npm run pack:android
```

Entry URL is set in `apps/mobile-android/capacitor.config.ts` (`server.url`).
