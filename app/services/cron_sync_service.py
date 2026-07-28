"""
Push cron schedules from database settings into Cloud Scheduler.

The CRON_* settings were previously write-only decoration: stored in the
settings table, rendered on the admin dashboard, and read by nothing. The
actual schedules lived only in scripts/create-or-update-scheduler-jobs.sh,
so editing the dashboard field changed nothing while appearing to work.

This module makes the settings authoritative for *cadence only*. Job
existence and the apikey header stay owned by the deploy script: the app
deliberately cannot rewrite the credential it authenticates itself with,
and a job this app has never heard of should not be silently created by a
settings save.
"""

import re

from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from app.config import CLOUD_SCHEDULER_LOCATION, GCP_PROJECT_ID
from app.services.settings_service import get_setting
from app.utils.config_helper import ConfigHelper
from app.utils.google_credentials import default_credentials, get_scoped_credentials
from app.utils.logging_config import get_logger

logger = get_logger("cron_sync_service")

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# Settings key -> Cloud Scheduler job id. The job ids match those created by
# scripts/create-or-update-scheduler-jobs.sh; a settings key with no job here
# is simply not syncable.
CRON_SETTING_TO_JOB = {
    "CRON_SYNC_VOLUNTEERS": "sync-volunteers",
    "CRON_SEND_WEEKLY_REMINDERS": "send-weekly-reminders",
    "CRON_ROTATE_SCHEDULE": "rotate-schedule",
}

# Each field may contain digits, wildcards, steps, ranges, lists, and the
# three-letter month/day names Cloud Scheduler accepts. Anything else - most
# importantly whitespace-or-punctuation-separated extra tokens - is refused
# before it reaches the API.
_CRON_FIELD = re.compile(r"^[0-9A-Za-z*/,\-]+$")
_CRON_FIELD_COUNT = 5


def is_valid_cron(expression: object) -> bool:
    """Cheap structural check for a 5-field cron expression.

    Cloud Scheduler is the real validator, but rejecting obvious garbage here
    keeps a typo in the dashboard from being reported as a job-level API
    failure with an opaque message.
    """
    if not isinstance(expression, str):
        return False
    fields = expression.split()
    if len(fields) != _CRON_FIELD_COUNT:
        return False
    return all(_CRON_FIELD.match(field) for field in fields)


def resolve_project_id() -> str:
    """The project the Cloud Scheduler jobs live in.

    GCP_PROJECT_ID wins when set, so a non-default project stays configurable.
    Otherwise the project attached to Application Default Credentials is used,
    which on Cloud Run is the project the service is deployed to - the same
    project the jobs are created in - so the endpoint works without anyone
    having to hand-edit env vars on the service after a deploy.

    Raises:
        ValueError: If neither source yields a project, since every job path
            would otherwise be malformed and every job would "fail" for the
            same uninformative reason.
    """
    if GCP_PROJECT_ID:
        return GCP_PROJECT_ID

    try:
        _, adc_project = default_credentials()
    except Exception as e:
        raise ValueError(
            "GCP_PROJECT_ID is not set and Application Default Credentials could "
            f"not be resolved to fall back on: {e}"
        ) from e

    if not adc_project:
        raise ValueError(
            "GCP_PROJECT_ID is not set and Application Default Credentials report "
            "no project; cannot address Cloud Scheduler jobs"
        )

    logger.info(f"GCP_PROJECT_ID unset; using ADC project {adc_project!r}")
    return adc_project


def _scheduler_client():
    return build(
        "cloudscheduler",
        "v1",
        credentials=get_scoped_credentials([CLOUD_PLATFORM_SCOPE]),
        cache_discovery=False,
    )


def sync_cron_schedules(db: Session, client=None) -> dict:
    """
    Reconcile every Cloud Scheduler job's schedule with its settings value.

    Args:
        db: Database session
        client: Optional pre-built Cloud Scheduler client (injected in tests)

    Returns:
        Dict with `synced` (jobs whose schedule was changed), `unchanged`,
        and `failed` entries. One job failing never prevents the others from
        being reconciled.

    Raises:
        ValueError: If the project cannot be resolved (see resolve_project_id).
    """
    project_id = resolve_project_id()

    client = client or _scheduler_client()
    jobs_api = client.projects().locations().jobs()
    timezone = ConfigHelper.get_schedule_timezone(db)
    parent = f"projects/{project_id}/locations/{CLOUD_SCHEDULER_LOCATION}/jobs"

    result: dict[str, list] = {"synced": [], "unchanged": [], "failed": []}

    for setting_key, job_id in CRON_SETTING_TO_JOB.items():
        desired = get_setting(db, setting_key)
        if not desired:
            logger.info(f"No {setting_key} setting; leaving {job_id} as deployed")
            result["unchanged"].append(job_id)
            continue

        if not is_valid_cron(desired):
            result["failed"].append(
                {
                    "job": job_id,
                    "error": f"{setting_key} is not a valid 5-field cron expression: {desired!r}",
                }
            )
            logger.warning(f"Refusing to apply invalid {setting_key}: {desired!r}")
            continue

        try:
            name = f"{parent}/{job_id}"
            current = jobs_api.get(name=name).execute()

            if (
                current.get("schedule") == desired
                and current.get("timeZone") == timezone
            ):
                result["unchanged"].append(job_id)
                continue

            # Only the cadence is written. Notably absent: httpTarget, so the
            # apikey header is never rewritten from inside the app.
            jobs_api.patch(
                name=name,
                updateMask="schedule,timeZone",
                body={"schedule": desired, "timeZone": timezone},
            ).execute()

            result["synced"].append(
                {
                    "job": job_id,
                    "schedule": desired,
                    "timezone": timezone,
                    "previous_schedule": current.get("schedule"),
                }
            )
            logger.info(
                f"Synced {job_id}: {current.get('schedule')!r} -> {desired!r} ({timezone})"
            )
        except Exception as e:
            result["failed"].append({"job": job_id, "error": str(e)})
            logger.error(f"Failed to sync {job_id}: {e}", exc_info=True)

    return result
