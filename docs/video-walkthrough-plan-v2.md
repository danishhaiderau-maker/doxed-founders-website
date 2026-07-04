# DoxxedCrypto Platform — Onboarding Walkthrough Plan (v2)

> **Target runtime:** ~2:30–3:00 narrated (DemoMaker plan ~110–130s screen time)
> **Public URL:** https://doxxedcrypto.digital
> **Founder Node onboarding:** https://doxxedcrypto.digital/founder-den?onboard=sovereign#founder-node-download
> **Bot showcase:** https://bot.doxxedcrypto.digital
> **Narration companion:** `docs/video-walkthrough-narration-v2.md`
> **Narration type:** `how_to` (tutorial / onboarding)
> **Voice:** calm, confident, builder-focused — welcoming, never “parasite” language

This plan supersedes v1 for **new-user onboarding**. It adds Founder OS chat → Cursor agent activation, sovereign Founder Node download via the platform (not GitHub-first), two-tier free tokens (Verified Builder vs Trial), and Settings pairing. Authenticated workspace scenes require a saved auth session (`save_auth_session`) or a pre-signed-in browser.

---

## Chapter 1 — Welcome & Landing (0:00–0:18)

| Field | Value |
|-------|-------|
| **URL** | https://doxxedcrypto.digital |
| **Actions** | Load hero. Slow scroll past Founder OS cards and BTC agent strip. Hover “Start Founder OS →”. |
| **Exact clicks** | None (hover only on `text=Start Founder OS →`) |
| **Narration beat** | What DoxxedCrypto is; doxxed founders; your laptop is the compute. |
| **Duration** | 18s |
| **Wait gate** | `text=Start Founder OS →` visible |

---

## Chapter 2 — Sign Up / Sign In (0:18–0:45)

| Field | Value |
|-------|-------|
| **URL** | https://doxxedcrypto.digital/login |
| **Actions** | Click nav `text=Sign in`. Hover `text=Continue with X`. Hover `input[type="email"]`. Expand token rules: click `text=How free AI tokens work on Founder OS`. Scroll to show Verified Builder + Trial cards. Hover Verified Builder card. Do **not** submit credentials or complete OAuth. |
| **Exact clicks** | Nav Sign in → summary “How free AI tokens work…” |
| **Narration beat** | X for founders who want the fast track; email for traders/voters; two tiers explained in friendly language. |
| **Duration** | 27s |
| **Wait gate** | `text=Continue with X` after navigation |

**Token tier copy (from UI — use verbatim tone):**
- **Verified Builder:** verify X, 500k tokens/day, 30-day window, DDollar bonus + GLM-5.2 free 90 days, GitHub + Cursor + commit locks priority.
- **Trial:** email-only, 25k tokens/day, paused when pool low, BYOK anytime; upgrade via X verification + one commit.

---

## Chapter 3 — Founder OS Paths (0:45–1:00)

| Field | Value |
|-------|-------|
| **URL** | https://doxxedcrypto.digital (scroll to `#founder-os` section) |
| **Actions** | Scroll to three path cards. Hover Sovereign, Hybrid, Founder Cloud in sequence. |
| **Exact clicks** | None (hover each card) |
| **Narration beat** | Sovereign = local privacy; Hybrid = optional cloud; Founder Cloud = hosted scale. |
| **Duration** | 15s |
| **Wait gate** | `text=SovereignEverything runs locally` visible |

---

## Chapter 4 — Download Founder Node (1:00–1:22)

| Field | Value |
|-------|-------|
| **URL** | https://doxxedcrypto.digital/founder-den?onboard=sovereign#founder-node-download |
| **Actions** | From login, click `text=Start Founder OS setup →` **or** direct goto above URL. `wait_until` `#founder-node-download`. Scroll download section into view. Hover `text=Download for Windows` (or OS-specific primary button). Hover `text=Windows (.exe)` / macOS / Linux links. Brief hold on install guide if visible. |
| **Exact clicks** | Optional: login CTA → founder-den; no download click (opens external tab) |
| **Narration beat** | Get Founder Node from the platform onboarding hub — not GitHub hunting. Pair with one code, 45s heartbeat, Ollama optional, vault stays local. |
| **Duration** | 22s |
| **Wait gate** | `#founder-node-download` or `text=Download for Windows` |

**Auth note:** The download block (`#founder-node-download`) renders only when signed in with `?onboard=sovereign`. For unsigned recordings, show the public Founder OS shell (journey + tabs) and narrate the sovereign URL; run `save_auth_session` first for live download footage.

**Note:** Site may show latest release (e.g. v0.7.7+). User-facing link is always the sovereign onboarding URL above.

---

## Chapter 5 — Pair in Settings (1:22–1:38) `[AUTH REQUIRED]`

| Field | Value |
|-------|-------|
| **URL** | https://doxxedcrypto.digital/founder-den → tab **Settings** (or `/settings/builder` when logged in) |
| **Actions** | Click `text=Settings`. Scroll to Founder Node / Pair your device panel. Hover “Code for desktop” / pairing UI. Do not paste real pairing codes on camera. |
| **Exact clicks** | Settings tab |
| **Narration beat** | Settings → Founder Node: generate pairing code, paste in tray app, confirm heartbeat online. |
| **Duration** | 16s |
| **Wait gate** | Settings panel content visible |
| **Fallback (no auth)** | Stay on founder-den public shell; narrate pairing steps over install guide on sovereign URL. |

---

## Chapter 6 — Development Workspace & Cursor Agent (1:38–2:08) `[AUTH + FOUNDER NODE IDEAL]`

