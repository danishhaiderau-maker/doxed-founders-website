# Android app — phone-first scope

The APK is a **WebView shell** around [doxxedcrypto.digital](https://doxxedcrypto.digital). It opens **Discover** with `?app=android` so navigation emphasizes what works well on a phone.

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
