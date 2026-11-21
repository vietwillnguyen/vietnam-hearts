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


def run_scheduler_script(tmp_path, *, jobs_exist: bool) -> dict[str, dict[str, str]]:
    """Run the deploy script against a stub ``gcloud`` and return what it asked for.

    Keyed by job id, each value is the ``--flag=value`` arguments of the
    create or update call the script made for that job.
    """
    import shutil
    import subprocess

    from app.config import PROJECT_ROOT

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(
        PROJECT_ROOT / "scripts" / "create-or-update-scheduler-jobs.sh", scripts
    )
    (scripts / "deploy.config").write_text(
        'SCHEDULER_REGION="test-region"\n'
        'SCHEDULER_TIMEZONE="Asia/Ho_Chi_Minh"\n'
        'BASE_URL="https://service.example.test"\n'
    )
    (tmp_path / ".env").write_text("SUPABASE_SECRET_KEY=test-key\n")

    log = tmp_path / "gcloud.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gcloud = bin_dir / "gcloud"
    gcloud.write_text(
        "#!/bin/bash\n"
        'if [ "$3" = describe ]; then exit "$GCLOUD_DESCRIBE_EXIT"; fi\n'
        'printf "%s\\x1f" "$@" >> "$GCLOUD_LOG"\n'
        'printf "\\n" >> "$GCLOUD_LOG"\n'
    )
    gcloud.chmod(0o755)

    subprocess.run(
        ["bash", str(scripts / "create-or-update-scheduler-jobs.sh")],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "GCLOUD_LOG": str(log),
            "GCLOUD_DESCRIBE_EXIT": "0" if jobs_exist else "1",
        },
        check=True,
        capture_output=True,
    )

    verb = "update" if jobs_exist else "create"
    calls: dict[str, dict[str, str]] = {}
    for line in log.read_text().splitlines():
        args = [arg for arg in line.split("\x1f") if arg]
        if args[:4] != ["scheduler", "jobs", verb, "http"]:
            continue
        calls[args[4]] = dict(
            arg[2:].split("=", 1) for arg in args[5:] if arg.startswith("--")
        )
    return calls


