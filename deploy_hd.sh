#!/bin/bash
set -e

# Load credentials from .env
set -a
source .env
set +a

# Fetch app login password from Secret Manager
APP_PASSWORD=$(gcloud secrets versions access latest --secret=APP_PASSWORD --project seo-optimizer-499718)

# Write a temp env-vars file — avoids issues with special characters in --set-env-vars
TMPFILE="/tmp/cloudrun-env-$$.yaml"
cat > "$TMPFILE" <<EOF
APP_USERNAME: "admin"
APP_PASSWORD: "${APP_PASSWORD}"
EOF

echo "Deploying to Cloud Run — Heron Dance project (this takes a few minutes)..."
gcloud run deploy seo-app \
  --source . \
  --region us-east1 \
  --allow-unauthenticated \
  --max-instances 2 \
  --concurrency 10 \
  --timeout 600 \
  --memory 1Gi \
  --project seo-optimizer-499718 \
  --env-vars-file "$TMPFILE" \
  --set-secrets "ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,OPENAI_API_KEY=OPENAI_API_KEY:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest,DATAFORSEO_LOGIN=DATAFORSEO_LOGIN:latest,DATAFORSEO_PASSWORD=DATAFORSEO_PASSWORD:latest,SHEETS_SERVICE_ACCOUNT_KEY=SHEETS_SERVICE_ACCOUNT_KEY:latest"

rm -f "$TMPFILE"
echo ""
echo "Done. Open the Service URL above, log in with: admin / ${APP_PASSWORD}"
