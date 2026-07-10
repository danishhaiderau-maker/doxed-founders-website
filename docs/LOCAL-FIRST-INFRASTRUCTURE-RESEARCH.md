# Local-First Infrastructure Research — Can the Laptop Replace GitHub, Vercel, Railway, and Neon?

> **Status:** Research · Open source landscape analysis
> **Owner:** Founder OS core team
> **Last updated:** 2026-07-10
> **Question answered:** Can a founder build entirely on their own machine (Private mode) and only attach cloud providers (Public mode) when they are ready to launch?

---

## TL;DR — Yes, and the open source stack exists today

Every layer of the modern founder stack has a free, open source, laptop-runnable equivalent:

| Cloud service | Local-first replacement | Cost | Runs on laptop? |
|--|--|--|--|
| GitHub | **Forgejo** (or Gitea) | Free | Yes — ~80 MB RAM, single binary |
| Vercel / Railway | **Cloudflare Tunnel** + local server | Free | Yes — outbound-only, no open ports |
| Neon / Postgres | **SQLite** (file-based) or local Postgres | Free | Yes — zero config, embedded |
| GitHub Actions CI | **act** (local) or **Woodpecker CI** (self-hosted) | Free | Yes — Docker-based |
| Replit / Codespaces | **DevPod** (client-only) | Free | Yes — devcontainer.json standard |

The open source landscape has matured past "hobby project" stage. In 2026, a base-model Mac mini or a typical Windows laptop can run the entire stack — git forge, database, web server, tunnel, and AI coding agent — simultaneously, for $0/month in cloud spend. There are documented cases of indie hackers running 100+ repos from a single Mac mini with zero cloud bills.

**The recommended "Private mode" bundle for Founder Stack:**

```
Founder Node (existing)
  + Forgejo          ← local git forge (GitHub replacement)
  + SQLite           ← local database (Neon replacement)
  + Cloudflare Tunnel ← public exposure (Vercel replacement, when needed)
  + Tailscale        ← phone remote control (already architected)
```

This lets a founder brainstorm, build, version, and database their entire project on their laptop, free, in private — and flip to Public mode by attaching GitHub + Neon + Vercel when they are ready to launch.

---

## 1. The Vision — Private vs Public Mode

Founder OS already has the right north star. From `FOUNDER_OS_PRODUCT_SPECIFICATION.md`:

> **Founder OS is the remote operating system for modern builders.** It connects to your desktop, resumes your work exactly where you left it, lets you use your preferred IDE and AI, helps you build in public, and keeps costs low by letting your laptop do the heavy lifting.

The Private vs Public toggle extends this mission cleanly:

### Private mode (default for new founders)
- **Compute:** the founder's laptop, via Founder Node
- **Storage:** local filesystem + SQLite file
- **Version control:** local git, optionally mirrored to a local Forgejo instance
- **Hosting:** `localhost` on existing ports (7002, 9001), exposed via tunnel only when the founder wants to share
- **Database:** SQLite file in the project directory
- **Cost:** $0/month
- **Visibility:** only the founder (and anyone they share a tunnel URL with)
- **Use case:** brainstorming, prototyping, early building, validating ideas

### Public mode (founder opts in when ready to launch)
- **Compute:** cloud (Vercel, Railway, Fly.io)
- **Storage:** cloud object storage (S3, Vercel Blob)
- **Version control:** GitHub (mirrored from local Forgejo, or pushed directly)
- **Hosting:** real domain via Vercel/Railway
- **Database:** Neon Postgres (migrated from SQLite)
- **Cost:** usage-based (free tiers cover early stage)
- **Visibility:** public internet
- **Use case:** launched product with real users

### The key UX principle

Private mode must **not feel like a toy**. It must feel like a real development environment — because it is one. The difference between Private and Public is not capability; it is visibility and cost. A founder in Private mode has a real git history, a real database, a real web server with a real HTTPS URL (via tunnel), and real deployments. They are building on professional infrastructure that happens to run on their own hardware.

This is the same mental model Replit uses: "your code lives on a real Linux box." The difference is that Founder OS puts that Linux box on the founder's own laptop instead of in Replit's cloud.

---

## 2. GitHub Replacement — Forgejo (or Gitea)

### The landscape

| Tool | RAM (idle) | Stars | Governance | Verdict |
|--|--|--|--|--|
| **Forgejo** | ~80 MB | 16K+ | Codeberg e.V. (non-profit, community) | **Recommended default** |
| Gitea | ~80 MB | 47K+ | Gitea Ltd (for-profit, open core) | Valid, but VC-backed |
| GitLab CE | 4 GB minimum | — | GitLab Inc (open core) | Too heavy for a laptop |
| OneDev | ~300 MB | — | Community | Java/JVM, decent all-in-one |
| Gogs | ~80 MB | — | Unmaintained | The original; Forgejo/Gitea forked from it |

### Why Forgejo is the 2026 default

Every 2026 comparison guide converges on the same recommendation: **start with Forgejo** for new self-hosted deployments. The reasons:

1. **Community-governed** under Codeberg e.V. (a German non-profit). No corporate owner can change direction or close the project.
2. **GPL-licensed** (copyleft), vs Gitea's permissive MIT. The license protects against future commercialization.
3. **Drop-in replacement for Gitea** — same API, same config format, same resource footprint. Migration between the two is documented.
4. **Same lightweight footprint** as Gitea: single Go binary, ~80 MB RAM idle, runs on a Raspberry Pi. It will run comfortably alongside Founder Node on any laptop.
5. **Forgejo Actions** — a GitHub Actions-compatible CI built in. Same YAML syntax, so workflow knowledge transfers.
6. **Federation roadmap** (ForgeFed / ActivityPub) — the only git forge with a credible federated future, relevant if Founder OS ever wants "follow a founder's repo across instances."

### Can it run alongside Founder Node?

**Yes, trivially.** Forgejo is a single Go binary that idles at ~80 MB RAM and serves a web UI on a port of your choice (default 3000). It writes to a SQLite database file by default (no external DB required). On a typical laptop with Founder Node already running, Forgejo adds negligible overhead.

Resource budget on a founder's laptop:

```
Founder Node (Electron tray app)        ~150–250 MB RAM
Founder OS Chat extension (in editor)   ~50–100 MB RAM
Forgejo (local git forge)               ~80 MB RAM
SQLite (embedded in Forgejo + project)  ~10 MB RAM
Local web server (Next.js dev)          ~200–400 MB RAM
Cloudflare Tunnel daemon                ~30 MB RAM
Tailscale (mesh networking)             ~20 MB RAM
─────────────────────────────────────────────────────
Total Private mode overhead             ~540–890 MB RAM
```

This fits comfortably in 8 GB of RAM and leaves room for the founder's IDE and browser.

### Does it provide a web UI for code browsing?

**Yes.** Forgejo's web UI is deliberately GitHub-like: repositories, pull requests, issues, wiki, releases, package registry, container registry, LFS support, SSH access. A founder can browse their code, review diffs, and manage issues in a browser at `http://localhost:3000`, exactly as they would on GitHub.

### Can founders push locally and later mirror to GitHub?

**Yes — this is a first-class Forgejo feature.** A Forgejo repository can be configured with a "mirror" that pushes to (or pulls from) a remote GitHub repository. The Private → Public migration path for git is:

1. Founder pushes commits to local Forgejo during Private mode.
2. When going Public, founder adds a GitHub remote and pushes the full history.
3. Optionally, Forgejo can be configured to automatically mirror-push to GitHub on every commit.

No history is lost. No rewriting is needed. The local Forgejo repo and the GitHub repo are the same git repository.

---

## 3. Vercel Replacement — Cloudflare Tunnel + Local Server

### The problem Vercel solves

Vercel does three things:
1. **Builds** your app from source (Next.js, React, etc.)
2. **Hosts** the built app on their global CDN
3. **Exposes** it on a real HTTPS domain (`yourapp.vercel.app` or a custom domain)

In Private mode, the founder's laptop already does step 1 (their dev server builds on every save) and step 2 (their dev server hosts the app on `localhost`). The only missing piece is step 3: exposing it on a real URL.

### Cloudflare Tunnel is the answer

**Cloudflare Tunnel** (via the `cloudflared` daemon) creates an outbound-only, post-quantum encrypted connection from the founder's laptop to Cloudflare's global edge network. Cloudflare then routes public traffic back to the laptop through that tunnel. No open ports, no public IP, no firewall changes, no NAT traversal headaches.

**Quick Tunnel (zero setup, temporary):**

```bash
cloudflared tunnel --url http://localhost:3000
```

This prints a random `https://<random>.trycloudflare.com` URL that is live on the public internet instantly. Free, no account required, dies when the process exits. Perfect for sharing a work-in-progress with a collaborator or testing a webhook.

**Named Tunnel (persistent, custom domain):**

```bash
# One-time setup
cloudflared tunnel create founder-app
cloudflared tunnel route dns founder-app app.foundersdomain.com

# Run it
cloudflared tunnel run founder-app
```

This gives a stable `https://app.foundersdomain.com` that persists across restarts. Requires a Cloudflare account (free) and a domain managed on Cloudflare DNS (also free).

### Why Cloudflare Tunnel beats the alternatives

| Tool | Free tier | Custom domain | Auth gating | Verdict |
|--|--|--|--|--|
| **Cloudflare Tunnel** | Unlimited tunnels, unlimited connections, unmetered bandwidth | Free (with CF DNS) | Cloudflare Access (free up to 50 users) | **Best overall** |
| ngrok | 1 tunnel, 40 conn/min, random URL that changes | Paid only ($8/mo+) | Paid add-on | Best for webhook debugging (request inspector) |
| Tailscale Funnel | 3 ports only | Limited | No built-in | Good for tailnet-only access |
| localtunnel | Unreliable, rate-limited | No | No | Avoid for anything serious |
| VS Code Port Forwarding | Free, fast | No custom domain | GitHub auth | Good for instant share from IDE |