@pytest.fixture
def settings_db():
    """A db whose get_setting returns values from a plain dict."""
    values = {
        "CRON_SYNC_VOLUNTEERS": "0 */2 * * *",
        "CRON_SEND_WEEKLY_REMINDERS": "0 12 * * 0",
        "CRON_ROTATE_SCHEDULE": "0 * * * *",
        "CRON_POLL_INBOX": "0 8,18 * * *",
        "CRON_SYNC_KNOWLEDGE_BASE": "0 5 * * *",
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
                "poll-volunteer-inbox": "0 8,18 * * *",
                "sync-knowledge-base": "0 5 * * *",
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
                "poll-volunteer-inbox": "0 8,18 * * *",
                "sync-knowledge-base": "0 5 * * *",
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
                "poll-volunteer-inbox": "0 8,18 * * *",
                "sync-knowledge-base": "0 5 * * *",
            }
        )

        result = sync_cron_schedules(db, client=client)

        assert [f["job"] for f in result["failed"]] == ["rotate-schedule"]
        assert "not a cron" in result["failed"][0]["error"]
        jobs.patch.assert_not_called()

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_blank_setting_fails_instead_of_reporting_unchanged(
        self, settings_db, blank
    ):
        """A wiped cadence must never read as a clean sync.

        Counting a missing CRON_* value as `unchanged` answered 200 "already
        current" while the real job kept its stale schedule - the dishonest
        success this whole change exists to remove. The job genuinely is
        unreconciled, so it is a failure and the endpoint must say 502.
        """
        db, values = settings_db
        if blank is None:
            del values["CRON_ROTATE_SCHEDULE"]
        else:
            values["CRON_ROTATE_SCHEDULE"] = blank
        client, jobs = make_client(
            {
                "sync-volunteers": "0 */2 * * *",
                "send-weekly-reminders": "0 12 * * 0",
                "rotate-schedule": "0 17 * * 5",  # stale, and about to stay stale
                "poll-volunteer-inbox": "0 8,18 * * *",
                "sync-knowledge-base": "0 5 * * *",
            }
        )

        result = sync_cron_schedules(db, client=client)

        assert [f["job"] for f in result["failed"]] == ["rotate-schedule"]
        assert "CRON_ROTATE_SCHEDULE" in result["failed"][0]["error"]
        assert "rotate-schedule" not in result["unchanged"]
        jobs.patch.assert_not_called()

    def test_blank_setting_does_not_stop_the_other_jobs(self, settings_db):
        """Per-item tolerance still applies to the empty case."""
        db, values = settings_db
        values["CRON_ROTATE_SCHEDULE"] = ""
        client, jobs = make_client(
            {
                "sync-volunteers": "0 0 1 1 *",
                "send-weekly-reminders": "0 0 1 1 *",
                "rotate-schedule": "0 17 * * 5",
                "poll-volunteer-inbox": "0 8,18 * * *",
                "sync-knowledge-base": "0 5 * * *",
            }
        )

        result = sync_cron_schedules(db, client=client)

        assert sorted(j["job"] for j in result["synced"]) == [
            "send-weekly-reminders",
            "sync-volunteers",
        ]
        assert [f["job"] for f in result["failed"]] == ["rotate-schedule"]

    def test_one_failing_job_does_not_stop_the_others(self, settings_db):
        # Same lesson as the 2026-07-03 rotation incident: never let one
        # bad item abort the whole reconciliation.
        db, _ = settings_db
        client, jobs = make_client(
            {
                "sync-volunteers": "0 0 1 1 *",
                "send-weekly-reminders": "0 0 1 1 *",
                "poll-volunteer-inbox": "0 8,18 * * *",
                "sync-knowledge-base": "0 5 * * *",
                # rotate-schedule absent entirely -> get() raises
            }
        )

        result = sync_cron_schedules(db, client=client)

        assert [f["job"] for f in result["failed"]] == ["rotate-schedule"]
        assert sorted(j["job"] for j in result["synced"]) == [
            "send-weekly-reminders",
            "sync-volunteers",
        ]

    def test_unset_project_id_falls_back_to_the_adc_project(self, settings_db):
        # Nothing in the repo sets GCP_PROJECT_ID on the Cloud Run service, and
        # requiring a hand-edited env var to make the endpoint work at all is
        # the same out-of-band configuration that let the jobs rot.
        db, _ = settings_db
        client, jobs = make_client(
            {v: "0 0 1 1 *" for v in CRON_SETTING_TO_JOB.values()}
        )

        with (
            patch("app.services.cron_sync_service.GCP_PROJECT_ID", ""),
            patch(
                "app.services.cron_sync_service.default_credentials",
                return_value=(MagicMock(), "adc-project"),
            ),
        ):
            sync_cron_schedules(db, client=client)

        for call in jobs.patch.call_args_list:
            assert call.kwargs["name"].startswith("projects/adc-project/")

    def test_explicit_project_id_wins_over_adc(self, settings_db):
        db, _ = settings_db
        client, jobs = make_client(
            {v: "0 0 1 1 *" for v in CRON_SETTING_TO_JOB.values()}
        )

        with patch(
            "app.services.cron_sync_service.default_credentials",
            return_value=(MagicMock(), "adc-project"),
        ) as mock_adc:
            sync_cron_schedules(db, client=client)

        mock_adc.assert_not_called()
        for call in jobs.patch.call_args_list:
            assert call.kwargs["name"].startswith("projects/test-project/")

    def test_missing_project_id_fails_loudly(self, settings_db):
        db, _ = settings_db
        client, _ = make_client({})

        with (
            patch("app.services.cron_sync_service.GCP_PROJECT_ID", ""),
            patch(
                "app.services.cron_sync_service.default_credentials",
                return_value=(MagicMock(), None),
            ),
        ):
            with pytest.raises(ValueError, match="GCP_PROJECT_ID"):
                sync_cron_schedules(db, client=client)

    def test_unresolvable_credentials_fail_loudly(self, settings_db):
        db, _ = settings_db
        client, _ = make_client({})

        with (
            patch("app.services.cron_sync_service.GCP_PROJECT_ID", ""),
            patch(
                "app.services.cron_sync_service.default_credentials",
                side_effect=Exception("no metadata server"),
            ),
        ):
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


