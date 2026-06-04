# External Audit Entry Point

**This file is safe to share.** It contains no secrets.

For ChatGPT, security reviewers, or architecture audits:

1. Read **[docs/AUDIT_FOR_CHATGPT.md](docs/AUDIT_FOR_CHATGPT.md)** — scope, checklist, boundaries  
2. Read **[docs/MISSION.md](docs/MISSION.md)** — product purpose and goals  
3. Read **[docs/REPOSITORY_LAYOUT.md](docs/REPOSITORY_LAYOUT.md)** — public vs private files  
4. Run **`npm run audit:export`** — generates code-only bundle at `../doxedcryptofounder-audit/`  
5. Zip that folder + attach generated **`AUDIT_SCOPE.txt`**

**Never share:** `../doxedcryptofounder-secrets/` or any `.env` file.
