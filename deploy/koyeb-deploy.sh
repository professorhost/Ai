#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./deploy/koyeb-deploy.sh APP_NAME SERVICE_NAME
#
# Authentication:
#   koyeb login
#
# Secrets/environment variables are intentionally not embedded here.
# Configure them in Koyeb or pass them with --env/Secrets.

APP_NAME="${1:-image-ai-bot}"
SERVICE_NAME="${2:-image-ai-bot}"

command -v koyeb >/dev/null 2>&1 || {
  echo "Koyeb CLI is required: https://www.koyeb.com/docs/build-and-deploy/cli" >&2
  exit 1
}

koyeb deploy . "${APP_NAME}/${SERVICE_NAME}" \
  --archive-builder docker \
  --ports 8000:http \
  --routes /:8000 \
  --checks 8000:http:/api/healthz \
  --checks-grace-period 8000=30