Cloudflare Tunnel is free forever, has no connection limits, no bandwidth caps, and includes Cloudflare's WAF + DDoS protection for free. For a founder building in Private mode, it is the clear choice.

### How it replaces Vercel's three jobs

1. **Build** — the founder's local dev server (`npm run dev`, `next dev`, `vite`, etc.) builds on every save. No Vercel build step needed.
2. **Host** — the local dev server hosts the app on `localhost:3000` (or whatever port). Cloudflare Tunnel routes public traffic to it.
3. **Expose** — Cloudflare Tunnel provides the real HTTPS URL, with automatic TLS, CDN caching, and DDoS protection. Indistinguishable from a Vercel deployment to the end user.

### The limitation (be honest about it)

Cloudflare Tunnel only works while the founder's laptop is online and the local server is running. If the laptop sleeps, loses internet, or the dev server crashes, the public URL goes down. This is fine for Private mode (brainstorming, demos, early testing) but is exactly why Public mode exists for production traffic.

Vercel's value is that your app stays up when your laptop closes. Founder OS should be honest: **Private mode is for building and sharing work-in-progress. Public mode is for serving users while you sleep.**

---

## 4. Neon Replacement — SQLite (with Prisma)

### The core finding: SQLite is a legitimate Postgres replacement for early-stage apps

This is the single most important technical finding in this research. **Prisma works with SQLite as easily as with Postgres**, and for the vast majority of early-stage founder apps, SQLite is not just sufficient — it is better:

- **Zero config.** The database is a file on disk (`dev.db`). No server to start, no connection string to manage, no Docker container to run.
- **Zero cost.** It is a file. There is no metering, no connection limits, no free tier to exhaust.
- **Zero latency.** Reads and writes hit the local filesystem. There is no network round trip. Queries are effectively instant.
- **Offline-first.** It works without internet, because it is a local file.
- **Prisma-native.** Prisma's `sqlite` provider is a first-class citizen. `prisma migrate dev` and `prisma db push` work identically to the Postgres workflow.

### Prisma + SQLite vs Prisma + Postgres (Neon)

| Feature | SQLite (local file) | Neon (Postgres) |
|--|--|--|
| Setup | Point Prisma at a file. Done. | Create Neon project, get connection string, configure pooling. |
| Prisma schema `provider` | `sqlite` | `postgresql` |
| Prisma Migrate (`prisma migrate dev`) | ✅ Full support | ✅ Full support |
| Prisma `db push` (fast dev loop) | ✅ Full support | ✅ Full support |
| Write concurrency | Single writer (WAL allows concurrent reads) | Multiple concurrent writers (full MVCC) |
| Postgres extensions (`JSONB`, `pg_trgm`, PostGIS) | ❌ Not available | ✅ Full extension ecosystem |
| Database branching | ❌ | ✅ Neon's killer feature |
| Cost | $0 forever | Free tier (0.5 GB, 100 CU-hours), then usage-based |
| When you hit limits | Migrate to Postgres | Scale up on Neon |

### The recommended local-first database loop

The OpenSaasAU architecture decision record (referenced in research) captures the exact pattern Founder OS should adopt:

> **Two database loops.** Local dev: SQLite + `db push` (fast, no migration history, disposable `dev.db`). Production: PostgreSQL + `prisma migrate dev` (authoring versioned migrations) and `prisma migrate deploy` (applying them).

This means:

1. **In Private mode:** the founder's app uses SQLite. They run `prisma db push` to iterate on schema changes instantly. The database is a `dev.db` file in their project directory. Zero cost, zero friction.
2. **Going Public:** the founder changes one line in `schema.prisma` (`provider = "postgresql"`), sets `DATABASE_URL` to their Neon connection string, runs `prisma migrate dev --name init_postgresql` to generate the migration, and deploys. The Prisma schema does not change — only the provider and connection string.

### Turso as the "bridge" option

**Turso** (built on libSQL, an open-source fork of SQLite) is worth knowing about because it offers a smooth gradient from pure-local to edge-distributed:

- **Start local:** use Turso's embedded replica mode. The database is a local SQLite file (`file:local.db`) that syncs to a remote primary in the background. Reads are local (instant), writes go to the primary.
- **Prisma supports Turso** via the `@prisma/adapter-libsql` driver adapter. You write the same Prisma schema; only the client initialization changes.
- **The catch:** Prisma Migrate is not directly compatible with Turso's HTTP connection. You run `prisma migrate dev` against a local SQLite file to generate migration SQL, then apply those SQL files to Turso via its CLI. This is a documented workflow but adds a step.

**Recommendation:** For Founder OS Private mode, use plain SQLite (not Turso). Turso's value (edge replication, distributed reads) does not matter when the founder is the only user. When going Public, migrate to Neon Postgres directly — the SQLite → Postgres path is better documented and more standard than the Turso → Postgres path.

### Can Prisma work with SQLite as easily as Postgres?

