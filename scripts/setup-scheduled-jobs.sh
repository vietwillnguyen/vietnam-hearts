#!/bin/bash

# Load Supabase service role key from .env
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
else
  echo "[ERROR] .env file not found. Please create it and set SUPABASE_SERVICE_ROLE_KEY."
  exit 1
fi

if [ -z "$SUPABASE_SERVICE_ROLE_KEY" ]; then
  echo "[ERROR] SUPABASE_SERVICE_ROLE_KEY is not set in .env."
  exit 1
fi

# Sync volunteers every 6 hours
gcloud scheduler jobs create http sync-volunteers \
  --schedule="0 */6 * * *" \
  --uri="https://vietnam-hearts-automation-367619842919.northamerica-northeast1.run.app/admin/sync-volunteers" \
  --http-method=POST \
  --headers="Content-Type=application/json,apikey=$SUPABASE_SERVICE_ROLE_KEY" \
  --time-zone="Asia/Ho_Chi_Minh" \
  --location="asia-southeast1"

# Send weekly reminders every Sunday at 12 PM
gcloud scheduler jobs create http send-weekly-reminders \
  --schedule="0 12 * * 0" \
  --uri="https://vietnam-hearts-automation-367619842919.northamerica-northeast1.run.app/admin/send-weekly-reminders" \
  --http-method=POST \
  --headers="Content-Type=application/json,apikey=$SUPABASE_SERVICE_ROLE_KEY" \
  --time-zone="Asia/Ho_Chi_Minh" \
  --location="asia-southeast1"

# Rotate schedule every Friday at 5PM
gcloud scheduler jobs create http rotate-schedule \
  --schedule="0 17 * * 5" \
  --uri="https://vietnam-hearts-automation-367619842919.northamerica-northeast1.run.app/admin/rotate-schedule" \
  --http-method=POST \
  --headers="Content-Type=application/json,apikey=$SUPABASE_SERVICE_ROLE_KEY" \
  --time-zone="Asia/Ho_Chi_Minh" \
  --location="asia-southeast1"