#!/bin/bash
set -e

GCLOUD_CONFIG="herondance"
GCLOUD_ACCOUNT="tech@herondance.org"
GCLOUD_PROJECT="seo-optimizer-499718"
GCLOUD_REGION="us-east1"

if ! gcloud config configurations describe "$GCLOUD_CONFIG" >/dev/null 2>&1; then
  gcloud config configurations create "$GCLOUD_CONFIG"
fi

gcloud config configurations activate "$GCLOUD_CONFIG"
gcloud config set account "$GCLOUD_ACCOUNT"
gcloud config set project "$GCLOUD_PROJECT"
gcloud config set run/region "$GCLOUD_REGION"

if ! gcloud auth list --filter="account:$GCLOUD_ACCOUNT" --format="value(account)" | grep -qx "$GCLOUD_ACCOUNT"; then
  echo "ERROR: $GCLOUD_ACCOUNT is not authenticated in gcloud."
  echo "Run: gcloud auth login $GCLOUD_ACCOUNT"
  exit 1
fi

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
echo "Done. Open the Service URL above and log in with the APP_PASSWORD stored in Secret Manager."

# ── Cloud Run Job (seo-batch-job) ────────────────────────────────────────────
# Reuse the same container image that was just built and deployed to the service.
echo ""
echo "Fetching image URI from deployed service..."
IMAGE=$(gcloud run services describe seo-app \
  --region us-east1 \
  --project seo-optimizer-499718 \
  --format 'value(spec.template.spec.containers[0].image)')
echo "Image: $IMAGE"

JOB_SECRETS="ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,OPENAI_API_KEY=OPENAI_API_KEY:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest,DATAFORSEO_LOGIN=DATAFORSEO_LOGIN:latest,DATAFORSEO_PASSWORD=DATAFORSEO_PASSWORD:latest,SHEETS_SERVICE_ACCOUNT_KEY=SHEETS_SERVICE_ACCOUNT_KEY:latest"

# Grant Secret Manager access to the job's service account (idempotent).
echo "Ensuring Secret Manager access for seo-app-sheets service account..."
gcloud projects add-iam-policy-binding seo-optimizer-499718 \
  --member="serviceAccount:seo-app-sheets@seo-optimizer-499718.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

echo "Deploying Cloud Run Job seo-batch-job..."
if gcloud run jobs describe seo-batch-job \
     --region us-east1 --project seo-optimizer-499718 \
     --format="value(name)" 2>/dev/null | grep -q seo-batch-job; then
  gcloud run jobs update seo-batch-job \
    --image "$IMAGE" \
    --command python \
    --args "batch_runner.py" \
    --region us-east1 \
    --project seo-optimizer-499718 \
    --service-account "seo-app-sheets@seo-optimizer-499718.iam.gserviceaccount.com" \
    --set-secrets "$JOB_SECRETS" \
    --task-timeout 3600 \
    --max-retries 0 \
    --memory 1Gi
else
  gcloud run jobs create seo-batch-job \
    --image "$IMAGE" \
    --command python \
    --args "batch_runner.py" \
    --region us-east1 \
    --project seo-optimizer-499718 \
    --service-account "seo-app-sheets@seo-optimizer-499718.iam.gserviceaccount.com" \
    --set-secrets "$JOB_SECRETS" \
    --task-timeout 3600 \
    --max-retries 0 \
    --memory 1Gi
fi

# Grant the service account permission to trigger jobs (idempotent).
# roles/run.developer is required for run.jobs.run — roles/run.invoker only covers HTTP services.
echo "Granting run.developer on project to the service account..."
gcloud projects add-iam-policy-binding seo-optimizer-499718 \
  --member="serviceAccount:seo-app-sheets@seo-optimizer-499718.iam.gserviceaccount.com" \
  --role="roles/run.developer"

echo ""
echo "Cloud Run Job deployed. Batch runs can now be triggered from the Gradio UI (Section D)."
