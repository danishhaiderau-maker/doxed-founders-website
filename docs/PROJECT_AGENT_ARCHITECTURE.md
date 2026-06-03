# Project agent architecture

Each **approved project** on Doxxed Crypto gets a full copy of the platform agent roster (Community Manager, Marketer, Builder, Researcher, etc.). Agents are **loyal to the project profile they run under** — not neutral platform bots.

## Layers

| Layer | Who provides it | Role |
|-------|-----------------|------|
| **Platform shell** | Doxxed Crypto | Agent templates, permissions, UI, build queue, GitHub/Cursor hooks, feed & scout surfaces |
| **Brain (LLM)** | Founder’s connected AI | Reasoning, tone, answers, marketing copy, community replies — billed to founder’s API key |
| **Vault memory** | Founder Node (optional) | Private goals/tasks on device; only metadata relay to cloud |
| **Code worker** | Cursor / OpenHands (optional) | Implements specs in GitHub — separate from “brain” |

## Loyalty model

When an agent runs in **Project A’s** workspace:

- System prompts include **project name, ticker, repo, and public context** for Project A only.
- Community Manager drafts replies that support **Project A** scouts and holders.
- Marketer promotes **Project A** releases and defends against FUD for **Project A**.
- Agents must not advocate for competing projects on the same run.

This matches “aggressive but on-brand” promotion: mediation and marketing serve **one** founder team.

## Community & marketing flows

1. **Community question** (feed, scout thread, town hall)  
   → Agent uses founder’s **default LLM** (or selected provider)  
   → Reads public project memory + policy  
   → Drafts reply; founder approves or posts via platform tools  

2. **Release / marketing push**  
   → Marketer agent + LLM drafts posts  
   → Optional publish to feed / X via Founder OS  
   → Cursor optional for landing-page or repo changes  

3. **Builder agent**  
   → Platform generates spec/tasks (rule-based or LLM)  
   → Optional **Cursor** dispatch for code  
   → Brain does not replace Cursor for repo edits  

## Recommended setup per founder

1. Pair **Founder Node** once (code hides after pair).  
2. Connect **one LLM** minimum (OpenRouter, DeepSeek, Ollama, Phala).  
3. Set **default Copilot provider** to that LLM.  
4. Connect **GitHub** + optional **Cursor**.  
5. Open **Agent Workforce** under your project; run agents with project context loaded.  

## Evolution (suggested)

- **Per-agent provider override** — e.g. Community uses Claude, Marketer uses DeepSeek.  
- **Approval queue** — all public agent posts require founder click-to-publish.  
- **Project-scoped API keys** — team members share one project brain.  
- **Audit log** — which agent posted what, with model id, for transparency on doxxed founders.  

Current codebase: workforce templates in `@dcf/utils` (`WORKFORCE_TEMPLATES`, `WORKFORCE_PERMISSIONS`); runtime in `BuildQueueService.executeWorkforceRuntime`; Copilot routing in `FounderCopilotService`.