**Yes.** This is settled. Prisma's `sqlite` provider is fully supported, has feature parity with `postgresql` for all common operations (CRUD, relations, migrations, introspection), and is the recommended starting point in multiple 2026 scaffold templates. The schema language is identical. The generated client API is identical. The only differences are in advanced Postgres-specific features (extensions, `JSONB` operators, stored procedures) that early-stage founders almost never use.

---

## 5. The All-in-One Package — Can We Bundle This Into Founder Stack?

### Current Founder Stack Lite

The existing `install-founder-stack-lite.ps1` bundles two components:

1. **Founder Node 0.8.0** — Electron tray app, local vault, metadata sync
2. **Founder OS Chat extension** (`founder-ide-extension-0.1.0.vsix`) — registers the AI Gateway as a chat provider in VS Code / Cursor / VSCodium

The full `Founder-Stack-Setup-<v>.exe` (Inno Setup) will additionally bundle a rebranded VSCodium fork ("Founder IDE"). See `packages/founder-ide/RELEASES.md`.

### What to add for Private mode

To make Private mode a first-class experience, the installer should optionally bundle:

| Component | Purpose | Size | Complexity to bundle |
|--|--|--|--|
| **Forgejo** binary | Local git forge (GitHub replacement) | ~100 MB | Low — single Go binary, runs as a background service |
| **SQLite** | Local database (already bundled with Node.js and most runtimes) | 0 (built-in) | None — it is already there |
| **cloudflared** binary | Cloudflare Tunnel daemon (Vercel replacement) | ~30 MB | Low — single binary, runs on demand |
| **Tailscale** installer | Mesh networking for phone remote control | ~25 MB | Medium — requires OS-level install + login |

**Total additional download:** ~155 MB. This is modest — the existing Founder Node installer is already ~80 MB and the full VSCodium build is ~300 MB.

### How the bundling works

These are all single-binary tools (or have official silent installers). The Inno Setup script (`packages/founder-ide/installer/founder-stack.iss`) can:

1. Install Forgejo as a Windows service (or a LaunchAgent on macOS) bound to `localhost:3000`.
2. Drop the `cloudflared` binary into the Founder Node install directory and add it to PATH.
3. Offer Tailscale as an optional checkbox during install (since it requires OS-level network configuration and a login).

The installer should present this as a **"Private mode" checkbox** during setup:

```
[ ] Enable Private mode (local-first development)
    Installs:
      ✓ Forgejo (local git server at localhost:3000)
      ✓ Cloudflare Tunnel (expose apps publicly when you choose)
      ✓ SQLite database (zero-config, file-based)
    Cost: $0/month. Your code stays on your machine until you publish.
```

### What NOT to bundle

- **Coolify / Dokploy** — these are self-hosted PaaS platforms designed for VPS deployment, not laptop use. They need Docker, 2 GB+ RAM, and a Linux server. They are the right tool for a founder who has graduated to a VPS, not for Private mode on a laptop.
- **Full Docker Desktop** — too heavy (~4 GB), too complex for non-technical founders, and not needed when Forgejo + SQLite + cloudflared are all single binaries.
- **Local Postgres** — SQLite covers the same ground with zero config. Local Postgres (via Docker or native install) is only worth it if the founder specifically needs Postgres extensions during Private mode, which is rare.

---

## 6. Phone Remote Over Tunnel — How the Phone Connects to a Private Laptop

### The existing architecture

Founder OS already has a phone remote (`/phone` page) with this architecture, documented in `apps/web/src/app/phone/page.tsx`:

```
Phone Browser ←(SSE)→ Founder OS Cloud API ←(WebSocket)→ Founder Node ←→ IDE
```

The phone authenticates with the founder's NextAuth JWT, sees a list of connected Founder Nodes, selects one, and streams chat completions via SSE through the AI Gateway. This is "Phase 2" of the bridge — the cloud API is still in the middle.

### The Private mode architecture (no cloud in the middle)

In Private mode, the phone should connect **directly** to the founder's laptop, with no Founder OS cloud API in the path. Two open source projects prove this works in 2026:

#### Option A: Tailscale mesh (recommended, matches existing phone remote)

```
Phone (Tailscale app) ←(WireGuard mesh)→ Laptop (Tailscale + Founder Node)
```

**How it works:**
1. Both the founder's phone and laptop run Tailscale (free for personal use).
2. Tailscale creates an encrypted WireGuard mesh between them. The laptop is reachable from the phone at a stable `100.x.x.x` IP or a MagicDNS name (`founder-laptop.tailnet.ts.net`), regardless of which network either device is on.
3. No ports are opened. No public IP is exposed. The connection works behind NAT, firewalls, and CGNAT.
4. The existing `/phone` web UI is served by Founder Node on the laptop (e.g., `http://founder-laptop.tailnet.ts.net:7002/phone`). The phone's browser opens it directly over the tailnet.
5. SSE streaming from the AI Gateway works identically — it just flows over the tailnet instead of the public internet.

**This is the same pattern the existing phone remote uses, minus the cloud API hop.** The phone authenticates with a local JWT (Founder Node issues its own), and the entire chat loop stays on the founder's hardware.

