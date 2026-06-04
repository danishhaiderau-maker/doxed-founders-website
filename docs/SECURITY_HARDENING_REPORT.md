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

Latest live recheck (2026-06-03 23:43 UTC):

- Web currently returns HSTS with `includeSubDomains; preload`, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and `Permissions-Policy`.
- API currently returns HSTS with `includeSubDomains`, `X-Content-Type-Options`, `X-Frame-Options`, and `Referrer-Policy`.
- API still returns `X-Powered-By: Express` on the live Railway endpoint. The branch code disables this header; deploy/merge this branch to remove it from production.
- API live `Permissions-Policy` was not present in the header check; the branch code adds it.

Changes implemented:

- Web now sets:
  - `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()`
- API now sets the same core hardening headers.
- API disables `X-Powered-By`.

## OWASP ZAP / Burp status

The cloud agent image used for this pass did not include OWASP ZAP, Burp Suite, Docker, or `zap-baseline.py`; only Java and curl were available during the latest recheck. Because of that, this pass used non-invasive live header checks and repository review instead of an authenticated ZAP/Burp crawl.

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

- Run an OWASP ZAP passive baseline scan from CI or a security workstation after deployment.
- Add GitHub secret scanning / push protection if not already enabled on the repository.
- Consider adding a Content Security Policy in report-only mode before enforcement, because Next.js and third-party auth/social flows can require careful script/connect/image allowances.
- Rotate production secrets if any have ever been pasted into local `.env` files outside the repo.
