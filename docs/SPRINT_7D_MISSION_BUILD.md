# Sprint 7d — Mission State → one-click build

## Shipped

- **`POST /copilot/mission-build`** — reads Mission State (`current_task`, `next_action`, `active_goal`), syncs GitHub, dispatches Cursor/OpenHands via `executeBuildTask`.
- **`runMissionBuild`** shared with **`POST /copilot/resume`** (resume still fills Copilot + dispatches + polls when remote).
- **Mission State panel** — primary **▶ Run build** button, live status line, agent link, after-build graph patch via polling.
- **Mission Control** — header **Run build**, sidebar **Run current task**, poll + `after-build` sync on completion.

## Flow

1. Founder sets goal / task / next action in Mission State.
2. **Run build** → API builds prompt from graph → builder worker.
3. Web polls Cursor/OpenHands until terminal.
4. **`POST /copilot/memory-graph/after-build`** promotes `next_action` → `current_task` on success.

## Requirements

- Cursor or OpenHands connected in Settings → AI stack.
- GitHub repo linked for best results (optional).