**Real-world proof:** Multiple 2026 projects (TailClaude, Rove, GhostTerm) implement exactly this — a mobile web UI that drives a local Claude Code / coding agent session over Tailscale, with zero cloud relay. The phone is a thin client; the laptop does all the compute.

#### Option B: Cloudflare Tunnel (for founders who do not want to install Tailscale)

```
Phone Browser ←(HTTPS)→ Cloudflare Edge ←(tunnel)→ Laptop (cloudflared + Founder Node)
```

**How it works:**
1. The founder runs `cloudflared tunnel --url http://localhost:7002` on their laptop.
2. Cloudflare assigns a public HTTPS URL (random for Quick Tunnel, or a custom subdomain for a named tunnel).
3. The phone opens that URL in its browser. No Tailscale app required on the phone.
4. Cloudflare routes traffic to the laptop through the outbound tunnel.

**The tradeoff:** this exposes the Founder Node UI on the public internet (behind the tunnel URL). It must be authenticated. Cloudflare Access (free up to 50 users) can gate it behind email-based auth, or Founder Node can require its own JWT.

### The coffee shop scenario

The user specifically asked: *"Can a founder be at a coffee shop, controlling their home laptop's coding session from their phone?"*

**Yes, with Tailscale.** This is exactly what Tailscale is built for. The founder's home laptop and phone are both on the tailnet. The laptop can be behind a home router's NAT, on a different network entirely, or even asleep (if Wake-on-LAN is configured). The phone reaches it by its tailnet hostname. The founder opens the `/phone` UI, picks up the coding session, and drives it from the coffee shop. The laptop's compute (CPU, disk, AI models if local) is doing the work; the phone is just the remote control.

**This is documented as working in production** by multiple indie hackers in 2026, running full coding sessions (Claude Code, Ollama models, build servers) from phones over Tailscale.

### Security considerations

The existing phone remote architecture comment in `page.tsx` notes: *"The Founder Node ↔ IDE WebSocket bridge is a later phase."* Private mode does not change this phasing — it changes where the phone connects (directly to the laptop, not to the cloud API). The security model:

1. **Tailscale ACLs** — restrict tailnet access to the founder's own devices only.
2. **Founder Node JWT** — the laptop issues its own auth token, independent of the cloud API.
3. **Optional passcode** — GhostTerm's model: require an `ACCESS_CODE` on connect.
4. **No raw terminal exposed publicly** — if using Cloudflare Tunnel (Option B), never expose an unauthenticated shell. Always put Founder Node's authenticated UI in front.

---

## 7. The UX — How to Make Private Mode Feel Like Real Building

### What the AI coding platforms teach us

The 2026 landscape of AI coding platforms (Replit, Bolt.new, Lovable) reveals what makes a dev environment feel "real" vs "toy":

| Platform | What makes it feel real | What makes it feel toy |
|--|--|--|
| **Replit** | Full Linux box in the cloud. Terminal access. 50+ languages. Built-in hosting, database, auth, secrets. "It's a real computer." | Infra lock-in. Moving off Replit means re-platforming hosting and cron. |
| **Lovable** | Two-way GitHub sync. Standard React + Vite + Supabase output. "Your code is portable." | Locks you to React + Supabase. No terminal access. |
| **Bolt.new** | Fastest prompt-to-preview. WebContainers run Node in the browser. | Highest lock-in. Moving off Bolt means rework. Browser-only. |

### The five things that make a local dev environment feel "real"

A founder in Private mode will not feel like they are using a toy if they have all five of these:

1. **A real domain.** Not `localhost:3000`. A real HTTPS URL like `https://app.foundersdomain.com` (via Cloudflare Tunnel) or at minimum `https://founder-laptop.tailnet.ts.net`. The URL is what makes the app feel deployed, not just running locally.

2. **A real database.** Not an in-memory mock or a JSON file. A real SQLite database with real tables, real migrations, real query performance. Prisma Studio (`npx prisma studio`) gives them a visual database browser at `localhost:5555` that looks and feels like a real database admin tool — because it is one.

3. **Real git history.** Not auto-saved blobs. Real commits with real messages, real branches, real diffs, viewable in Forgejo's web UI at `localhost:3000`. The founder can see their project's history evolve. They can create a branch for an experiment and merge it. This is the same workflow they will use on GitHub later.

4. **Real deployments.** The founder can share their app with someone via a tunnel URL and get real feedback. The app is live on the internet (via Cloudflare Tunnel), even if it is running on their laptop. This is the difference between "I'm building something" and "I'm building something people can see."

5. **Real tooling.** The same editor (Cursor, VS Code), the same AI (GPT-5, Claude, GLM, Ollama), the same CLI (`git`, `npm`, `prisma`), the same package ecosystem. Private mode does not ask the founder to learn new tools. It just changes where those tools run.

### What makes it feel childish (avoid these)

