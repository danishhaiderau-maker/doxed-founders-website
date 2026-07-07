# Local Staging Workflow — Option 4

Instead of pushing every change to GitHub immediately (which triggers
CI on each push and burns Actions minutes), this workflow stages
changes locally and only pushes when you explicitly request it.

## How to use it

### 1. Make changes locally (default mode)
Cursor agent works normally in the local checkout. Changes accumulate
in `git status` as unstaged/staged files. Nothing pushes automatically.

### 2. Check what's staged with `npm run stage:status`
Shows:
  - Files changed since last push
  - Approx Actions minutes saved (count of would-have-pushed commits)
  - Suggested batch commit message

### 3. When ready to deploy, run `npm run stage:push`
  - Creates ONE squashed commit with all staged changes
  - Pushes to origin/master
  - Triggers CI exactly ONCE (not N times)
  - Logs what was shipped

### 4. CI runs once, Railway/Vercel auto-deploy

## When NOT to use this

- Hotfix for a live incident (push immediately)
- Small one-line fix that's clearly safe (push immediately)
- Anything you want deployed ASAP

## When to use this

- Exploratory work that takes many small steps
- Multi-file refactors where intermediate states are broken
- Working through a list of independent improvements
- Anything where you'd otherwise push >3 commits in one session

## Safety

- `stage:status` is non-destructive - just shows what's pending
- `stage:push` always creates a NEW commit (no force-push, no history rewrite)
- You can run `stage:push` with `--dry-run` to see exactly what would happen
- If something goes wrong, `git reset HEAD~1` undoes the squash locally before push

## Files

- `scripts/stage-status.mjs` - shows pending changes + minutes saved
- `scripts/stage-push.mjs` - squashes and pushes a batch
- `package.json` scripts:
  - `npm run stage:status`
  - `npm run stage:push`
  - `npm run stage:push -- --dry-run`
  - `npm run stage:push -- --msg "custom message"`
