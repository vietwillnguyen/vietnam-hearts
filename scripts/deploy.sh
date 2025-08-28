#!/bin/bash

# Exit on any error
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Load environment variables
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
else
  echo -e "${RED}[ERROR] .env file not found. Please create it and set SUPABASE_SERVICE_ROLE_KEY.${NC}"
  exit 1
fi

if [ -z "$SUPABASE_SERVICE_ROLE_KEY" ]; then
  echo -e "${RED}[ERROR] SUPABASE_SERVICE_ROLE_KEY is not set in .env.${NC}"
  exit 1
fi

# Configuration
PROJECT_ID="vietnam-hearts-automation-367619842919"
REGION="northamerica-northeast1"
TIMEZONE="Asia/Ho_Chi_Minh"

# Base URL for the service
BASE_URL="https://${PROJECT_ID}.northamerica-northeast1.run.app"

# Function to create or update a job
create_or_update_job() {
    local job_name=$1
    local schedule=$2
    local endpoint=$3
    local description=$4
    
    echo -e "${YELLOW}Setting up job: ${job_name}${NC}"
    
    # Check if job exists
    if gcloud scheduler jobs describe "$job_name" --location="$REGION" >/dev/null 2>&1; then
        echo "Job $job_name already exists. Updating..."
        gcloud scheduler jobs update http "$job_name" \
            --schedule="$schedule" \
            --uri="${BASE_URL}${endpoint}" \
            --http-method=POST \
            --headers="Content-Type=application/json,apikey=$SUPABASE_SERVICE_ROLE_KEY" \
            --time-zone="$TIMEZONE" \
            --location="$REGION" \
            --description="$description"
        echo -e "${GREEN}✓ Updated job: ${job_name}${NC}"
    else
        echo "Creating new job: $job_name"
        gcloud scheduler jobs create http "$job_name" \
            --schedule="$schedule" \
            --uri="${BASE_URL}${endpoint}" \
            --http-method=POST \
            --headers="Content-Type=application/json,apikey=$SUPABASE_SERVICE_ROLE_KEY" \
            --time-zone="$TIMEZONE" \
            --location="$REGION" \
            --description="$description"
        echo -e "${GREEN}✓ Created job: ${job_name}${NC}"
    fi
}

# Function to list existing jobs
list_existing_jobs() {
    echo -e "${YELLOW}Existing scheduled jobs:${NC}"
    gcloud scheduler jobs list --location="$REGION" --format="table(name,schedule,state)" || true
}

# Function to delete a job
delete_job() {
    local job_name=$1
    echo -e "${YELLOW}Deleting job: ${job_name}${NC}"
    gcloud scheduler jobs delete "$job_name" --location="$REGION" --quiet || true
    echo -e "${GREEN}✓ Deleted job: ${job_name}${NC}"
}

# Main deployment
echo -e "${GREEN}🚀 Deploying Vietnam Hearts scheduled jobs...${NC}"

# Show existing jobs
list_existing_jobs

# Create/update jobs
create_or_update_job \
    "sync-volunteers" \
    "0 */6 * * *" \
    "/admin/sync-volunteers" \
    "Sync volunteers from Google Sheets every 6 hours"

create_or_update_job \
    "send-weekly-reminders" \
    "0 12 * * 0" \
    "/admin/send-weekly-reminders" \
    "Send weekly reminders every Sunday at 12 PM"

create_or_update_job \
    "rotate-schedule" \
    "0 17 * * 5" \
    "/admin/rotate-schedule" \
    "Rotate schedule every Friday at 5 PM"

echo -e "${GREEN}✅ All scheduled jobs deployed successfully!${NC}"

# Show final status
echo -e "${YELLOW}Final job status:${NC}"
list_existing_jobs