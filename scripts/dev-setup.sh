#!/usr/bin/env bash
set -euo pipefail

echo "Starting DoxedCryptoFounder development environment..."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

docker compose up -d
echo "Waiting for PostgreSQL..."
sleep 3

npm run db:migrate
npm run db:seed

echo ""
echo "Ready! Run: npm run dev"
echo "  Frontend: http://localhost:3000"
echo "  API:      http://localhost:4000/api/health"
