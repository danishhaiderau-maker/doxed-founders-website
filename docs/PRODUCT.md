# Founder OS — Product Constitution

| Field | Value |
|-------|-------|
| **Status** | DRAFT |
| **Authored** | 2026-07-07 |
| **Owners** | Founder |

> This is the constitution. Every engineer reads it before writing a line of Founder OS code. If a change contradicts a tenet below, the change is wrong unless the founder amends this doc.

---

## 1. Thesis

**Founder OS exists to compound founder intelligence over time.** Every subsystem — Memory, Routing, Intent, Learning — contributes to making the founder better at building their company. The product wins when a founder who has used Founder OS for six months is materially faster, better-informed, and harder to displace than the same founder on day one. The platform's job is to make that compounding visible and irreversible.

---

## 2. Three core tenets

1. **Founder time is the only scarce resource.** Every design decision asks: "Did this save the founder time?" If a feature does not save founder time, it does not ship.
2. **Persistent operational intelligence is the moat.** Not routing alone. Memory + Routing + Learning. The longer a founder uses Founder OS, the harder it is to leave — because all of their accumulated company knowledge lives here.
3. **Founder OS is an Operating System for Building Companies.** Not an IDE. Not a Cursor competitor. Not a chatbot. The OS layer that sits above AI models and execution environments.

---

## 3. The Principles

- AI models are infrastructure. Today GLM/DeepSeek/Claude; tomorrow whatever exists in 2028.
- Founder OS owns persistent operational intelligence.
- Every feature must improve founder productivity.
- Every kernel sprint must deliver visible founder value.
- Applications depend on the kernel.
- The kernel never depends on applications.
- Memory is company memory, not chat history.
- Routing is capability-based, not provider-name-based.
- Founder OS never competes with editors; it orchestrates them.
- DDollar is in-game currency for engagement tracking, not a security.
- Doxxing is the product — the platform's trust signal is human review.
- Token launch is Phase 7+, not Phase 1. The kernel must earn the right to ask founders to launch.

---

## 4. What Founder OS is NOT

- NOT an IDE
- NOT an AI model
- NOT a Cursor competitor
- NOT a chatbot
- NOT a marketplace (that's an app on top)
- NOT a launchpad-first product (the launch flow exists, but ships after the kernel is mature)
- Does NOT bill founders for tokens directly
- Does NOT mark up AI costs
- Does NOT accept BYOK (Bring Your Own Key is dead)
- Does NOT compete with applications built on top of it

---

## 5. The architectural pattern

Every kernel service follows the **Input → Decision → Output** pattern.

- **Memory:** Input (Current Workspace) → Decision (What is relevant?) → Output (Context)
- **Routing:** Input (Capability Request) → Decision (Best capability) → Output (Execution Plan)
- **Intent:** Input (Founder Goal) → Decision (Tasks) → Output (Execution Graph)
- **Execution:** Input (Execution Graph) → Decision (Execution Target) → Output (Completed Actions)
- **Learning:** Input (Outcome Signal) → Decision (Weight Update) → Output (New Weights)
- **DDollar:** Input (Charge Event) → Decision (Tier/Balance Check) → Output (Ledger Entry)
- **Auth:** Input (Request) → Decision (Identity Check) → Output (Token)

The Decision step is pure — no side effects. The Output is observable. Full architecture in [`KERNEL.md`](./KERNEL.md).

---

## 6. Roadmap

| Phase | Name | Ships |
|-------|------|-------|
| 0 | Foundation docs | BILLING, PRODUCT, KERNEL |
| 1 | Kernel Foundation | Routing Engine v2, Memory skeleton, Flight Recorder, Execution Profiles |
| 2 | Visibility + Thin Workspace | Founder OS shell, two CTAs, Decision Log viewer, AI Usage dashboard |
| 3 | Execution Targets | Execution Manager interface + Cursor + 1 more IDE |
| 4 | Learning Engine | Outcome signals → routing weight updates |
| 5 | Founder Intent Engine | Goal → Task → Execution Graph |
| 6 | Workspace Polish | Daily-use quality |
| 7+ | Token Launch + DEX | Raise Room → Solana mint → DEX → Meteora |

---

## Change log

| Date | Change |
|------|--------|
| 2026-07-07 | Initial draft. |
