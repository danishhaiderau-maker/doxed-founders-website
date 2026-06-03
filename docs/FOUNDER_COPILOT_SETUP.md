# Founder Copilot — setup & usage guide

This guide explains how to make **Founder Copilot** work end-to-end on [doxxedcrypto.digital](https://doxxedcrypto.digital), what to connect, and how to delegate work (including **Cursor**) when you are away from your laptop.

---

## What Founder Copilot actually is

Founder Copilot is **mission control in the browser**:

| Layer | Role |
|-------|------|
| **Copilot chat** | Answers from project memory, GitHub, tasks, launch readiness |
| **Workforce agents** | Product Manager, Researcher, Builder, Marketer, etc. — queue tasks & drafts when you ask |
| **Cursor Cloud** | Runs code on your **GitHub repo** in the cloud (not on your local Windows disk) |
| **Founder Node** | Optional — syncs **metadata** from your PC vault when the node is online |

Copilot is **not** a replacement for local Cursor on your laptop unless you connect **GitHub + Cursor API** and push work to the repo.

---

## Quick checklist (minimum → full power)

### Minimum (planning & queue only)

- [ ] Sign in at `/login`
- [ ] Activate **founder profile** (Founder OS → Settings tab, or list your project)
- [ ] Set **current goal** in AI Stack → Goal focus

**Result:** Rule-based answers + task queue. Status line: `Rule-based · add DeepSeek/OpenAI in Builder for AI chat`.

### Recommended (intelligent chat)

Everything above, plus:

- [ ] **LLM API key** in AI Stack (DeepSeek, OpenAI, Claude, Gemini, OpenRouter, or Phala TEE)
- [ ] Set **Default provider** to your LLM (not `RULE_BASED`)

**Result:** Natural language answers, richer agent specs, investor explanations, etc.

### Serious remote work (code from phone/browser)

Everything above, plus:

- [ ] **GitHub repo** linked: `owner/repo` in Founder OS → Integrations / Stack hub
- [ ] Tap **Sync commits** after you ship (or enable regular sync)
- [ ] **Cursor API key** in AI Stack → Cursor Cloud Agents
- [ ] Toggle **Founder Copilot (Cursor)** in Connected stack
- [ ] Set **Default provider** to `CURSOR` *only if* you want Builder agent tasks to auto-dispatch code

### Full automation (issues + memory)

- [ ] **GitHub PAT** (repo scope) in Builder — auto-creates GitHub issues from agent output
- [ ] **Founder Node** paired on Windows — metadata relay when PC is online
- [ ] Memory files in repo: `.founder-os/project-context.md`, `tasks.json` (sync via GitHub)

---

## Step-by-step setup

### 1. Founder profile

1. Go to [Founder OS](https://doxxedcrypto.digital/founder-den)
2. Complete founder activation / list your project
3. Confirm you see **Builder** badge in the nav (not just signed-in user)

Without a founder profile, Copilot returns errors or empty memory.

### 2. Link GitHub

1. Founder OS → **Integrations** (or Copilot → Connected stack)
2. Enter repo as `danishhaiderau-maker/doxed-founders-website` (your `owner/repo`)
3. Click **Connect GitHub**
4. Click **Sync commits** after new pushes

**What this enables:** Recent commits in answers, `.founder-os/` memory in repo, Cursor agent target repo.

**If you see:** `No recent commits synced — connect GitHub and tap Sync commits`

→ Repo not linked, sync not run, or GitHub API temporarily failed. Sync again.

### 3. Add an LLM (fixes “Rule-based” answers)

1. [AI Stack](https://doxxedcrypto.digital/settings/builder)
2. Connect one provider (DeepSeek is cost-effective; OpenAI/Claude for quality)
3. Set **Default provider** to that LLM
4. Reload Founder OS — status should show `Chat LLM · …` not `Rule-based`

**Without an LLM**, Copilot still works but replies use **templates** (project memory block + canned structure). That is what you saw for “Explain to investors” and “Command cursor…”.

### 4. Connect Cursor (remote coding)

Cursor needs **three** things (toggle alone is not enough):

| Step | Where | Why |
|------|--------|-----|
| GitHub repo | Stack hub | Cursor runs on this repo |
| Cursor API key | Builder → Cursor Cloud Agents | From [cursor.com/dashboard](https://cursor.com/dashboard) → Integrations |
| Stack toggle | Connected stack → Founder Copilot (Cursor) | Marks integration active |

**To dispatch code from Copilot:**

- Type your task, then click **Run on Cursor** (not just Send), **or**
- Ask the **Builder** worker with default provider `CURSOR`, **or**
- Use **Resume work** (Copilot bar) when default provider is Cursor

Cursor Cloud Agents work on **GitHub**, not files only on your Windows machine.

### 5. GitHub PAT (optional — auto issues)

1. GitHub → Settings → Developer settings → Personal access tokens
2. Scope: `repo` (for private repos) or `public_repo`
3. Paste in Builder → GitHub personal access token

**Enables:** Product Manager / Builder agents to **publish** queued GitHub issues, not just queue them locally.

### 6. Founder Node (optional — laptop continuity)

1. Download from [founder-node](https://doxxedcrypto.digital/founder-node)
2. Builder → Memory storage → **Founder Node**
3. Generate pairing code → pair in tray app
4. Keep node running while you work locally (syncs metadata every ~60s)

**When laptop is off:** Copilot uses **last synced metadata** + GitHub + platform queue — not your full local vault.

---

## How to use Copilot day-to-day

### Status / planning prompts (Copilot, not agents)

Use for questions — **do not** start with “Act as my …”:

- `What am I working on right now?`
- `What is the most pressing issue?`
- `What changed this week?`
- `Resume work`
- `Explain this project to investors`

These route to **Copilot** (with LLM if connected).

### Delegation prompts (workforce agents)

Use when you want **tasks queued**:

- `Act as my Product Manager. Break down our MVP into user stories…`
- `Research competitors for [X]`
- `Draft a launch thread for this week’s commits`
- `Create GitHub tasks for [feature]`

Or open [Agents](https://doxxedcrypto.digital/agents) and click a template (deep-links into Copilot).

**After an agent runs:** Open **Memory** tab → review build queue → dismiss duplicates → sync to GitHub if needed.

### Remote coding without laptop

1. Confirm GitHub repo linked + recent sync
2. Confirm Cursor API connected
3. Prompt example:

   ```
   Continue from open task "Define MVP user stories".
   Implement the next P0 item on danishhaiderau-maker/doxed-founders-website.
   ```

4. Click **Run on Cursor**
5. Open the returned **Cursor agent URL** on any device

### Resume work

- Copilot quick action **Resume work**, or type `Resume work`
- With Cursor as default provider: starts/refollows a cloud agent using memory + repo context
- Without Cursor: copies a **cursor-ready prompt** you can paste into local Cursor

---

## What each agent does

| Agent | You ask for… | Creates | Can auto-run |
|-------|----------------|---------|--------------|
| **Product Manager** | Specs, user stories, P0/P1 | Build queue, GitHub issue drafts | Issues (with PAT) |
| **Researcher** | Market/competitor briefs | Queue + research issues | Issues (with PAT) |
| **Builder** | Code plans | Queue + Cursor prompt | **Cursor agent** (if CURSOR default) |
| **Marketer** | Posts, threads | Queue + community draft notice | Draft in Projects |
| **Community Manager** | FAQ, welcome copy | Queue + community draft | Draft in Projects |
| **Fundraising / Launch** | Raise/launch checklists | Queue + Raise Room link | Checklist link |

Agents are **workers Copilot invokes** — they do not run 24/7 on their own.

---

## APIs you need (summary)

| Integration | Required? | Purpose |
|-------------|-----------|---------|
| **LLM key** (DeepSeek/OpenAI/etc.) | Strongly recommended | Smart chat; without it = rule-based |
| **GitHub repo** | Yes for code context | Commits, memory files, Cursor target |
| **Cursor API** | For remote coding | Cloud agents on GitHub |
| **GitHub PAT** | Optional | Auto-create issues |
| **Founder Node** | Optional | Bridge Windows vault metadata |
| **Phala / BYO AI** | Optional | Private TEE inference ([PHALA_PRIVATE_AI.md](./PHALA_PRIVATE_AI.md)) |

**Cursor alone is not enough** — add GitHub + ideally an LLM.

---

## Troubleshooting (common issues)

### “Rule-based · add DeepSeek/OpenAI in Builder”

→ No LLM connected or default provider is `RULE_BASED`. Add a key in Builder and set default provider.

### Same question, different commit lists / “No recent commits synced”

→ GitHub sync is **on demand** or cached. Tap **Sync commits** in Integrations. Answers may use cached memory between syncs.

### “Command cursor…” only returns project memory text

→ Normal chat **does not** start Cursor. Click **Run on Cursor**, or use Builder agent + `CURSOR` default provider, or **Resume work**.

### Tasks keep duplicating (Define MVP user stories × many)

→ Each agent/Copilot delegation creates new queue items. Review **Memory** tab → mark done or dismiss old items. Set a clear **Goal focus** in Builder so `current goal` is not overwritten by spec titles.

### Project shows URL instead of name (`https://doxxedcrypto.digital/`)

→ Project record name/slug may be the URL from listing. Update project name in founder settings or re-list with a proper project name.

### Away from laptop — can’t see Windows-only work

→ Only synced data is visible: GitHub commits, platform queue, Founder Node metadata (if node was online). Unsynced local vault files are **not** in the cloud.

### Cursor connected in stack but agents fail

→ Stack toggle ≠ API key. Confirm Builder shows Cursor **API key connected** and repo is `owner/repo` format.

---

## Suggested first session (15 minutes)

1. Builder: connect **DeepSeek or OpenAI**, set as default provider  
2. Stack hub: link GitHub repo → **Sync commits**  
3. Builder: paste **Cursor API key** → verify connected  
4. Builder: set **Goal focus** to one sentence (e.g. “Ship Founder Copilot setup docs and landing redesign”)  
5. Founder OS Copilot: `What am I working on right now?` — confirm LLM answer  
6. `Act as my Product Manager. Break down [one feature] into 3 tasks` — check Memory tab  
7. Pick one P0 task → **Run on Cursor** — open agent URL  

---

## Related docs

- [FOUNDER_COPILOT_MVP_USER_STORIES.md](./FOUNDER_COPILOT_MVP_USER_STORIES.md) — MVP research and content-generation user stories
- [PHALA_PRIVATE_AI.md](./PHALA_PRIVATE_AI.md) — private TEE inference (Step 3)
- [apps/founder-node/README.md](../apps/founder-node/README.md) — Founder Node pairing & vault
