# Code Signing Guide — Founder Stack / Founder Node

Windows SmartScreen warns users when they run unsigned .exe files ("Windows protected your PC"). A code signing certificate makes SmartScreen trust your installer: an **EV** certificate is trusted immediately, while an **OV** certificate earns trust after a few downloads. Signed installs also show "Verified publisher: [Your Name]" instead of the scary warning.

> **Recommended path for the Founder Stack project: Azure Trusted Signing**
> (a.k.a. Azure Artifact Signing). It is the cheapest path to a signed
> installer — no hardware token, ~$10/month, Microsoft-hosted, integrates
> with GitHub Actions via `azure/artifact-signing-action@v2`. See
> [§0](#0-azure-trusted-signing-recommended-for-ci) below. The Certum
> instructions in [§1](#1-buy-a-certum-code-signing-certIFICATE)–[§5](#5-config-reference-already-in-appsfounder-nodepackagejson)
> are kept as a fallback for local/offline signing.

---

## 0. Azure Trusted Signing (recommended for CI)

Azure Trusted Signing (recently rebranded to **Azure Artifact Signing**) is a
Microsoft-hosted code-signing service. You don't own a private key — Microsoft
holds it in Azure Key Vault and signs on your behalf after OIDC-authenticating
your GitHub Actions workflow. This is what `build-founder-ide.yml` uses.

### 0.1 Account provisioning (one-time, manual gate)

This step takes **hours to days** of Microsoft review. Start it early.

1. Sign in to the Azure Portal (<https://portal.azure.com>) with an Azure
   subscription. Create one if you don't have one (free tier is fine — Trusted
   Signing itself is ~$10/month per certificate profile).
2. Search the marketplace for **Trusted Signing Accounts** (or **Artifact
   Signing Accounts** on the new portal UX). Create a Trusted Signing Account
   in a supported region (e.g. `East US` — the endpoint URL must match the
   region: `https://eus.codesigning.azure.net/`). Write the account name down;
   it goes into the `AZURE_TRUSTED_SIGNING_ACCOUNT_NAME` secret.
3. Inside the account, create a **Certificate Profile**. The profile name goes
   into `AZURE_TRUSTED_SIGNING_CERT_PROFILE_NAME`. Pick **OV** (Standard)
   unless you have an EV entitlement — OV builds SmartScreen reputation over
   time, EV is trusted immediately but costs more and takes longer to provision.
4. Submit the profile for **identity validation**. Microsoft reviews the
   publisher identity (this is the slow part — hours to days). You'll get an
   email when validation completes. The profile shows `Active` once validated.
5. While validation is pending, you can wire the GitHub Actions secrets — the
   workflow will fail at the signing step until validation completes, but
   every other step (build, verify, SBOM, provenance) runs.

### 0.2 App registration + federated credentials (OIDC)

Trusted Signing authenticates via OpenID Connect (OIDC) federation — no
long-lived client secret strictly required for the OIDC flow, but the
`azure/artifact-signing-action@v2` action supports both OIDC and the older
client-secret flow. We use the client-secret flow because it's what the action
defaults to and what the existing repo secrets name.

1. In Azure Portal, go to **Microsoft Entra ID** → **App registrations** →
   **New registration**. Name it e.g. `founder-stack-signing`.
2. Under **Certificates & secrets**, create a new **Client secret**. Copy the
   *Value* (not the Secret ID) immediately — it's only shown once. This goes
   into the `AZURE_CLIENT_SECRET` repo secret.
3. Copy the **Application (client) ID** → `AZURE_CLIENT_ID`.
4. Copy the **Directory (tenant) ID** → `AZURE_TENANT_ID`.
5. Under **API permissions**, add **Azure Key Vault** → **user_impersonation**
   (delegated). The Trusted Signing service uses Key Vault under the hood.
6. Back on the Trusted Signing Account, go to **Access control (IAM)** →
   **Add role assignment** → **Trusted Signing Certificate Profile Signer**
   → assign to the App registration you just created.

### 0.3 GitHub Actions secrets

In the GitHub repo (Settings → Secrets and variables → Actions), add these 6
secrets. **Never hardcode them in the workflow file or commit them to the repo.**

| Secret name | Value |
|---|---|
| `AZURE_TENANT_ID` | Directory (tenant) ID from §0.2 step 4 |
| `AZURE_CLIENT_ID` | Application (client) ID from §0.2 step 3 |
| `AZURE_CLIENT_SECRET` | Client secret Value from §0.2 step 2 |
| `AZURE_TRUSTED_SIGNING_ENDPOINT` | Region endpoint, e.g. `https://eus.codesigning.azure.net/` |
| `AZURE_TRUSTED_SIGNING_ACCOUNT_NAME` | Trusted Signing Account name from §0.1 step 2 |
| `AZURE_TRUSTED_SIGNING_CERT_PROFILE_NAME` | Certificate Profile name from §0.1 step 3 |

### 0.4 How the workflow uses them

`build-founder-ide.yml` signs **both** the inner `FounderIDESetup.exe` (step
16.1) and the outer `Founder-Stack-Setup-<v>.exe` (step 17.5) via:

```yaml
- uses: azure/artifact-signing-action@v2
  with:
    azure-tenant-id:              ${{ secrets.AZURE_TENANT_ID }}
    azure-client-id:              ${{ secrets.AZURE_CLIENT_ID }}
    azure-client-secret:          ${{ secrets.AZURE_CLIENT_SECRET }}
    endpoint:                     ${{ secrets.AZURE_TRUSTED_SIGNING_ENDPOINT }}
    trusted-signing-account-name: ${{ secrets.AZURE_TRUSTED_SIGNING_ACCOUNT_NAME }}
    certificate-profile-name:     ${{ secrets.AZURE_TRUSTED_SIGNING_CERT_PROFILE_NAME }}
    files-folder:                 <dir containing the .exe>
    files-folder-filter:          exe
    file-digest:                  SHA256
    timestamp-rfc3161:            http://timestamp.acs.microsoft.com
    timestamp-digest:             SHA256
```

The signing step is gated on
`github.event_name == 'push' || !github.event.inputs.skip_release` — so
artifact-only dev builds (`-f skip_release=true` from a workflow_dispatch)
skip signing for faster iteration. A later `signtool verify` step
hard-fails the build if a release build's exe is unsigned.

### 0.5 Verifying a signature (signtool)

`windows-2022` runners ship `signtool.exe` under
`C:\Program Files (x86)\Windows Kits\10\bin\<sdk-version>\x64\signtool.exe`
(the SDK version dir drifts across runner images — locate it with
`Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe"`).

```powershell
$signtool = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe" | Select-Object -Last 1
& $signtool.FullName verify /pa /v 'Founder-Stack-Setup-0.9.2.exe'
```

You should see `Successfully verified` and the publisher name from your
certificate profile.

### 0.6 Build provenance + SBOM (already wired in CI)

In addition to signing, the workflow:

- Generates an SPDX SBOM via `anchore/sbom-action@v0` (uploaded as a release
  asset named `founder-stack-<v>-sbom.spdx.json`).
- Creates a sigstore build-provenance attestation via
  `actions/attest-build-provenance@v1`, verifiable downstream with:
  ```bash
  gh attestation verify Founder-Stack-Setup-0.9.2.exe --repo <org>/<repo>
  ```

### 0.7 SmartScreen reputation

Trusted Signing **OV** profiles start with zero SmartScreen reputation —
users will see a soft warning for the first few hundred downloads, then it
fades. EV profiles are trusted immediately but cost more and take longer to
provision. There is no shortcut: you build reputation by being downloaded.

---

## 1. Buy a Certum code signing certificate

Certum is the cheapest reputable CA for individual / open-source developers.

1. Go to <https://certum.store/> (or <https://www.certum.eu/en/offer/code-signing-certificates/>).
2. Pick a product:
   - **Open Source Code Signing** — cheapest, ~€100–150/year. Standard **OV** trust (.pfx file). Ideal if you publish open source and can wait a few releases for SmartScreen reputation.
   - **Qualified Code Signing** — ~€200–300/year. **EV**-level trust, ships on a USB hardware token, SmartScreen trusts immediately.
3. Fill in personal details (name, email, phone, address). These appear **on the certificate** — make sure they're exactly how you want to be shown to users.
4. Complete identity verification. Certum emails instructions — usually a short video call or a document upload (ID + proof of address).
5. Pay.
6. Wait 1–3 business days for issuance.
7. Receive the certificate:
   - **OV** → you get a `.pfx` file download (and a password).
   - **EV / Qualified** → a USB hardware token is shipped to your address, plus a PIN.

---

## 2. Sign the Founder Node installer

The electron-builder config in `apps/founder-node/package.json` is already wired for signing. It uses a custom hook at `apps/founder-node/build/sign.js` that calls `signtool.exe` with RFC 3161 timestamping (sha256).

Pick **one** of the two paths below depending on which certificate you bought.

### Path A — OV cert (`.pfx` file)

Set two environment variables in the shell you build from (PowerShell):

```powershell
$env:CSC_LINK = 'C:\certs\founder-node.pfx'
$env:CSC_KEY_PASSWORD = 'the-password-certum-gave-you'
```

Then build as usual:

```powershell
cd apps\founder-node
npm run pack:win
```

`sign.js` detects `CSC_LINK`, invokes `signtool.exe sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 /f <pfx> /p <password> <file>`, and signs the `.exe` + uninstaller automatically.

### Path B — EV cert (USB hardware token)

electron-builder's built-in `CSC_LINK` flow does **not** work with a USB token — the private key never leaves the token, so you can't pass a `.pfx`. Instead `sign.js` uses `signtool` with the certificate's SHA-1 thumbprint, which makes the token sign.

1. Plug in the USB token Certum shipped you.
2. Install the Certum driver / SafeNet middleware if prompted (it ships with the token, or download from Certum).
3. Find the certificate's SHA-1 thumbprint. In PowerShell:

   ```powershell
   Get-ChildItem Cert:\CurrentUser\My | Format-List Subject, Thumbprint
   ```

   Copy the 40-char hex `Thumbprint` for your Certum cert (remove spaces).
4. Set the env var:

   ```powershell
   $env:SIGNTOOL_SHA1 = 'A1B2C3D4E5F6...'   # your thumbprint, no spaces
   ```

5. Build:

   ```powershell
   cd apps\founder-node
   npm run pack:win
   ```

`sign.js` runs `signtool.exe sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 /sha1 <thumbprint> <file>` for each binary. The token blinks / prompts for your PIN, then signs.

> If `signtool.exe` isn't on your `PATH`, set `$env:SIGNTOOL_PATH` to its full location (e.g. `C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe`) or install the Windows SDK.

### Verifying a signature

```powershell
signtool verify /pa /v 'Founder Node Setup 0.7.7.exe'
```

You should see `Successfully verified` and your name under the publisher.

---

## 3. After your first signed release

1. Upload the signed `.exe` to GitHub Releases (the `publish` block in `package.json` already targets GitHub).
2. SmartScreen behaviour:
   - **EV cert** → trusted **immediately**, no warnings from day one.
   - **OV cert** → users may still see a soft warning for the first few hundred downloads while SmartScreen builds reputation. This is normal and fades.
3. Users see **"Verified publisher: [Your Name]"** instead of "Windows protected your PC".

---

## 4. Environment variables reference

| Env var | What it is | Required for | Example |
|---|---|---|---|
| `CSC_LINK` | Path to the `.pfx` cert file | Path A (OV) | `C:\certs\founder-node.pfx` |
| `CSC_KEY_PASSWORD` | Password for the `.pfx` | Path A (OV) | (the password Certum gave you) |
| `SIGNTOOL_SHA1` | SHA-1 thumbprint of the cert on the USB token | Path B (EV) | `A1B2C3D4E5F6...` |
| `SIGNTOOL_PATH` | Full path to `signtool.exe` if not on PATH | Both (optional) | `C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe` |

> **Never commit certificate files or passwords to the repo.** Set these env vars in your shell, in a local `.env` that's gitignored, or in your CI secrets — not in `package.json`.

---

## 5. Config reference (already in `apps/founder-node/package.json`)

```json
"win": {
  "target": [{ "target": "nsis", "arch": ["x64"] }],
  "icon": "build/icon.ico",
  "signAndEditExecutable": false,
  "signingHashAlgorithms": ["sha256"],
  "rfc3161TimeStampServer": "http://timestamp.digicert.com",
  "sign": "build/sign.js"
}
```

- `signingHashAlgorithms: ["sha256"]` — modern Windows; SHA-1 is deprecated.
- `rfc3161TimeStampServer` — RFC 3161 timestamping so signatures stay valid after the cert expires.
- `sign: "build/sign.js"` — custom hook that branches on env vars (Path A vs Path B).
- Existing build targets (NSIS x64, Mac DMG, Linux AppImage) are untouched.
