"""Tests for pushing cron schedules from settings into Cloud Scheduler.

Before this existed, CRON_SYNC_VOLUNTEERS / CRON_SEND_WEEKLY_REMINDERS /
CRON_ROTATE_SCHEDULE were stored in the settings table and rendered on the
admin dashboard under the label "Changes take effect on the next Cloud
Scheduler sync" - but nothing ever read them. The real schedules lived only
in scripts/create-or-update-scheduler-jobs.sh, so an admin could edit the
dashboard field, see it save, and have nothing whatsoever change.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.cron_sync_service import (
    CRON_SETTING_TO_JOB,
    is_valid_cron,
    sync_cron_schedules,
)


def make_client(current_schedules, patch_side_effect=None):
    """Fake Cloud Scheduler client backed by a {job_id: schedule} dict."""
    jobs = MagicMock()

    def _get(name):
        job_id = name.rsplit("/", 1)[-1]
        if job_id not in current_schedules:
            raise Exception(f"job {job_id} not found")
        result = MagicMock()
        result.execute.return_value = {
            "name": name,
            "schedule": current_schedules[job_id],
            "timeZone": "Asia/Ho_Chi_Minh",
        }
        return result

    def _patch(name, updateMask, body):  # noqa: N803 - mirrors the Google API kwarg
        result = MagicMock()
        if patch_side_effect:
            result.execute.side_effect = patch_side_effect
        else:
            result.execute.return_value = {"name": name, **body}
        return result

    jobs.get.side_effect = _get
    jobs.patch.side_effect = _patch

    client = MagicMock()
    client.projects.return_value.locations.return_value.jobs.return_value = jobs
    return client, jobs


@pytest.fixture
def settings_db():
    """A db whose get_setting returns values from a plain dict."""
    values = {
        "CRON_SYNC_VOLUNTEERS": "0 */2 * * *",
        "CRON_SEND_WEEKLY_REMINDERS": "0 12 * * 0",
        "CRON_ROTATE_SCHEDULE": "0 * * * *",
        "SCHEDULE_TIMEZONE": "Asia/Ho_Chi_Minh",
    }

    def fake_get_setting(db, key, default=None):
        return values.get(key, default)

    with (
        patch("app.services.cron_sync_service.get_setting", fake_get_setting),
        patch(
            "app.utils.config_helper.get_setting",
            fake_get_setting,
        ),
        patch("app.services.cron_sync_service.GCP_PROJECT_ID", "test-project"),
        patch(
            "app.services.cron_sync_service.CLOUD_SCHEDULER_LOCATION", "asia-southeast1"
        ),
    ):
        yield MagicMock(), values


class TestIsValidCron:
    def test_accepts_standard_five_field_expressions(self):
        assert is_valid_cron("0 * * * *")
        assert is_valid_cron("0 */2 * * *")
        assert is_valid_cron("30 17 * * 1-5")

    def test_rejects_wrong_field_count(self):
        # Six fields is the seconds-resolution form; Cloud Scheduler takes five.
        assert not is_valid_cron("0 0 * * * *")
        assert not is_valid_cron("0 * * *")

    def test_rejects_empty_and_non_string(self):
        assert not is_valid_cron("")
        assert not is_valid_cron(None)
        assert not is_valid_cron(MagicMock())

    def test_rejects_shell_injection_shaped_input(self):
        assert not is_valid_cron("0 * * * *; rm -rf /")


class TestSyncCronSchedules:
    def test_patches_job_whose_schedule_drifted(self, settings_db):
        db, _ = settings_db
        client, jobs = make_client(
            {
                "sync-volunteers": "0 */2 * * *",
                "send-weekly-reminders": "0 12 * * 0",
                "rotate-schedule": "0 17 * * 5",  # stale weekly value
            }
        )

        result = sync_cron_schedules(db, client=client)

        assert [j["job"] for j in result["synced"]] == ["rotate-schedule"]
        assert jobs.patch.call_count == 1
        kwargs = jobs.patch.call_args.kwargs
        assert kwargs["body"]["schedule"] == "0 * * * *"
        assert kwargs["updateMask"] == "schedule,timeZone"
        assert kwargs["name"].endswith(
            "projects/test-project/locations/asia-southeast1/jobs/rotate-schedule"
        )

    def test_leaves_matching_jobs_untouched(self, settings_db):
        db, _ = settings_db
        client, jobs = make_client(
            {
                "sync-volunteers": "0 */2 * * *",
                "send-weekly-reminders": "0 12 * * 0",
                "rotate-schedule": "0 * * * *",
            }
        )

        result = sync_cron_schedules(db, client=client)

        assert sorted(result["unchanged"]) == sorted(CRON_SETTING_TO_JOB.values())
        jobs.patch.assert_not_called()

    def test_applies_the_configured_timezone(self, settings_db):
        db, _ = settings_db
        client, jobs = make_client(
            {**{v: "0 0 1 1 *" for v in CRON_SETTING_TO_JOB.values()}}
        )

        sync_cron_schedules(db, client=client)

        for call in jobs.patch.call_args_list:
            assert call.kwargs["body"]["timeZone"] == "Asia/Ho_Chi_Minh"

    def test_invalid_cron_is_rejected_without_calling_the_api(self, settings_db):
        db, values = settings_db
        values["CRON_ROTATE_SCHEDULE"] = "not a cron"
        client, jobs = make_client(
            {
                "sync-volunteers": "0 */2 * * *",
                "send-weekly-reminders": "0 12 * * 0",
                "rotate-schedule": "0 17 * * 5",
            }
        )

        result = sync_cron_schedules(db, client=client)

        assert [f["job"] for f in result["failed"]] == ["rotate-schedule"]
        assert "not a cron" in result["failed"][0]["error"]
        jobs.patch.assert_not_called()

    def test_one_failing_job_does_not_stop_the_others(self, settings_db):
        # Same lesson as the 2026-07-03 rotation incident: never let one
        # bad item abort the whole reconciliation.
        db, _ = settings_db
        client, jobs = make_client(
            {
                "sync-volunteers": "0 0 1 1 *",
                "send-weekly-reminders": "0 0 1 1 *",
                # rotate-schedule absent entirely -> get() raises
            }
        )

        result = sync_cron_schedules(db, client=client)

        assert [f["job"] for f in result["failed"]] == ["rotate-schedule"]
        assert sorted(j["job"] for j in result["synced"]) == [
            "send-weekly-reminders",
            "sync-volunteers",
        ]

    def test_missing_project_id_fails_loudly(self, settings_db):
        db, _ = settings_db
        client, _ = make_client({})

        with patch("app.services.cron_sync_service.GCP_PROJECT_ID", ""):
            with pytest.raises(ValueError, match="GCP_PROJECT_ID"):
                sync_cron_schedules(db, client=client)

    def test_never_touches_job_headers(self, settings_db):
        # The apikey header is deliberately out of scope: the app must not be
        # able to rewrite its own admin credential, and the drift that broke
        # rotation is fixed by the deploy script, not from inside the app.
        db, _ = settings_db
        client, jobs = make_client(
            {v: "0 0 1 1 *" for v in CRON_SETTING_TO_JOB.values()}
        )

        sync_cron_schedules(db, client=client)

        for call in jobs.patch.call_args_list:
            assert "httpTarget" not in call.kwargs["body"]
            assert "headers" not in call.kwargs["body"]
            assert "headers" not in call.kwargs["updateMask"]
