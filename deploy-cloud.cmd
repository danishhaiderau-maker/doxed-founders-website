@echo off
cd /d "%~dp0"
echo.
echo === Doxed Founders - Cloud deploy helper ===
echo.
echo 1. Neon:     https://console.neon.tech  (copy connection string)
echo 2. Railway:  https://railway.app/dashboard  (paste vars from scripts\deploy-cloud-env.template.txt)
echo 3. Vercel:   https://vercel.com/new  (import GitHub repo, root: apps/web)
echo 4. Seed DB:  set DATABASE_URL=your_neon_url ^& npm run db:seed
echo.
echo Env template: scripts\deploy-cloud-env.template.txt
echo.
start https://console.neon.tech
start https://railway.app/dashboard
start https://vercel.com/new
pause
