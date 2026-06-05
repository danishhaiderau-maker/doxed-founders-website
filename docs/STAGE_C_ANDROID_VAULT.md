# Stage C — Android vault validation

**Prerequisite:** Stages A (ship) and B (publish) complete.

**Goal:** Prove the mobile vault path works on a **physical Android device** — pair, pull, merge — without leaking private note bodies to Neon.

Current app version in repo: **0.4.0** (`apps/mobile-android/package.json`). Download: https://doxxedcrypto.digital/mobile

---

## Server-side check (automation)

```bash
npm run stage-c:probe
```

Confirms encrypted relay rows exist and Neon does not store readable vault markdown in `ProjectMemoryDeviceSync` payload fields.

---

## Device checklist (you — ~15 minutes)

### 1. Install APK

1. On your **Android phone**, open https://doxxedcrypto.digital/mobile
2. Download **Doxxed Crypto Android** (APK v0.4.x)
3. Allow install from browser if prompted
4. Open app → log in as **@bitbro4crypto** (same account as founder-den)

### 2. Pair desktop Founder Node (if not already)

1. On PC: https://doxxedcrypto.digital/settings/builder → **Founder Node**
2. Confirm **Danish Founder Node** shows **online** (you already have this in Resume)
3. **Step 2 → Code for Android** → copy 6-digit pairing code

### 3. Pair vault on phone

1. In the APK: open **Founder OS** or **Settings** path that shows **Android vault**
2. Paste pairing code → **Pair**
3. Expect: paired status + node label

### 4. Pull + merge

1. Tap **Pull from relay** (or equivalent sync button)
2. Expect: last sync timestamp updates
3. Optional: add a **private note** on desktop vault only → sync node → pull on phone → note appears on device

### 5. Privacy check (pass/fail)

| Check | Pass |
|-------|------|
| Resume on desktop still says “encrypted blob relayed” | ✓ |
| `npm run stage-c:probe` shows no plaintext task bodies in Neon | ✓ |
| Phone shows vault status after pull | ✓ |

### 6. Conflict (optional)

1. Edit same vault field on phone and desktop (offline one device)
2. Pull/merge → app should prefer merge rules (newer wins or conflict message)

---

## When Stage C is done

- [ ] APK installed on physical device
- [ ] Pairing code succeeded
- [ ] Pull/merge updates `lastSyncAt`
- [ ] `stage-c:probe` green
- [ ] Mark checklist in `docs/SHIP_STAGE_CHECKLIST.md`

**Then:** daily **Stage D** loop (Resume → Ask → Build with Cursor → Publish from queue).

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| No vault panel in browser | Expected — vault UI is **inside APK** only |
| Pairing fails | Regenerate code; clock skew; re-login |
| Pull empty | Desktop Founder Node must sync first (~60s) |
| Old APK | Hard refresh `/mobile` or use GitHub release asset |
