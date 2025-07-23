# Set your Supabase service role key
export SUPABASE_SERVICE_ROLE_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFwdmtjaHV1ZnV0aHJpcmxoZnJzIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc0OTcxMDIwNSwiZXhwIjoyMDY1Mjg2MjA1fQ.676mJsxA2GnJjvWRmWLBvFGz4sS6HcI0jjIZxUP4b-s"

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