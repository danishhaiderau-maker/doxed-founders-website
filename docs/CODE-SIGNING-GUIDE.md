# Code Signing Guide — Founder Node

Windows SmartScreen warns users when they run unsigned .exe files ("Windows protected your PC"). A code signing certificate makes SmartScreen trust your installer: an **EV** certificate is trusted immediately, while an **OV** certificate earns trust after a few downloads. Signed installs also show "Verified publisher: [Your Name]" instead of the scary warning.

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
signtool verify /pa /v 'Founder Node Setup 0.7.3.exe'
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