- **Fake URLs** like `localhost:3000` shown as if they are "your app's address." They are not an address; they are a development port.
- **No git** — auto-saving without history. The founder cannot go back, cannot branch, cannot see progress.
- **Mocks and stubs** instead of a real database. "Your data will be saved when you upgrade" is the death of feeling real.
- **Toy branding** — cute mascots, big cartoon buttons, "playground" language. Founder OS should use the same professional language in Private mode as in Public mode.
- **Disabled features** — grayed-out buttons for "deploy," "share," "publish" that only unlock on a paid plan. In Private mode, everything should work; it just runs locally.

### The run.dev inspiration

**run.dev** (a 2026 Rust binary) is worth studying for UX inspiration. It provides a TUI dashboard for local development that gives you: automatic local domains with real HTTPS (`api.myapp.local`), a reverse proxy, process management, and AI-powered crash diagnosis. It feels like a real ops dashboard, not a toy. Founder OS's Founder Node tray app could evolve in this direction — a single dashboard showing the founder's git forge, database, tunnel, and running services, all on their laptop.

---

## 8. Open Source Projects Doing This — The Landscape

### Local-first infrastructure projects

| Project | What it does | Stars (2026) | Relevance to Founder OS |
|--|--|--|--|
| **Forgejo** | Self-hosted git forge (GitHub replacement) | 16K+ | Core Private mode component |
| **Cloudflare Tunnel** (`cloudflared`) | Expose localhost to internet, free | — | Core Private mode component |
| **Tailscale** | Mesh networking for phone remote | — | Core Private mode component |
| **DevPod** | Client-only dev environment manager (devcontainer.json) | 14.7K+ | Optional — for founders who want reproducible environments |
| **Coolify** | Self-hosted PaaS (Vercel/Heroku replacement) | 57K+ | Post-Private — when founder graduates to a VPS |
| **Dokploy** | Lightweight self-hosted PaaS (Docker Swarm native) | 35K+ | Post-Private — alternative to Coolify |
| **act** | Run GitHub Actions locally | 70K+ | Optional Private mode CI |
| **Woodpecker CI** | Self-hosted CI (fork of Drone) | — | Optional Private mode CI |
| **Turso** | Distributed SQLite (libSQL) | — | Bridge option — not recommended for pure Private mode |
| **run.dev** | AI-native local dev environment (single Rust binary) | New | UX inspiration for Founder Node dashboard |

### Local-first AI coding projects

| Project | What it does | Relevance |
|--|--|--|
| **localcoder** | Local AI coding agent (Ollama + Gemma/Qwen, zero cloud) | Proves local AI coding is viable in 2026 |
| **Ollama** | Run LLMs locally (Llama, Gemma, Qwen, etc.) | BYO_AI already integrates this |
| **Open WebUI** | ChatGPT-like UI for local models | UX reference for local AI chat |

### Local-first sync / data projects

| Project | What it does | Relevance |
|--|--|--|
| **ElectricSQL** | Postgres → SQLite read-path sync (local-first) | Relevant if Founder OS adds local-first sync later |
| **PowerSync** | Bidirectional Postgres ↔ SQLite sync | Relevant for mobile/offline apps founders build |
| **Zero** | Client-side reactive cache (by Rocicorp) | Relevant for instant-UI local-first apps |

### The "sovereign indie hacker" movement

This is not just a technology trend — it is a cultural one. A widely-circulated 2026 DEV.to post ("I Run 107 Repos from a Mac Mini in Goa") documents a founder running an entire AI infrastructure stack (Ollama, 15 services, Claude Code, Cloudflare tunnel) from a base-model Mac mini for $0/month in cloud spend. The post articulates the philosophy clearly:

> "If you can't run it without an internet connection, you don't own it."

And the economics:

> "A Mac Studio costs what 4 months of GPU cloud would. It'll last 5 years. The math isn't close."

This is the same economic argument Founder OS already makes in its product spec: *"the laptop is the compute, the cloud is the exception."* The open source ecosystem has caught up to make this practical.

---

## 9. Recommended Stack for Private Mode

### The bundle

```
┌─────────────────────────────────────────────────────────┐
│                   FOUNDER STACK (Private mode)           │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  Founder IDE │  │  Founder Node│  │   Forgejo     │  │
│  │  (Cursor or  │  │  (tray app,  │  │  (git forge,  │  │
│  │  VS Code or  │  │  local vault,│  │  web UI at    │  │
│  │  Founder IDE)│  │  AI gateway) │  │  :3000)       │  │
│  └──────────────┘  └──────────────┘  └───────────────┘  │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │   SQLite     │  │  Cloudflare  │  │   Tailscale   │  │
│  │  (dev.db,    │  │   Tunnel     │  │   (mesh net   │  │
│  │  zero config)│  │  (public URL │  │  for phone    │  │
│  │              │  │   on demand) │  │   remote)     │  │
│  └──────────────┘  └──────────────┘  └───────────────┘  │
│                                                          │
│  Total cloud cost: $0/month                              │
│  Total RAM overhead: ~600–900 MB                         │
│  Visibility: private (founder only) unless tunnel is on  │
└─────────────────────────────────────────────────────────┘
```

### Component roles

