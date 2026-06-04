# Run the Bybit research bot from THIS folder only (no cross-contamination with C:\Users\user\15minu_bot.py)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env — add BYBIT_API_KEY and BYBIT_SECRET, then run again."
    exit 1
}

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& .\.venv\Scripts\Activate.ps1
pip install -q -r requirements.txt
python bot.py