class TestPollInboxJobMapping:
    """``CRON_POLL_INBOX`` drives the ``poll-volunteer-inbox`` job.

    The mapping is what makes the dashboard field authoritative. Without it the
    field is decoration again: stored, rendered, and read by nothing - which is
    exactly the failure the cron sync service was built to end.
    """

    def test_the_setting_maps_to_the_job(self):
        assert CRON_SETTING_TO_JOB["CRON_POLL_INBOX"] == "poll-volunteer-inbox"

    def test_the_deploy_script_creates_the_job_against_the_poll_endpoint(
        self, tmp_path
    ):
        # The script owns job existence; this module owns cadence. A name that
        # disagrees means the sync silently updates nothing.
        created = run_scheduler_script(tmp_path, jobs_exist=False)
        assert created["poll-volunteer-inbox"]["uri"] == (
            "https://service.example.test/admin/email-bot/poll"
        )

    def test_every_mapped_job_is_created_by_the_deploy_script(self, tmp_path):
        created = run_scheduler_script(tmp_path, jobs_exist=False)
        for job_id in CRON_SETTING_TO_JOB.values():
            assert job_id in created, f"{job_id} is mapped but never created"

    def test_a_drifted_poll_cadence_is_patched(self, settings_db):
        db, _ = settings_db
        client, jobs = make_client(
            {
                "sync-volunteers": "0 */2 * * *",
                "send-weekly-reminders": "0 12 * * 0",
                "rotate-schedule": "0 * * * *",
                "poll-volunteer-inbox": "*/5 * * * *",  # a far too frequent poll
                "sync-knowledge-base": "0 5 * * *",
            }
        )

        result = sync_cron_schedules(db, client=client)

        assert [job["job"] for job in result["synced"]] == ["poll-volunteer-inbox"]
        kwargs = jobs.patch.call_args.kwargs
        assert kwargs["body"]["schedule"] == "0 8,18 * * *"
        assert kwargs["name"].endswith("jobs/poll-volunteer-inbox")

    def test_the_default_cadence_is_accepted_as_a_cron_expression(self):
        # Two runs a day is expressed as a list in the hour field, which the
        # structural validator has to accept.
        assert is_valid_cron("0 8,18 * * *")

    @pytest.mark.parametrize("jobs_exist", [False, True])
    def test_the_attempt_deadline_is_set_on_the_poll_job(self, tmp_path, jobs_exist):
        # It has to exceed the worst case of one run at EMAIL_BOT_PER_RUN_CAP.
        # A deadline that fires mid-run produces exactly the concurrent-retry
        # case the run lock exists for.
        calls = run_scheduler_script(tmp_path, jobs_exist=jobs_exist)
        assert calls["poll-volunteer-inbox"]["attempt-deadline"] == "600s"


class TestKnowledgeBaseSyncJobMapping:
    def test_the_setting_maps_to_the_job(self):
        assert CRON_SETTING_TO_JOB["CRON_SYNC_KNOWLEDGE_BASE"] == "sync-knowledge-base"

    def test_the_job_targets_the_sync_endpoint(self, tmp_path):
        created = run_scheduler_script(tmp_path, jobs_exist=False)
        assert created["sync-knowledge-base"]["uri"] == (
            "https://service.example.test/admin/email-bot/sync-knowledge-base"
        )

    def test_it_runs_before_the_morning_poll(self, test_db):
        # The point of the job: an edit made today is answerable tomorrow
        # morning, which needs the sync to land before the 08:00 poll.
        from app.services.settings_service import (
            get_setting,
            initialize_default_settings,
        )

        initialize_default_settings(test_db)
        sync = get_setting(test_db, "CRON_SYNC_KNOWLEDGE_BASE")
        poll = get_setting(test_db, "CRON_POLL_INBOX")
        sync_hour = int(sync.split()[1])
        first_poll_hour = min(int(hour) for hour in poll.split()[1].split(","))
        assert sync_hour < first_poll_hour

    def test_a_drifted_sync_cadence_is_patched(self, settings_db):
        db, _ = settings_db
        client, jobs = make_client(
            {
                "sync-volunteers": "0 */2 * * *",
                "send-weekly-reminders": "0 12 * * 0",
                "rotate-schedule": "0 * * * *",
                "poll-volunteer-inbox": "0 8,18 * * *",
                "sync-knowledge-base": "0 0 1 1 *",  # yearly, which is useless
            }
        )

        result = sync_cron_schedules(db, client=client)

        assert [job["job"] for job in result["synced"]] == ["sync-knowledge-base"]
        assert jobs.patch.call_args.kwargs["body"]["schedule"] == "0 5 * * *"