| Component | Role | Already in Founder Stack? |
|--|--|--|
| Founder Node | Tray app, local vault, AI gateway routing | ✅ Yes (0.8.0) |
| Founder OS Chat extension | AI provider in editor | ✅ Yes (0.1.0) |
| Forgejo | Local git forge + web UI | ❌ **Add to installer** |
| SQLite | Local database | ✅ Already available (bundled with Node.js) |
| cloudflared | Public URL exposure | ❌ **Add to installer** |
| Tailscale | Phone remote mesh networking | ❌ **Add as optional install** |

### What the founder experience looks like

1. **Install.** Founder downloads `Founder-Stack-Setup.exe`, checks "Enable Private mode." The installer sets up Founder Node, the editor extension, Forgejo, and cloudflared.

2. **First project.** Founder creates a new project in Founder IDE. Founder Node initializes a git repo, commits it to local Forgejo, creates a SQLite database file, and starts the dev server on `localhost:3000`.

3. **Building.** Founder codes with AI (via the chat extension, routed through the AI gateway). Every save rebuilds. Schema changes run via `prisma db push` against the local SQLite file. Commits go to local Forgejo with full history.

4. **Sharing.** Founder clicks "Share" in Founder Node. Cloudflare Tunnel spins up and returns a public HTTPS URL. The founder sends it to a collaborator or opens it on their phone.

5. **Phone remote.** Founder opens the `/phone` URL on their phone (over Tailscale or the tunnel). They pick up the coding session from the couch, the coffee shop, or the train.

6. **Going Public (later).** When ready to launch, the founder flips to Public mode: pushes Forgejo repo to GitHub, migrates SQLite to Neon, deploys to Vercel/Railway. See section 10.

---

## 10. Migration Path — Private → Public

### The principle: nothing is lost, nothing is rewritten

The Private → Public migration should be a **configuration change, not a reimplementation.** The founder's code, schema, git history, and data all move with them. This is the payoff of choosing standards-based local tools (git, SQLite, Prisma, standard web frameworks) instead of proprietary ones.

### Step-by-step migration

#### Step 1: Git — Push local Forgejo to GitHub

```bash
# Add GitHub as a remote to the existing local repo
git remote add github git@github.com:founder/project.git

# Push the full history (all Private mode commits are preserved)
git push github main
```

Optionally, configure Forgejo to auto-mirror-push to GitHub on every future commit. The founder now has a public GitHub repo with their complete build history.

#### Step 2: Database — Migrate SQLite to Neon Postgres

```prisma
// schema.prisma — change one line
datasource db {
  provider = "postgresql"   // was: "sqlite"
  url      = env("DATABASE_URL")
  directUrl = env("DIRECT_DATABASE_URL")
}
```

```bash
# 1. Create a Neon project, get connection strings
# 2. Generate the migration against the local SQLite schema
npx prisma migrate dev --name init_postgresql

# 3. Apply the migration to Neon
npx prisma migrate deploy

# 4. Transfer the data (SQLite → Postgres)
#    Use pgloader for a one-time data migration:
pgloader ./dev.db postgresql://user:pass@neon.host/db
```

The Prisma schema (models, relations, fields) does not change. Only the `provider` and connection strings change. The application code that uses Prisma Client does not change at all.

#### Step 3: Hosting — Deploy to Vercel / Railway

```bash
# Push to GitHub (already done in Step 1)
# Vercel/Railway auto-deploys from GitHub
# Set DATABASE_URL to the Neon connection string in Vercel env vars
```

The same code that ran on `localhost:3000` now runs on `https://app.foundersdomain.com`. The only differences: it is on Vercel's CDN, it connects to Neon instead of a local file, and it stays up when the founder's laptop closes.

#### Step 4: Tunnel — Retire Cloudflare Tunnel (or keep it for staging)

The founder can now stop the Cloudflare Tunnel (the app is on Vercel). Or keep it running as a staging environment — `staging.foundersdomain.com` via tunnel points to the laptop for testing changes before they ship to production.

### What does NOT change in the migration

- **Code.** Same files, same framework, same logic.
- **Prisma schema.** Same models, same relations. Only the `provider` line changes.
- **Git history.** Every Private mode commit is in the GitHub repo.
- **Founder Node.** Still runs locally as the AI gateway and tray app.
- **Founder IDE.** Same editor, same extension.
- **Phone remote.** Still works, now pointing at the production URL (or still at the laptop via Tailscale).

### The migration is reversible

If a founder goes Public and then decides they want to come back to Private (e.g., the cloud bills are too high, or they want to iterate privately on a v2), the path is:

1. Stop deploying to Vercel. The app is still running on `localhost`.
2. Export the Neon database back to SQLite (via `pgloader` reverse, or `pg_dump` + conversion).
3. Change `provider` back to `sqlite` in `schema.prisma`.
4. Continue building privately.

This reversibility is a feature, not a bug. It means Public mode is not a one-way door. The founder's commitment to the cloud is always month-to-month.

---

## Appendix A: Why Not Coolify / Dokploy for Private Mode?

Coolify and Dokploy are excellent self-hosted PaaS platforms (the leading Vercel/Heroku alternatives in 2026), but they are designed for **VPS deployment, not laptop use.**

