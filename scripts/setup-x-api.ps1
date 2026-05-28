# X API setup for @Bitbro4crypto — read founder tweets + auto-repost + trending posts
# Run: npm run setup:x-api

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== @Bitbro4crypto X automation setup ===" -ForegroundColor Cyan
Write-Host "Profile: https://x.com/Bitbro4crypto (Crypto Gems / Whale tracker)" -ForegroundColor DarkGray
Write-Host ""

Write-Host "WHAT THIS ENABLES" -ForegroundColor Yellow
Write-Host "  1. Read tweets from listed doxxed founders (TWITTER_BEARER_TOKEN)"
Write-Host "  2. Repost/quote founder updates on @Bitbro4crypto"
Write-Host "  3. Post hot paper buys when many traders buy the same project"
Write-Host "  4. Post 50%+ paper trader wins with thesis + founder video link"
Write-Host "  5. In-app notifications for trending buys and big wins"
Write-Host ""

Write-Host "STEP 1 — Developer app (same X account as @Bitbro4crypto)" -ForegroundColor Yellow
Write-Host "  https://developer.x.com/en/portal/dashboard"
Write-Host "  Create Project + App → enable Read AND Write permissions"
Write-Host ""

Write-Host "STEP 2 — READ credentials (fetch founder tweets)" -ForegroundColor Yellow
Write-Host "  App → Keys and tokens → Bearer Token"
Write-Host "  Railway: TWITTER_BEARER_TOKEN=..."
Write-Host ""

Write-Host "STEP 3 — WRITE credentials (post as @Bitbro4crypto)" -ForegroundColor Yellow
Write-Host "  App → Keys and tokens → generate:"
Write-Host "    API Key + API Secret (Consumer keys)"
Write-Host "    Access Token + Access Token Secret (must be for @Bitbro4crypto account)"
Write-Host ""
Write-Host "  Railway env vars:" -ForegroundColor Green
Write-Host "    TWITTER_API_KEY="
Write-Host "    TWITTER_API_SECRET="
Write-Host "    TWITTER_ACCESS_TOKEN="
Write-Host "    TWITTER_ACCESS_TOKEN_SECRET="
Write-Host "    X_BRAND_HANDLE=Bitbro4crypto"
Write-Host "    PUBLIC_SITE_URL=https://doxxedcrypto.digital"
Write-Host ""

Write-Host "STEP 4 — Optional tuning" -ForegroundColor Yellow
Write-Host "  TRENDING_BUY_MIN_TRADERS=5     # unique buyers in 24h to trigger hot buy post"
Write-Host "  TRENDING_BUY_WINDOW_HOURS=24"
Write-Host "  TRADER_WIN_MIN_PNL_PERCENT=50  # min position gain to share portfolio"
Write-Host "  ADMIN_SYNC_JWT=                # admin token for daily cron"
Write-Host ""

Write-Host "STEP 5 — Daily cron (once per 24h, ~`$15-45/mo read + small write cost)" -ForegroundColor Yellow
Write-Host "  npm run sync:x-social-daily"
Write-Host "  Cron: 0 9 * * *  (9:00 UTC daily)"
Write-Host ""

Write-Host "Pricing: https://docs.x.com/x-api/getting-started/pricing" -ForegroundColor DarkGray
Write-Host "  Reads ~ `$0.005/tweet · Writes ~ `$0.015/tweet" -ForegroundColor DarkGray
Write-Host ""

Write-Host "SECURITY: Never paste tokens in chat or commit to GitHub." -ForegroundColor Red
Write-Host ""

$open = Read-Host "Open X Developer Console? (y/n)"
if ($open -eq "y") {
  Start-Process "https://developer.x.com/en/portal/dashboard"
}

Write-Host "When Railway env vars are set, redeploy API and run: npm run sync:x-social-daily" -ForegroundColor Green
