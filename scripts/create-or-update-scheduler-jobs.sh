#!/bin/bash

# Exit on any error
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Load deployment configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/deploy.config"

if [ ! -f "$CONFIG_FILE" ]; then
  echo -e "${RED}[ERROR] deploy.config not found at ${CONFIG_FILE}${NC}"
  exit 1
fi
source "$CONFIG_FILE"

# Map config vars to local names
REGION="${SCHEDULER_REGION}"
TIMEZONE="${SCHEDULER_TIMEZONE}"

# Load environment variables (for secrets)
ENV_FILE="${SCRIPT_DIR}/../.env"  # secrets stay at project root
if [ -f "$ENV_FILE" ]; then
  export $(grep -v '^#' "$ENV_FILE" | xargs)
else
  echo -e "${RED}[ERROR] .env file not found at ${ENV_FILE}. Please create it and set SUPABASE_SECRET_KEY.${NC}"
  exit 1
fi

if [ -z "$SUPABASE_SECRET_KEY" ]; then
  echo -e "${RED}[ERROR] SUPABASE_SECRET_KEY is not set in .env.${NC}"
  exit 1
fi

# Jobs that could not be created or updated. Reported at the end so one
# broken job never hides the state of the others.
FAILED_JOBS=()

# Function to create or update a job
#
# `create` takes a whole-value --headers, but `update` only accepts the
# merge-semantics --update-headers/--remove-headers/--clear-headers trio.
# Passing --headers to the update path makes gcloud reject the whole command,
# which is what silently froze the apikey header on rotate-schedule and
# send-weekly-reminders at a pre-migration Supabase key.
#
# The `$schedule` and `$TIMEZONE` arguments are bootstrap defaults and are
# therefore only sent on the CREATE path. Cadence belongs to the CRON_*
# settings, applied by POST /admin/sync-cron-schedules; sending it on UPDATE
# too would mean every credential rotation silently stamped these hardcoded
# values back over whatever an admin had configured - putting the dashboard
# back to lying about cadence, which is the failure this repair exists to end.
create_or_update_job() {
    local job_name=$1
    local schedule=$2
    local endpoint=$3
    local description=$4

    echo -e "${YELLOW}Setting up job: ${job_name}${NC}"

    # Check if job exists
    if gcloud scheduler jobs describe "$job_name" --location="$REGION" >/dev/null 2>&1; then
        echo "Job $job_name already exists. Updating credentials and target (cadence left as configured)..."
        if gcloud scheduler jobs update http "$job_name" \
            --uri="${BASE_URL}${endpoint}" \
            --http-method=POST \
            --update-headers="Content-Type=application/json,apikey=$SUPABASE_SECRET_KEY" \
            --location="$REGION" \
            --description="$description"; then
            echo -e "${GREEN}✓ Updated job: ${job_name}${NC}"
        else
            FAILED_JOBS+=("$job_name")
            echo -e "${RED}✗ Failed to update job: ${job_name}${NC}"
        fi
    else
        echo "Creating new job: $job_name"
        if gcloud scheduler jobs create http "$job_name" \
            --schedule="$schedule" \
            --uri="${BASE_URL}${endpoint}" \
            --http-method=POST \
            --headers="Content-Type=application/json,apikey=$SUPABASE_SECRET_KEY" \
            --time-zone="$TIMEZONE" \
            --location="$REGION" \
            --description="$description"; then
            echo -e "${GREEN}✓ Created job: ${job_name}${NC}"
        else
            FAILED_JOBS+=("$job_name")
            echo -e "${RED}✗ Failed to create job: ${job_name}${NC}"
        fi
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
    "0 */2 * * *" \
    "/admin/review-and-sync" \
    "LLM-judge pending signups, then sync accepted volunteers and send confirmation emails (every 2 hours)"

create_or_update_job \
    "send-weekly-reminders" \
    "0 12 * * 0" \
    "/admin/send-weekly-reminders" \
    "Send weekly reminders every Sunday at 12 PM"

# Reconciling schedule sheets is idempotent - it converges on the same target
# state for "now" no matter how often it runs - so it is scheduled frequently
# rather than once a week. That way a missed run, a manual edit, or a week
# boundary is corrected within the hour instead of waiting until Friday.
# This value is only the bootstrap default; the live cadence is owned by the
# CRON_ROTATE_SCHEDULE setting and applied by POST /admin/sync-cron-schedules.
create_or_update_job \
    "rotate-schedule" \
    "0 * * * *" \
    "/admin/rotate-schedule" \
    "Reconcile schedule sheets to the current week (hourly)"

if [ ${#FAILED_JOBS[@]} -gt 0 ]; then
    echo -e "${RED}❌ ${#FAILED_JOBS[@]} job(s) failed: ${FAILED_JOBS[*]}${NC}"
    echo -e "${YELLOW}Final job status:${NC}"
    list_existing_jobs
    exit 1
fi

echo -e "${GREEN}✅ All scheduled jobs deployed successfully!${NC}"

# Show final status
echo -e "${YELLOW}Final job status:${NC}"
list_existing_jobs