- **Resource footprint:** Both require 2 GB+ RAM minimum and run Docker under the hood. Coolify idles at 500 MB–1.2 GB; Dokploy at ~350–600 MB. This is too heavy to run alongside Founder Node + an IDE + a dev server on a typical laptop.
- **Target environment:** Both assume a Linux server with SSH access and a public IP (or a tunnel in front). They are the right tool for the founder who has graduated from Private mode to a $5–20/month VPS and wants "git push → live URL" without managing Docker manually.
- **Complexity:** Both require Docker knowledge. A non-technical founder in Private mode should not need to understand containers.

**Recommendation:** Recommend Coolify/Dokploy as the **next step after Private mode** — when the founder has outgrown the laptop (needs 24/7 uptime, more RAM, more storage) but does not want to go full-cloud (Vercel + Neon). Founder OS could eventually offer a "Private+ mode" that provisions a VPS with Coolify and connects it to the founder's existing Forgejo + Prisma stack. But that is a future phase, not v1 of Private mode.

---

## Appendix B: Cost Comparison — Private vs Public over 12 months

| Phase | Private mode (laptop) | Public mode (cloud) |
|--|--|--|
| Months 1–3 (building, no users) | **$0** — laptop + SQLite + tunnel | $0–5/mo — Vercel free + Neon free (if you avoid overages) |
| Months 4–6 (beta, <100 users) | **$0** — laptop handles it easily | $5–20/mo — Vercel Pro + Neon Launch |
| Months 7–12 (growth, 1K+ users) | Laptop starts to strain (uptime, bandwidth) | $20–100/mo — Vercel + Neon scaling |
| Break-even point | Laptop must stay on 24/7 for production | Cloud pays for itself once you have real users |

**The Private mode economic argument:** a founder saves $0–60/mo during months 1–6 by staying private, which is exactly when they have the least revenue. The laptop is a sunk cost (they already own it). Cloud spend is real money leaving their account every month. Founder OS's economic bet — *"the laptop is the compute, the cloud is the exception"* — is mathematically correct for the first 6–12 months of most founder journeys.

---

## Appendix C: References

### Self-hosted git
- [Forgejo vs Gitea 2026](https://techfuelhq.com/homelab/forgejo-vs-gitea-2026/)
- [Best Self-Hosted Git Platforms 2026](https://selfhosting.sh/best/git-hosting/)

### Tunnels
- [Cloudflare Tunnel docs](https://developers.cloudflare.com/tunnel/)
- [Cloudflare Quick Tunnels](https://try.cloudflare.com/)
- [Cloudflare Tunnel vs ngrok 2026](https://meshwg.com/compare/cloudflare-tunnel-vs-ngrok/)

### Databases
- [SQLite vs Neon for Solo Developers 2026](https://solodevstack.com/blog/sqlite-vs-neon-solo-developers)
- [Turso + Prisma docs](https://www.prisma.io/docs/orm/v6/overview/databases/turso)
- [Neon: SQLite migration guide](https://neon.com/docs/import/migrate-sqlite)
- [Prisma + Neon migrations](https://neon.com/docs/guides/prisma-migrations)

### Self-hosted PaaS
- [Coolify vs Dokploy 2026](https://servercompass.app/blog/coolify-vs-dokploy-self-hosted-paas-comparison)
- [Self-Hosted PaaS Showdown 2026](https://dev.to/deploynix/self-hosted-paas-showdown-2026-coolify-vs-dokploy-vs-caprover-vs-deploynix-46l3)

### CI
- [act: Run GitHub Actions locally](https://github.com/nektos/act)
- [Woodpecker CI local execution](https://woodpecker-ci.org/docs/usage/local-execution)

### Dev environments
- [Daytona vs DevPod vs Coder 2026](https://ossalt.com/guides/daytona-vs-devpod-vs-coder-dev-envs-2026)

### Phone remote / mesh networking
- [TailClaude](https://github.com/mz0in/tailclaude)
- [GhostTerm](https://github.com/chengwaye/ghostterm)
- [Rove](https://github.com/AleksandreJavakhishvili/Rove)
- [Run Claude Code from your phone (2026)](https://rizz.dev/blog/tutorials/claude-code-from-your-phone)

### Local-first philosophy
- [I Run 107 Repos from a Mac Mini in Goa](https://dev.to/paul_desai_ff9e1e7b5605ef/i-run-107-repos-from-a-mac-mini-in-goa-6hm)
- [How to Build a Local-First Tech Stack 2026](https://medium.com/codetodeploy/how-to-build-a-local-first-tech-stack-replace-paid-ai-saas-tools-in-2026-0c216fbfaf9b)

### UX reference
- [Lovable vs Bolt vs Replit 2026](https://tessellatelabs.com/knowledge/lovable-vs-bolt-vs-replit-2026)
- [run.dev](https://getrun.dev/)

---

**End of research.** This document is research-only. It does not modify any code. Implementation of Private mode would touch the Founder Stack installer (`packages/founder-ide/installer/`) and Founder Node — separate engineering work.
