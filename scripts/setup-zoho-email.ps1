# Zoho Mail setup for doxxedcrypto.digital
# Domain DNS: Name.com nameservers (Namecheap / Name.com registrar)
#
# Run: powershell -ExecutionPolicy Bypass -File scripts/setup-zoho-email.ps1
# Optional: -OpenBrowser  opens signup + DNS pages

param([switch]$OpenBrowser, [switch]$CheckDns)

$Domain = 'doxxedcrypto.digital'
$PrimaryMailbox = 'hello'
$Email = "$PrimaryMailbox@$Domain"

Write-Host "`n=== Zoho Mail setup: $Domain ===" -ForegroundColor Cyan
Write-Host "Recommended project email: $Email" -ForegroundColor Green
Write-Host "Also create: admin@, support@ (optional)`n" -ForegroundColor DarkGray

Write-Host "STEP 1 — Create Zoho Mail (free plan)" -ForegroundColor Yellow
Write-Host "  1. Go to https://www.zoho.com/mail/zohomail-pricing.html"
Write-Host "  2. Choose Mail Free (1 domain, up to 5 users, 5GB each)"
Write-Host "  3. Sign up with YOUR personal Gmail (recovery only)"
Write-Host "  4. Organization: DoxedCryptoFounder"
Write-Host "  5. Add domain: $Domain → 'I already own this domain'`n"

Write-Host "STEP 2 — Verify domain in Zoho" -ForegroundColor Yellow
Write-Host "  Zoho shows a TXT record like:"
Write-Host "    Host: @   Value: zoho-verification=zbxxxx.zmverify.zoho.com"
Write-Host "  Add it in your domain DNS panel (Namecheap → Domain List → Manage → Advanced DNS)`n"

Write-Host "STEP 3 — Add MX records (receive mail)" -ForegroundColor Yellow
Write-Host "  In Advanced DNS → Mail Settings → Custom MX, add:"
Write-Host "    @  MX  10  mx.zoho.com"
Write-Host "    @  MX  20  mx2.zoho.com"
Write-Host "    @  MX  50  mx3.zoho.com`n"

Write-Host "STEP 4 — Add SPF (send mail / avoid spam)" -ForegroundColor Yellow
Write-Host "  TXT record:"
Write-Host "    Host: @"
Write-Host "    Value: v=spf1 include:zohomail.com ~all`n"

Write-Host "STEP 5 — Add DKIM (Zoho Admin → Email Configuration → DKIM)" -ForegroundColor Yellow
Write-Host "  Zoho generates a TXT record like:"
Write-Host "    Host: zmail._domainkey"
Write-Host "    Value: v=DKIM1; k=rsa; p=...`n"

Write-Host "STEP 6 — Create mailbox in Zoho Admin" -ForegroundColor Yellow
Write-Host "  Users → Add User → $Email"
Write-Host "  Set a strong password (save in password manager)`n"

Write-Host "STEP 7 — Sign up X account" -ForegroundColor Yellow
Write-Host "  Use $Email on https://x.com/i/flow/signup"
Write-Host "  Enable 2FA on BOTH Zoho email and X`n"

Write-Host "STEP 8 — Verify DNS (run this script with -CheckDns after 1–4 hours)" -ForegroundColor Yellow

if ($OpenBrowser) {
  Start-Process 'https://www.zoho.com/mail/zohomail-pricing.html'
  Start-Process 'https://www.namecheap.com/myaccount/login.aspx'
  Start-Process 'https://mailadmin.zoho.com/cpanel/home.do'
}

if ($CheckDns) {
  Write-Host "`n--- DNS check ---" -ForegroundColor Cyan
  Write-Host "MX records:"
  nslookup -type=MX $Domain 2>&1
  Write-Host "`nTXT (verification / SPF):"
  nslookup -type=TXT $Domain 2>&1
  Write-Host "`nExpected MX: mx.zoho.com, mx2.zoho.com, mx3.zoho.com"
}

Write-Host "`nSaved email target: $Email" -ForegroundColor Green
Write-Host "I cannot create Zoho for you (needs your signup + phone). Follow steps above (~20 min).`n"
