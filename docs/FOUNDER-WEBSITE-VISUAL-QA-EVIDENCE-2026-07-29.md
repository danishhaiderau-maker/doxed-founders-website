# Founder Website Visual QA Evidence

Date: 2026-07-29

This is local source evidence. It does not claim that the website changes are
deployed or that production connectors are healthy.

## Founder workspace

The authenticated, production-shaped Founder workspace harness passed at:

- 1440x900
- 1024x768
- 390x844

All three screens returned 200 and had:

- no missing required Founder workspace text;
- no forbidden Void, phase, or unlimited-AI claims;
- no critical console or page errors;
- no bad responses from the explicit API fixtures;
- no horizontal overflow or identified overflowing elements; and
- visible Continue building, Control desktop, Connect services, Manage AI,
  and Plan and usage controls.

## Downloads

Direct browser inspection found a real React hydration mismatch in
`FounderNodeDownloads`. The server rendered OS-neutral markup while the first
browser render detected Windows and inserted a second beta link. The browser
discarded and regenerated the tree.

The component now starts with an OS-neutral state and detects the OS only after
hydration. A server-render regression test proves that the initial markup is
identical with and without a Windows `navigator`.

The repeatable downloads harness then passed at:

- 1440x900
- 390x844

Both screens returned 200 and had:

- one All downloads heading and one Founder IDE desktop section;
- one-app and embedded-Node release language;
- no Founder Stack, Founder Copilot, or Cursor Pro saved text;
- no console errors, page errors, bad responses, or unexpected API calls; and
- no horizontal overflow or identified overflowing elements.

The visual harness uses explicit read-only fixtures for shared shell data so an
offline local API does not create false visual failures. Any new unlisted API
dependency fails the harness.

## Automated checks

- Founder workspace visual harness: 3 screens, 0 failures.
- Downloads visual harness: 2 screens, 0 failures.
- Downloads hydration stability: 1 passed, 0 failed.
- Full web no-output TypeScript: passed.
- Whitespace check: passed.

## Evidence paths

`C:\Users\user\Desktop\Final Bots\artifacts\founder-website-v1-visual-qa-20260729\`

The directory contains:

- `desktop-founder-workspace.png`
- `tablet-founder-workspace.png`
- `mobile-founder-workspace.png`
- `founder-workspace-evidence.json`
- `desktop-downloads.png`
- `mobile-downloads.png`
- `evidence.json`

## Development performance

The first local webpack route compilation measured about 140 seconds for
`/founder-os` and 142 seconds for `/downloads`. The full web no-output
TypeScript pass measured about 254 seconds.

A Turbopack experiment first exposed a stale local dependency junction from
this worktree into `founder-vision-api`. The junction was replaced with a link
to this worktree's own root dependencies; no package or source file was
deleted. Turbopack then started without that junction panic but still did not
serve the route inside the acceptance window. No unproven fast command or
configuration was committed.

The extension fast lane remains the correct path for Founder IDE surface
iteration. Website cold-start performance remains an open build-lane issue and
must be fixed with measurements before it is called complete.

## Still open

- signed-in visual sweeps for account, settings, usage, phone, authorization,
  and admin routes;
- loading, empty, error, offline, reduced-motion, and success state coverage;
- production plan, quota, AI route, device, and connector evidence;
- final Apple-inspired visual-system alignment after product hierarchy freeze.

