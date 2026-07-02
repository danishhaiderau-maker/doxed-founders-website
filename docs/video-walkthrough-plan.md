# DoxxedCrypto Platform — Video Walkthrough Plan

> **Target runtime:** 90–120 seconds (DemoMaker sweet spot)
> **Public URL:** https://doxxedcrypto.digital
> **Bot showcase:** https://bot.doxxedcrypto.digital
> **Narration companion:** `docs/video-walkthrough-narration.md`
> **Voice:** calm, confident product-launch narrator. Not hype-bro, not robotic.

All claims are grounded in `docs/FOUNDER_OS_PRODUCT_SPECIFICATION.md`, `apps/api/src/wall/wall.controller.ts`, `apps/api/src/founder-os/founder-promo.service.ts`, `apps/web/src/app/login/page.client.tsx`, and the Prisma schema. Backend-only/new features not yet on the public site are marked **[LOCAL DEV]** with the route.

---

## Scene 1 — Hero / Landing Offer
- **URL:** https://doxxedcrypto.digital
- **On-screen actions:** Load landing page. Hero headline fades in. Camera catches the offer strip: "Free DDollar · GLM 5.2 free 90 days". Slow scroll past the fold.
- **Camera direction:** Cinematic slow zoom from 1.0x → 1.15x on the hero headline, then smooth pan down to the offer CTA. Cursor rests on the "Get started" button without clicking.
- **Narration:** Welcome to DoxxedCrypto — where the founders are doxxed, not the traders. Sign up today and claim free DDollar, plus GLM 5.2 free for ninety days. No credit card. Just build.
- **Duration:** 7s
- **Transition:** Whip-pan right into the problem panel.

## Scene 2 — The Problem Pitch
- **URL:** https://doxxedcrypto.digital/#problem (or the fair-warning section of the landing page)
- **On-screen actions:** Hold on the "99.6% of Pump.fun traders have not realized over $10k" stat. Subtle count-up animation on the number. Cut to the "doxxed founders, not anonymous pump cycles" headline.
- **Camera direction:** Static lock on the stat with a 5% push-in. Number counts up. Then a hard cut to the doxxed-founders headline.
- **Narration:** Ninety-nine point six percent of Pump.fun traders have never realized over ten thousand dollars. The anonymous pump cycle keeps eating retail capital. We replaced it with doxxed founders and verifiable work.
- **Duration:** 7s
- **Transition:** Cross-dissolve into the three-paths panel.

## Scene 3 — Founder OS, Three Paths
- **URL:** https://doxxedcrypto.digital/#founder-os (or /founder-os if dedicated route exists)
- **On-screen actions:** Three cards slide in left-to-right: Sovereign · Hybrid · Founder Cloud. Hover each card to lift it. End with cursor resting on Founder Cloud.
- **Camera direction:** Cards animate in with a 120ms stagger. Camera does a gentle dolly across the three cards. Hover lift on each.
- **Narration:** Founder OS gives you three ways to run. Go Sovereign and keep everything on your laptop. Run Hybrid and blend local compute with cloud brains. Or choose Founder Cloud and let us host the runtime for you.
- **Duration:** 8s
- **Transition:** Match-cut on the laptop icon into the Founder Node explainer.

## Scene 4 — Founder Node Explainer
- **URL:** https://doxxedcrypto.digital/#founder-node (or [LOCAL DEV] `/founder-node` route if not yet on public site)
- **On-screen actions:** Diagram animates: laptop → outbound WebSocket → cloud control plane. Caption chips appear: "Ollama local", "BYO keys", "Pair with one code", "45s heartbeat". Vault sync line draws in with a lock icon.
- **Camera direction:** Top-down diagram view. Lines draw in with a 200ms ease. Lock icon pulses once. End on a wide hold.
- **Narration:** Founder Node is the runtime that lives on your laptop. It pairs with one code, beats a heartbeat every forty-five seconds, and runs Ollama locally — so your private context never leaves your machine. No remote desktop. No VPN. No open ports. Just your laptop as the compute.
- **Duration:** 9s
- **Transition:** Browser tab switch to the bot showcase domain.

## Scene 5 — BTC Trading Agent Showcase
- **URL:** https://bot.doxxedcrypto.digital
- **On-screen actions:** Land on the bot dashboard. PnL ticker animates. Scroll to the live trade log. Hover the "Copy trade" button. Hover "Hire with DDollar". Show the Bitfinex paper-trading badge.
- **Camera direction:** Quick push-in on the PnL ticker (1.0x → 1.2x). Smooth cursor glide to Copy trade, then to Hire with DDollar. Hold on the trade log as new rows stream in.
- **Narration:** This is the BTC conservative agent — live on Bitfinex with real paper trading. Watch the PnL update in real time, copy a trade in one click, or hire the agent with DDollar. Every fill is logged. Every decision is auditable.
- **Duration:** 9s
- **Transition:** Tab switch back to the main site, Discover route.

