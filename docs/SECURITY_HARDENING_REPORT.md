# Security Hardening Report

## Scope

This report covers the June 2026 hardening pass for the live Doxxed Founder web and API surfaces.

Targets checked:

- Web: `https://doxxedcrypto.digital`
- API health endpoint: `https://doxed-founders-website-production.up.railway.app/api/health`

## Live header check

Commands used:

```bash
curl -I -L --max-time 20 https://doxxedcrypto.digital
curl -I -L --max-time 20 https://doxed-founders-website-production.up.railway.app/api/health
```

Findings before this change:

- Web returned `Strict-Transport-Security: max-age=63072000` from Vercel.
- Web did not explicitly include `includeSubDomains` or `preload` in the repo-level config.
- API did not return `Strict-Transport-Security`.
- API returned `X-Powered-By: Express`.
- API CORS response was origin-varying and credential-aware.

Latest live recheck (2026-06-09 15:07 UTC):

- Web currently returns HSTS with `includeSubDomains; preload`, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and `Permissions-Policy` on the main HTML response. Production does not yet return the branch CSP header.
- API currently returns HSTS with `includeSubDomains`, `X-Content-Type-Options`, `X-Frame-Options`, and `Referrer-Policy`.
- API no longer returns `X-Powered-By`.
- API live `Permissions-Policy` was not present in the header check; the branch code adds it.

Changes implemented:

- Web now sets:
  - `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()`
  - `Content-Security-Policy` with self-first defaults and explicit OAuth form-action allowances
- API now sets the same core hardening headers.
- API disables `X-Powered-By`.

## OWASP ZAP / Burp status

A manual GitHub Actions OWASP ZAP baseline run completed successfully on 2026-06-04. A scheduled baseline run also completed successfully on 2026-06-08. Latest summary: 0 failures, 12 warnings, 58 passes. The main actionable web-app warning remains `Content Security Policy (CSP) Header Not Set`; this branch adds a conservative CSP header in `apps/web/next.config.ts`, but it has not reached production yet. ZAP also reported HSTS missing on several cached/static `304 Not Modified` responses even though the main HTML response includes HSTS.

The cloud agent image used for local rechecks did not include OWASP ZAP, Burp Suite, Docker, or `zap-baseline.py`; only Java and curl were available locally. Because of that, local rechecks used non-invasive live header checks and repository review while CI runs the ZAP baseline.

A scheduled/manual GitHub Actions workflow has been added at `.github/workflows/security-zap-baseline.yml` to run a passive ZAP baseline against the live site and upload HTML/Markdown/JSON reports as artifacts.

Manual follow-up from a security workstation remains available:

```bash
docker run --rm -t ghcr.io/zaproxy/zaproxy:stable \
  zap-baseline.py -t https://doxxedcrypto.digital -r zap-baseline.html
```

Use the baseline/passive scan first. Avoid active scans against production unless an explicit maintenance window and test account are available.

## API key and secret review

Repository scan patterns checked for common committed secrets:

- OpenAI-style `sk-*`
- GitHub PATs (`ghp_*`, `github_pat_*`)
- Slack tokens
- AWS access keys
- Private key blocks
- Long assigned `SECRET`, `TOKEN`, `API_KEY`, `PRIVATE_KEY`, and `PASSWORD` values

Findings:

- No live API keys or private key material were found in tracked source.
- Matches were placeholders in example env/template files.
- `.gitignore` excludes local secret files such as `.env.x.secrets`.
- JWT fallback references existed across API modules; these now use a shared resolver that fails production startup unless `JWT_SECRET` is strong.

## Remaining recommendations

- Deploy the CSP header to production, then re-run the OWASP ZAP passive baseline and review whether the CSP warning is resolved.
- Investigate whether Vercel/Next headers apply consistently to cached static `304 Not Modified` responses, because ZAP still reports HSTS missing on some cached resources.
- Add GitHub secret scanning / push protection if not already enabled on the repository.
- Tighten CSP over time by removing `unsafe-inline` / `unsafe-eval` after adding nonce or hash support for Next.js runtime scripts.
- Rotate production secrets if any have ever been pasted into local `.env` files outside the repo. This repo scan cannot rotate secrets; rotation must happen in Vercel/Railway/GitHub secret stores.
