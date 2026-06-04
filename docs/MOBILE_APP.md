# Android app — phone-first scope

The APK is a **WebView shell** around [doxxedcrypto.digital](https://doxxedcrypto.digital). It opens **Discover** with `?app=android` so navigation emphasizes what works well on a phone.

**File size (~3.9–4.5 MB) is expected.** The APK does not contain the Next.js site, AI models, or `~/FounderVault/` data — only the Android wrapper. Updates to Founder OS, Copilot, mic, and sync ship on **Vercel** when you deploy the website; users get them on next app open without reinstalling the APK (unless native Capacitor plugins change).

## Download (Phase 1)

| Channel | URL |
|---------|-----|
| **Website (primary)** | [doxxedcrypto.digital/mobile](https://doxxedcrypto.digital/mobile) |
| **Direct APK** | [doxxedcrypto.digital/downloads/doxxedcrypto-android.apk](https://doxxedcrypto.digital/downloads/doxxedcrypto-android.apk) |
| **GitHub release** | Tag `android-app-v*` → [Releases](https://github.com/danishhaiderau-maker/doxed-founders-website/releases) |

Build locally: `npm run pack:android` (writes `apps/web/public/downloads/doxxedcrypto-android.apk`).

**Roadmap (vault on phone, PC sync, release phases):** [MOBILE_VAULT_ROADMAP.md](./MOBILE_VAULT_ROADMAP.md)

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
