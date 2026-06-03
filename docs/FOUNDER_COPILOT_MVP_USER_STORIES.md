# Doxxed Founder Copilot - MVP User Stories

## Scope

This MVP focuses on Founder Copilot as a browser-based research and content-generation assistant for doxxed crypto founders. It should help a founder understand what is happening around their project, turn raw activity into useful summaries, and draft public-facing content without losing connection to GitHub, project memory, and Founder OS context.

The MVP is not a general chatbot. It is a project-aware copilot that answers from connected sources and produces drafts the founder can review before publishing.

## Primary users

- **Founder:** building a crypto product and wants help tracking progress, researching the market, and publishing updates.
- **Contributor/operator:** helps the founder organize tasks, research competitors, and prepare launch content.
- **Community reader/investor:** indirectly benefits from clearer build updates, project explainers, and evidence-backed public communication.

## MVP success criteria

- A founder can ask "what am I working on?" and receive a specific answer grounded in project memory, GitHub activity, and open tasks.
- A founder can request a research brief and receive a structured, source-aware summary they can act on.
- A founder can turn recent build activity into a draft update for the build feed, X, or community channels.
- Copilot clearly separates facts, assumptions, and suggested next steps.
- Generated content remains draft-first: founders approve before publishing.

## Core research user stories

### R1 - Current work summary

**As a founder,** I want Copilot to summarize what I am currently working on, **so that** I can quickly regain context when I return to Founder OS.

**Acceptance criteria**

- Uses current goal, latest GitHub commits, open build queue items, roadmap state, and recent Founder OS events when available.
- Names the active project and connected repository when known.
- Calls out the most likely next step.
- If data is missing, explains which connection or sync is missing instead of guessing.

### R2 - Recent progress digest

**As a founder,** I want a digest of recent commits, deploys, tasks, and community activity, **so that** I can understand what changed since my last session.

**Acceptance criteria**

- Groups activity by source: GitHub, deploys, build queue, community, and launch readiness.
- Highlights meaningful product progress over noisy event counts.
- Includes a short "founder takeaway" and "next action" section.
- Handles empty activity gracefully.

### R3 - Competitor and market research brief

**As a founder,** I want Copilot to create a competitor or market brief from my prompt and project context, **so that** I can position my project clearly.

**Acceptance criteria**

- Produces a structured brief with market category, likely competitors, differentiators, risks, and open questions.
- Labels unsupported claims as assumptions.
- Suggests follow-up research tasks for the build queue.
- Avoids fabricating exact metrics unless sourced by connected data or supplied context.

### R4 - Investor/customer explainer

**As a founder,** I want Copilot to explain my project in plain language for investors or users, **so that** I can communicate the thesis without rewriting from scratch.

**Acceptance criteria**

- Uses project description, current goal, build history, and launch readiness signals.
- Provides a concise one-liner, longer narrative, and proof points.
- Separates "what is built" from "what is planned."
- Includes risk or uncertainty notes when the project is early.

### R5 - Research-to-task conversion

**As a founder,** I want Copilot to turn a research finding into concrete next tasks, **so that** research directly improves the build queue.

**Acceptance criteria**

- Creates P0/P1 task suggestions with clear outcomes.
- Identifies whether each task is product, engineering, content, community, or launch work.
- Can optionally prepare GitHub issue titles if GitHub PAT is connected.
- Avoids creating duplicate tasks when similar open items already exist.

## Core content-generation user stories

### C1 - Build update draft

**As a founder,** I want Copilot to draft a build update from recent activity, **so that** I can publish consistently without manually summarizing every commit.

**Acceptance criteria**

- Converts technical work into founder-friendly language.
- Includes what shipped, why it matters, what is next, and a transparent proof point.
- Can generate variants for build feed, X, and project room/community.
- Keeps publishing as an explicit review step.

### C2 - Weekly founder update

**As a founder,** I want a weekly update draft, **so that** my community and supporters can follow progress over time.

**Acceptance criteria**

- Summarizes the week using commits, tasks, deploys, and community activity.
- Includes sections for progress, blockers, asks, and next week.
- Produces a short version and a longer narrative version.
- Avoids overstating progress when evidence is thin.

### C3 - Launch or roadmap announcement

**As a founder,** I want Copilot to draft a launch or roadmap announcement, **so that** I can communicate milestones clearly.

**Acceptance criteria**

- Uses roadmap state, launch readiness, and current build queue context.
- Includes audience-specific framing for builders, users, and supporters.
- Flags missing launch prerequisites before producing overly promotional copy.
- Provides a checklist of assets or facts needed before publishing.

### C4 - X/social thread draft

**As a founder,** I want Copilot to create an X thread from project progress or research, **so that** I can build in public with less friction.

**Acceptance criteria**

- Produces a hook, 3-7 post thread, and closing call to action.
- Keeps claims grounded in project activity and avoids hype-only language.
- Suggests proof links such as GitHub commits, build feed posts, or project room links.
- Offers an alternate shorter post for quick publishing.

### C5 - Community response or FAQ draft

**As a founder,** I want Copilot to draft responses to repeated community questions, **so that** I can answer clearly and consistently.

**Acceptance criteria**

- Uses project facts and current state where available.
- Produces concise FAQ entries or a single reply.
- Marks uncertain answers for founder review.
- Can suggest a community thread or project-room update when the question is broadly useful.

## Cross-cutting MVP requirements

- **Context grounding:** Every answer should prefer connected project memory, GitHub repo data, build queue items, and Founder OS events over generic completion.
- **Source transparency:** Copilot should mention when an answer is based on synced data, cached data, or founder-provided prompt text.
- **Draft-first publishing:** Content generation should produce drafts and require founder confirmation before publishing.
- **No silent remote execution:** Research and content tasks can queue work, but code dispatch to Cursor must be explicit and repo-bound.
- **Privacy-aware output:** Copilot should avoid exposing private tokens, credentials, wallet secrets, or unsupported personal claims.
- **Duplicate control:** Copilot should avoid repeatedly creating the same "define MVP user stories" or similar task when an active item already exists.

## MVP non-goals

- Fully autonomous publishing without review.
- Unlimited web crawling or unsourced market claims.
- Replacing local Cursor for unsynced files.
- Live trading, financial advice, or token price predictions.
- Multi-project portfolio management beyond the founder's active project context.

## Suggested P0 delivery order

1. Current work summary.
2. Recent progress digest.
3. Build update draft.
4. Weekly founder update.
5. Competitor/market research brief.
6. Research-to-task conversion.

## Open product questions

- Which sources should be shown as citations in the UI versus summarized in prose?
- Should research briefs be stored as build queue items, suggested updates, or a separate research artifact?
- What is the minimum evidence required before Copilot can generate an investor-facing proof point?
- How should Copilot detect and merge duplicate content-generation tasks?
