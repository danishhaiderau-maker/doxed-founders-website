# Mobile analyzer access

`http://127.0.0.1:9001/` is intentionally loopback-only. On a phone,
`127.0.0.1` refers to the phone, so `ERR_CONNECTION_REFUSED` is expected.

Use the authenticated Fly mirror instead:

`https://doxed-btc-bot.fly.dev/analysis`

The first visit redirects to `/analysis/login`. Enter the current owner token
in the password field. The token is POSTed and is never placed in the URL. The
server exchanges it for a derived, HttpOnly, analyzer-only cookie scoped to
`/analysis`. That cookie is not accepted by trading or settings endpoints.

The page is a read-only static snapshot published by the desktop sync loop.
It can be viewed while the laptop is offline, but its timestamp will stop
advancing until the desktop analyzer and publisher resume. The local dashboard
continues to bind only to `127.0.0.1`, and no router port or public tunnel is
opened.

Security invariants:

- Never share a URL containing `admin_token` or any other credential.
- Do not bind the local analyzer to `0.0.0.0`.
- Do not expose port 9001 through a quick tunnel.
- Rotate `BOT_ADMIN_TOKEN` to invalidate both admin and analyzer sessions.