| Field | Value |
|-------|-------|
| **URL** | https://doxxedcrypto.digital/founder-den?tab=activity#founder-dev-workspace |
| **Actions** | Open Workspace / Activity tab. Show connect wizard if node offline OR sidebar with Cursor workspaces if online. Hover Cursor status chip. Select a Cursor chat session if listed. Type a short prompt in `placeholder` containing “Message Founder OS” — e.g. “Summarize my repo README” — **do not send secrets**. Show agent badge / “Sent to Cursor — agent working…”. |
| **Exact clicks** | Activity tab → Cursor session (if present) → focus chat input |
| **Narration beat** | Type in Founder OS chat; pick a Cursor session; agent activates remotely — progress streams here without opening the IDE. |
| **Duration** | 30s |
| **Wait gate** | `#founder-dev-workspace` or connect wizard / chat input visible |
| **Fallback (no auth)** | Use login page `text=Start Founder OS setup →` + narrate this chapter over founder-den marketing copy. |

---

## Chapter 7 — BTC Agent Showcase (2:08–2:25)

| Field | Value |
|-------|-------|
| **URL** | https://bot.doxxedcrypto.digital |
| **Actions** | Goto showcase. `wait_until` body loaded (may be SPA — allow up to 30s). Scroll dashboard. Hover PnL / live stats area. Hover copy-trade or hire CTA if present. **Marketing view only — no strategy internals.** |
| **Exact clicks** | None required |
| **Narration beat** | Live paper-trading showcase; copy a trade or hire with DDollar; auditable fills. |
| **Duration** | 17s |
| **Wait gate** | Page title or first visible dashboard element |

---

## Chapter 8 — Discover (2:25–2:38)

| Field | Value |
|-------|-------|
| **URL** | https://doxxedcrypto.digital/discover |
| **Actions** | Scroll project list. Hover `text=Apply for listing`. Hover scout / activity score callouts. |
| **Exact clicks** | None |
| **Narration beat** | Ranked projects, activity scores, apply when you are ready to list. |
| **Duration** | 13s |
| **Wait gate** | `text=Discover` or listing cards |

---

## Chapter 9 — Trust Center (2:38–2:50)

| Field | Value |
|-------|-------|
| **URL** | https://doxxedcrypto.digital/trust |
| **Actions** | Scroll scout queue, pending listings, investigations columns. Hover scout voting link if visible. |
| **Exact clicks** | None |
| **Narration beat** | Community governs listings; scouts vote; investigations and delists are public. |
| **Duration** | 12s |
| **Wait gate** | Trust page content rendered |

---

## Chapter 10 — DDollar & Community (2:50–3:02)

| Field | Value |
|-------|-------|
| **URL** | https://doxxedcrypto.digital (footer `text=Ecosystem—DDollar paper economy` or `/discover` ecosystem link) |
| **Actions** | Hover DDollar economy card. Optional: landing scout queue / community wall teaser. |
| **Narration beat** | DDollar powers hires, pins, and scout rewards — community-owned economy. |
| **Duration** | 12s |

---

## Chapter 11 — Closing CTA (3:02–3:15)

| Field | Value |
|-------|-------|
| **URL** | https://doxxedcrypto.digital |
| **Actions** | Scroll to hero/footer. Hover `text=Start Founder OS →`, `text=Hire Agent →`, `text=Founder Node →`. Hold on wordmark. |
| **Narration beat** | Start Founder OS, hire the agent, download Founder Node — build in public. |
| **Duration** | 13s |

---

## Runtime Summary

| Ch | Section | Screen ~s | Narration ~s |
|----|---------|-----------|--------------|
| 1 | Landing welcome | 18 | 18 |
| 2 | Login & token tiers | 27 | 27 |
| 3 | Founder OS paths | 15 | 15 |
| 4 | Founder Node download | 22 | 22 |
| 5 | Settings pairing | 16 | 16 |
| 6 | Cursor agent chat | 30 | 30 |
| 7 | BTC showcase | 17 | 17 |
| 8 | Discover | 13 | 13 |
| 9 | Trust Center | 12 | 12 |
| 10 | DDollar / community | 12 | 12 |
| 11 | Closing CTA | 13 | 13 |
| **Total** | | **~195s max** | **~3:15** |

**DemoMaker trim:** For a single ~120s recording, merge chapters 3+4, shorten Trust/DDollar to hover-only, and use auth fallback narration for chapter 6.

---

## DemoMaker Plan JSON (public-path recording)

Use with `create_demo_video(url="https://doxxedcrypto.digital", narration_type="how_to", mode="full")`. Chapters 5–6 need `auth_session` label after `save_auth_session`.

See narrateai plan embedded in production job or regenerate from steps above.

---

## Grounding (code & product)

| Feature | Source |
|---------|--------|
| Login X vs email, token collapsible | `apps/web/src/app/login/page.client.tsx`, `founder-token-rules-collapsible.tsx` |
| Sovereign onboarding URL | `apps/web/src/app/founder-node/page.tsx` → redirect `/founder-den?onboard=sovereign#founder-node-download` |
| Cursor dispatch from chat | `apps/web/src/components/minimal-dev-workspace.tsx` |
| Founder Node pairing copy | `founder-node-downloads.tsx`, `founder-node-v2-panel.tsx` |
| BTC showcase | https://bot.doxxedcrypto.digital (marketing only) |
| Token tiers | Verified Builder 500k/day; Trial 25k/day — UI defaults in collapsible |

---

## Pre-recording checklist

1. **DemoMaker MCP** reachable (`check_app` → `reachable: true`) ✓
2. **Auth (optional but recommended):** run `save_auth_session(start_url="https://doxxedcrypto.digital/login", label="doxxed-founder")` — sign in with X in headed browser once
3. **Founder Node (optional):** paired + Cursor open for chapter 6 live footage
4. **No secrets** in plan steps, narration, or committed docs