## Scene 6 — Discover / Projects
- **URL:** https://doxxedcrypto.digital/discover
- **On-screen actions:** Project list loads. Activity score column visible. Badges appear: "Hot", "Under Review", "Scam Alert". Click one Hot project card to open the detail page. Scroll to metrics.
- **Camera direction:** Slow vertical scroll through the list. Badges fade in with a 100ms stagger. Cursor clicks the top Hot project. Smooth scroll into the detail view.
- **Narration:** Open Discover and you see every project ranked by an activity score. Hot projects, under-review projects, and scam-alert badges are all surfaced up front — so trust is visible before you click.
- **Duration:** 8s
- **Transition:** Slide left into the Trust Center.

## Scene 7 — Trust Center
- **URL:** https://doxxedcrypto.digital/trust (or /trust-center)
- **On-screen actions:** Three columns: Pending Listings, Scout Voting, Investigations. Click a pending listing to expand the scout vote tally. Scroll to a delisting request row.
- **Camera direction:** Static wide on three columns, then a slow push-in on the Scout Voting column. Click expands a row. Hold.
- **Narration:** The Trust Center is where the community governs the list. Pending listings wait for scout votes. Investigations stay open until evidence closes them. Delisting requests are public. Nothing is hidden behind admin doors.
- **Duration:** 8s
- **Transition:** Match-cut from a coin icon to the DDollar economy panel.

## Scene 8 — DDollar Economy
- **URL:** https://doxxedcrypto.digital/ddollar (or economy section on landing)
- **On-screen actions:** Pie chart animates: 80% community / 10% airdrop / 70% vesting-over-10-years callout. The 10-year vesting timeline draws in below.
- **Camera direction:** Pie chart sweeps from 0 to 80%. Timeline draws left-to-right with year ticks. End on a wide hold.
- **Narration:** DDollar is the platform economy — and eighty percent of it is community owned. Ten percent drops as an airdrop, and seventy percent vests over ten years. The longer the community stays, the more it earns.
- **Duration:** 7s
- **Transition:** Cross-dissolve into the project wall.

## Scene 9 — Community / Wall Feature
- **URL:** [LOCAL DEV] https://doxedcryptofounder.up.railway.app/projects/{slug} — public wall at `GET /wall/projects/:slug/messages`; or the web route `apps/web/src/app/project/[slug]/page.tsx`
- **On-screen actions:** Project wall loads with Telegram-style message bubbles. A build-log badge and a founder-video badge appear on two messages. Cursor hovers a message and clicks Pin (DDollar cost chip visible). Then click "Activate Summarizer" (1,000 DDollar / 30 days chip).
- **Camera direction:** Slow scroll up the message stream. Bubble pop-in animation. Cursor moves to Pin button; DDollar cost chip scales in. Cut to the Summarizer button and click.
- **Narration:** Every project gets a wall — a Telegram-style message stream where members chat, share build logs, and pin the updates that matter. Pin a message with DDollar to push it up. Run the Chat Summarizer and catch up in seconds.
- **Duration:** 9s
- **Transition:** Slide right into the platform activity feed.

## Scene 10 — Platform Activity Feed
- **URL:** https://doxxedcrypto.digital/activity (or feed section)
- **On-screen actions:** Three tabs: Trades · Listings · Broadcasts. Click each tab in sequence. Each tab swap animates the row list.
- **Camera direction:** Tab clicks with a 250ms interval. Row list cross-fades on each tab change. Hold on Broadcasts.
- **Narration:** The activity feed rolls up everything in one place. Trades, listings, and broadcasts each get their own tab, so the pulse of the platform is one glance away.
- **Duration:** 7s
- **Transition:** Wipe up into the adoption chart.

## Scene 11 — Adoption Chart
- **URL:** https://doxxedcrypto.digital/adoption (or adoption section on landing/dashboard)
- **On-screen actions:** Three line charts animate in: AI tokens logged per call, GitHub sync events, build posts. Each line draws left-to-right with a tooltip appearing on the latest point.
- **Camera direction:** Charts draw in with a 200ms stagger. Camera holds wide, then pushes in 10% on the latest data point of each chart.
- **Narration:** The adoption chart tracks real usage — AI tokens logged per call, GitHub sync events, and build posts shipped by founders. Every dot is a real event. No vanity metrics.
- **Duration:** 7s
- **Transition:** Cursor glides up to the Sign in button.

## Scene 12 — Login Flow
- **URL:** https://doxxedcrypto.digital/login
- **On-screen actions:** Login card loads. Founder promo signup banner visible at top. "To list a project, sign in with X" info card present. Hover the X (Twitter) OAuth button. Hover the email form. Click into the email field, type a sample email. Show the Terms + Privacy links at the bottom.
- **Camera direction:** Card scales in from 0.95 → 1.0. Cursor moves from the promo banner down to the X button, then to the email field. Hold on the Terms/Privacy footer links.
- **Narration:** Signing in takes seconds. Use X to list a project, or email to trade and vote. Two-factor supports authenticator, recovery codes, and passkeys. Your referral code carries your credit on the way in.
- **Duration:** 8s
- **Transition:** Cursor clicks the Privacy link.

## Scene 13 — Legal Pages
- **URL:** https://doxxedcrypto.digital/legal/privacy and https://doxxedcrypto.digital/legal/terms
- **On-screen actions:** Privacy page loads. Scroll past the "What we collect" section. Click the Terms link in the page header (or footer). Terms page loads. Scroll to the acceptance section.
- **Camera direction:** Smooth scroll on Privacy, then a click transition to Terms. Slow vertical scroll. Hold on the acceptance heading.
- **Narration:** Every claim here is backed by policy. The Privacy Policy explains what we collect. The Terms explain what you agree to. Both are linked right under the sign-in button.
- **Duration:** 7s
- **Transition:** Cut back to the founder promo banner on the login page.

## Scene 14 — Founder Promo
- **URL:** https://doxxedcrypto.digital/login (banner) and [LOCAL DEV] `apps/api/src/founder-os/founder-promo.service.ts` — surfaced via Settings → Connected Accounts / promo status endpoint
- **On-screen actions:** Founder promo banner animates in with "GLM 5.2 free 90 days". Cut to a Settings panel showing the promo status: 30M token cap, days remaining, providers list (GLM, DeepSeek, Gemini, OLLAMA_LOCAL).
- **Camera direction:** Banner slides in from the top. Cut to the Settings panel. Cursor hovers each provider chip. Hold on the days-remaining counter.
- **Narration:** New founders get GLM 5.2 free for ninety days — with Gemini and DeepSeek as fallbacks. Thirty million tokens, no credit card, no key to manage. When the promo ends, bring your own keys and keep building.
- **Duration:** 8s
- **Transition:** Wipe to the closing card.

## Scene 15 — Closing CTA
- **URL:** https://doxxedcrypto.digital (hero or footer CTA cluster)
- **On-screen actions:** Three CTA buttons appear in sequence: "Start Founder OS", "Hire the BTC agent", "Get the Android app". Each button lifts on hover. End on the DoxxedCrypto wordmark.
- **Camera direction:** Buttons animate in with a 150ms stagger. Hover lift on each. Final slow zoom out to reveal the wordmark and the tagline "Your laptop is the compute."
- **Narration:** Start Founder OS today. Hire the BTC agent with DDollar. Or grab the Android app and resume your work from anywhere. Your laptop is the compute. Your ideas are the work. We handle the rest.
- **Duration:** 8s
- **Transition:** Fade to black on the wordmark.

---

## Runtime Summary

| Scene | Section | Duration |
|-------|----------------------------------|----------|
| 1 | Hero / Landing Offer | 7s |
| 2 | Problem Pitch | 7s |
| 3 | Founder OS — Three Paths | 8s |
| 4 | Founder Node | 9s |
| 5 | BTC Trading Agent | 9s |
| 6 | Discover / Projects | 8s |
| 7 | Trust Center | 8s |
| 8 | DDollar Economy | 7s |
| 9 | Community / Wall | 9s |
| 10 | Activity Feed | 7s |
| 11 | Adoption Chart | 7s |
| 12 | Login Flow | 8s |
| 13 | Legal Pages | 7s |
| 14 | Founder Promo | 8s |
| 15 | Closing CTA | 8s |
| **Total** | | **117s** |

Sits inside the 90–120s DemoMaker window. Trim each scene by ~1s to hit ~100s if a tighter cut is needed.

---

## Grounding Notes

- **Founder Node details** (pair code, 45s heartbeat, Ollama, no remote desktop / VPN / port forwarding): spec §4.4–4.5.
- **Three runtime paths** (Sovereign / Hybrid / Founder Cloud) and "laptop is the compute" tagline: spec §1.4, §2.4, §3.3.
- **Wall feature**: `apps/api/src/wall/wall.controller.ts` — public message stream, join, pin (DDollar spend), Chat Summarizer (1,000 DDollar / 30 days), aggregated feed, unread badges. Web component: `apps/web/src/components/project-wall.tsx`.
- **Founder promo**: `apps/api/src/founder-os/founder-promo.service.ts` — GLM 5.2 default, Gemini + DeepSeek fallbacks, 90-day window, 30M token cap, no credit card, BYOK after.
- **Login**: `apps/web/src/app/login/page.client.tsx` — X (Twitter) for listing, email for trading/voting, 2FA (TOTP / recovery / passkey), referral code persistence, Terms + Privacy footer links, founder promo signup banner.
- **BTC agent**: `services/btc-conservative-agent` on port :7002, public at https://bot.doxxedcrypto.digital, Bitfinex paper trading per repo context.
- **DDollar economy**: 80% community owned, 10% airdrop, 70% over 10 years — per landing page offer (user-provided context).
- **Discover / Trust Center / Adoption**: grounded in Prisma models — `TrendingScore` (activity score), `ProjectTrustReport` / `ProjectInvestigation` / `AuditReport` (badges), `ListingApplication` / `ListingVote` / `ScoutMarket` (Trust Center), `AiTokenUsageLog` / `GitHubConnection` / `FounderBuildPost` (adoption chart).
