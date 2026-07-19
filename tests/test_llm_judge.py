"""
Tests for LLM-as-judge volunteer submission review feature.

Covers:
- get_pending_submissions_with_rows: fetches only non-ACCEPTED/non-REJECTED rows with row numbers
- update_submission_judgment: writes status/summary/rating back to the sheet
- _judge_submission: calls Gemini and parses structured JSON response
- POST /admin/judge-pending-submissions: end-to-end endpoint with dry_run support
"""

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_auth_service():
    """Mock admin auth for integration tests."""
    from app.dependencies.auth import get_current_admin_user
    from app.main import app

    async def _mock_admin():
        return {"email": "admin@test.org", "is_admin": True, "is_authenticated": True}

    app.dependency_overrides[get_current_admin_user] = _mock_admin
    yield _mock_admin
    app.dependency_overrides.clear()


SAMPLE_PENDING_ROW = {
    "applicant_status": "PENDING",
    "timestamp": "01/01/2025 10:00:00",
    "llm_judge_score": "",
    "email_address": "alice@example.com",
    "quiz_score": "",
    "first_name": "Alice",
    "last_name": "Smith",
    "passport_id_number": "AB123456",
    "passport_expiry_date": "01/01/2030",
    "date_of_birth": "01/01/1995",
    "passport_upload": "https://drive.google.com/passport",
    "headshot_upload": "https://drive.google.com/headshot",
    "social_media_link": "https://linkedin.com/in/alice",
    "location": "Ho Chi Minh City",
    "phone_number": "0901234567",
    "position_interest": "Teacher",
    "availability": "Monday, Wednesday",
    "start_date": "06/01",
    "commitment_duration": "6 months",
    "teaching_experience": "Some experience as TA",
    "experience_details": "Taught English to children in my community",
    "teaching_certificate": "No",
    "vietnamese_speaking": "Basic",
    "other_support": "",
    "referral_source": "Facebook",
    "motivation": "I want to help children learn English",
    "expected_gain": "Teaching experience and cultural exchange",
    "children_experience": "Yes, tutored children aged 8-12",
    "safeguarding_discomfort": "Speak to the person privately and report to coordinator",
    "safeguarding_physical": "Gently redirect and explain boundaries",
    "safeguarding_contact": "Politely decline and explain it is against policy",
}

SAMPLE_ACCEPTED_ROW = {
    **SAMPLE_PENDING_ROW,
    "applicant_status": "ACCEPTED",
    "email_address": "bob@example.com",
}
SAMPLE_REJECTED_ROW = {
    **SAMPLE_PENDING_ROW,
    "applicant_status": "REJECTED",
    "email_address": "carol@example.com",
}

GEMINI_ACCEPT_RESPONSE = json.dumps(
    {
        "summary": "Alice has uploaded all required documents and shows genuine enthusiasm.",
        "rating": 7,
        "verdict": "ACCEPTED",
        "reasoning": "All identity documents present, no safeguarding concerns.",
    }
)

GEMINI_REJECT_RESPONSE = json.dumps(
    {
        "summary": "Applicant did not provide identity documents.",
        "rating": 2,
        "verdict": "REJECTED",
        "reasoning": "Missing passport_upload and headshot_upload.",
    }
)


# ---------------------------------------------------------------------------
# Unit tests: GoogleSheetsService.get_pending_submissions_with_rows
# ---------------------------------------------------------------------------


class TestGetPendingSubmissionsWithRows:
    """Tests for the new method that returns (row_number, submission) tuples."""

    def _make_raw_rows(self, statuses, timestamp="01/01/2025 10:00:00", judgement=""):
        """Build raw sheet values list from a list of statuses.
        Col layout: A=status, B=timestamp, C=llm_judge_score, D=email, ...
        """
        rows = []
        for status in statuses:
            row = [
                status,
                timestamp,
                judgement,  # A, B, C
                "x@x.com",
                "",  # D email, E quiz_score
                "A",
                "B",  # F first, G last
                "P1",
                "01/01/30",
                "01/01/95",  # H,I,J
                "http://p",
                "http://h",
                "http://s",  # K,L,M
                "HCMC",
                "090",
                "Teacher",
                "Mon",  # N,O,P,Q
                "06/01",
                "6mo",
                "Some",
                "Details",  # R,S,T,U
                "No",
                "Basic",
                "",
                "FB",  # V,W,X,Y
                "Motivation",
                "Gain",
                "Yes",  # Z,AA,AB
                "",
                "Appropriate",
                "Appropriate",  # AC,AD,AE
                "No",
            ]  # AF
            rows.append(row)
        return rows

    def test_returns_only_pending_rows(self):
        """Only rows explicitly marked PENDING with a non-empty email are returned."""
        from app.services.google_sheets import GoogleSheetsService

        svc = GoogleSheetsService()
        svc._initialized = True
        mock_sheet = MagicMock()
        svc._sheet = mock_sheet

        raw = self._make_raw_rows(["PENDING", "ACCEPTED", "REJECTED", "", "PENDING"])
        mock_sheet.values().get().execute.return_value = {"values": raw}

        with (
            patch(
                "app.utils.config_helper.ConfigHelper.get_new_signups_sheet_id",
                return_value="SHEET_ID",
            ),
            patch(
                "app.utils.config_helper.ConfigHelper.get_google_sheets_max_retries",
                return_value=1,
            ),
        ):
            result = svc.get_pending_submissions_with_rows(db=MagicMock())

        statuses = [sub["applicant_status"] for _, sub in result]
        assert "ACCEPTED" not in statuses
        assert "REJECTED" not in statuses
        # Blank-status rows (new form submissions) and PENDING rows are both included
        assert len(result) == 3

    def test_row_numbers_are_one_indexed_with_header_offset(self):
        """Row 0 in values list → row 2 in sheet (header is row 1)."""
        from app.services.google_sheets import GoogleSheetsService

        svc = GoogleSheetsService()
        svc._initialized = True
        mock_sheet = MagicMock()
        svc._sheet = mock_sheet

        raw = self._make_raw_rows(["PENDING", "ACCEPTED", "PENDING"])
        mock_sheet.values().get().execute.return_value = {"values": raw}

        with (
            patch(
                "app.utils.config_helper.ConfigHelper.get_new_signups_sheet_id",
                return_value="SHEET_ID",
            ),
            patch(
                "app.utils.config_helper.ConfigHelper.get_google_sheets_max_retries",
                return_value=1,
            ),
        ):
            result = svc.get_pending_submissions_with_rows(db=MagicMock())

        row_numbers = [row_num for row_num, _ in result]
        # first PENDING is values[0] → sheet row 2
        # second PENDING is values[2] → sheet row 4
        assert row_numbers == [2, 4]

    def test_uses_wide_range(self):
        """Fetch range must cover well beyond the current 24 columns (A-X) to handle future additions."""
        from app.services.google_sheets import GoogleSheetsService

        svc = GoogleSheetsService()
        svc._initialized = True
        mock_sheet = MagicMock()
        svc._sheet = mock_sheet

        mock_sheet.values().get().execute.return_value = {"values": []}

        with (
            patch(
                "app.utils.config_helper.ConfigHelper.get_new_signups_sheet_id",
                return_value="SHEET_ID",
            ),
            patch(
                "app.utils.config_helper.ConfigHelper.get_google_sheets_max_retries",
                return_value=1,
            ),
        ):
            svc.get_pending_submissions_with_rows(db=MagicMock())

        get_call_kwargs = mock_sheet.values().get.call_args
        used_range = get_call_kwargs[1].get("range") or get_call_kwargs[0][1]
        assert used_range.startswith(
            "A2"
        ), f"Range should start with 'A2', got: {used_range}"
        # Must not use the old narrow ranges that drop columns S-X and beyond
        assert used_range not in ("A2:R", "A2:X"), f"Range is too narrow: {used_range}"

    def test_skips_rows_without_timestamp(self):
        """Rows with no timestamp are not real form entries and must be skipped."""
        from app.services.google_sheets import GoogleSheetsService

        svc = GoogleSheetsService()
        svc._initialized = True
        mock_sheet = MagicMock()
        svc._sheet = mock_sheet

        raw = self._make_raw_rows(["PENDING", ""], timestamp="")
        mock_sheet.values().get().execute.return_value = {"values": raw}

        with (
            patch(
                "app.utils.config_helper.ConfigHelper.get_new_signups_sheet_id",
                return_value="SHEET_ID",
            ),
            patch(
                "app.utils.config_helper.ConfigHelper.get_google_sheets_max_retries",
                return_value=1,
            ),
        ):
            result = svc.get_pending_submissions_with_rows(db=MagicMock())

        assert result == []

    def test_skips_rows_with_existing_judgement(self):
        """Rows that already have LLM judge output in col C are not re-judged."""
        from app.services.google_sheets import GoogleSheetsService

        svc = GoogleSheetsService()
        svc._initialized = True
        mock_sheet = MagicMock()
        svc._sheet = mock_sheet

        raw = self._make_raw_rows(
            ["PENDING", ""], judgement="[ACCEPTED] 7/10 | looks good"
        )
        mock_sheet.values().get().execute.return_value = {"values": raw}

        with (
            patch(
                "app.utils.config_helper.ConfigHelper.get_new_signups_sheet_id",
                return_value="SHEET_ID",
            ),
            patch(
                "app.utils.config_helper.ConfigHelper.get_google_sheets_max_retries",
                return_value=1,
            ),
        ):
            result = svc.get_pending_submissions_with_rows(db=MagicMock())

        assert result == []

    def test_returns_empty_list_when_no_pending_rows(self):
        """Returns empty list when all rows are already reviewed."""
        from app.services.google_sheets import GoogleSheetsService

        svc = GoogleSheetsService()
        svc._initialized = True
        mock_sheet = MagicMock()
        svc._sheet = mock_sheet

        raw = self._make_raw_rows(["ACCEPTED", "REJECTED"])
        mock_sheet.values().get().execute.return_value = {"values": raw}

        with (
            patch(
                "app.utils.config_helper.ConfigHelper.get_new_signups_sheet_id",
                return_value="SHEET_ID",
            ),
            patch(
                "app.utils.config_helper.ConfigHelper.get_google_sheets_max_retries",
                return_value=1,
            ),
        ):
            result = svc.get_pending_submissions_with_rows(db=MagicMock())

        assert result == []


# ---------------------------------------------------------------------------
# Unit tests: GoogleSheetsService.update_submission_judgment
# ---------------------------------------------------------------------------


class TestUpdateSubmissionJudgment:
    """Tests for writing LLM verdict back to the sheet."""

    def test_writes_to_correct_cells(self):
        """Status → col A, combined LLM text → col C (the dedicated LLM Judge Score column)."""
        from app.services.google_sheets import GoogleSheetsService

        svc = GoogleSheetsService()
        svc._initialized = True
        mock_sheet = MagicMock()
        svc._sheet = mock_sheet
        mock_sheet.values().batchUpdate().execute.return_value = {}

        with (
            patch(
                "app.utils.config_helper.ConfigHelper.get_new_signups_sheet_id",
                return_value="SHEET_ID",
            ),
            patch(
                "app.utils.config_helper.ConfigHelper.get_google_sheets_max_retries",
                return_value=1,
            ),
        ):
            svc.update_submission_judgment(
                db=MagicMock(),
                row_number=5,
                status="ACCEPTED",
                summary="Great candidate.",
                rating=8,
            )

        call_args = mock_sheet.values().batchUpdate.call_args
        body = call_args[1].get("body") or call_args[0][0]
        value_ranges = body["data"]
        written = {vr["range"]: vr["values"][0][0] for vr in value_ranges}

        assert written.get("A5") == "ACCEPTED"
        c5 = written.get("C5", "")
        # Col C = LLM Judge Score: verdict + rating + reasoning + summary
        assert "ACCEPTED" in c5
        assert "8/10" in c5
        assert "Great candidate." in c5
        # Col B (timestamp) and col D (quiz score) must NOT be touched
        assert "B5" not in written
        assert "D5" not in written

    def test_uses_correct_sheet_id(self):
        """batchUpdate is called with the signups sheet ID from config."""
        from app.services.google_sheets import GoogleSheetsService

        svc = GoogleSheetsService()
        svc._initialized = True
        mock_sheet = MagicMock()
        svc._sheet = mock_sheet
        mock_sheet.values().batchUpdate().execute.return_value = {}

        with (
            patch(
                "app.utils.config_helper.ConfigHelper.get_new_signups_sheet_id",
                return_value="MY_SHEET_ID",
            ),
            patch(
                "app.utils.config_helper.ConfigHelper.get_google_sheets_max_retries",
                return_value=1,
            ),
        ):
            svc.update_submission_judgment(
                db=MagicMock(),
                row_number=3,
                status="REJECTED",
                summary="No docs.",
                rating=1,
            )

        batchUpdate_call = mock_sheet.values().batchUpdate.call_args
        used_sheet_id = (
            batchUpdate_call[1].get("spreadsheetId") or batchUpdate_call[0][0]
        )
        assert used_sheet_id == "MY_SHEET_ID"


# ---------------------------------------------------------------------------
# Unit tests: _judge_submission
# ---------------------------------------------------------------------------


class TestJudgeSubmission:
    """Tests for the Gemini LLM judging helper."""

    def test_returns_accepted_verdict_on_valid_response(self):
        """Parses Gemini JSON and returns verdict ACCEPTED."""
        with (
            patch("google.generativeai.configure"),
            patch("google.generativeai.GenerativeModel") as mock_model_cls,
        ):
            mock_model = MagicMock()
            mock_model_cls.return_value = mock_model
            mock_model.generate_content.return_value = MagicMock(
                text=GEMINI_ACCEPT_RESPONSE
            )

            from app.routers.admin.signups import _judge_submission

            result = _judge_submission(SAMPLE_PENDING_ROW)

        assert result["verdict"] == "ACCEPTED"
        assert result["rating"] == 7
        assert "Alice" in result["summary"] or len(result["summary"]) > 10

    def test_returns_rejected_verdict_on_missing_docs(self):
        """Parses Gemini JSON and returns verdict REJECTED."""
        with (
            patch("google.generativeai.configure"),
            patch("google.generativeai.GenerativeModel") as mock_model_cls,
        ):
            mock_model = MagicMock()
            mock_model_cls.return_value = mock_model
            mock_model.generate_content.return_value = MagicMock(
                text=GEMINI_REJECT_RESPONSE
            )

            from app.routers.admin.signups import _judge_submission

            result = _judge_submission(SAMPLE_PENDING_ROW)

        assert result["verdict"] == "REJECTED"
        assert result["rating"] == 2

    def test_strips_markdown_fences_from_response(self):
        """Handles responses wrapped in ```json ... ``` fences."""
        fenced = f"```json\n{GEMINI_ACCEPT_RESPONSE}\n```"
        with (
            patch("google.generativeai.configure"),
            patch("google.generativeai.GenerativeModel") as mock_model_cls,
        ):
            mock_model = MagicMock()
            mock_model_cls.return_value = mock_model
            mock_model.generate_content.return_value = MagicMock(text=fenced)

            from app.routers.admin.signups import _judge_submission

            result = _judge_submission(SAMPLE_PENDING_ROW)

        assert result["verdict"] == "ACCEPTED"

    def test_raises_on_invalid_json(self):
        """Raises ValueError if Gemini returns unparseable response."""
        with (
            patch("google.generativeai.configure"),
            patch("google.generativeai.GenerativeModel") as mock_model_cls,
        ):
            mock_model = MagicMock()
            mock_model_cls.return_value = mock_model
            mock_model.generate_content.return_value = MagicMock(text="not json at all")

            from app.routers.admin.signups import _judge_submission

            with pytest.raises(ValueError, match="parse"):
                _judge_submission(SAMPLE_PENDING_ROW)

    def test_raises_when_gemini_key_missing(self):
        """Raises RuntimeError when GEMINI_API_KEY is not set."""
        import app.routers.admin.signups as signups_mod

        with patch.dict("os.environ", {}, clear=True):
            # Force re-evaluation by calling the function without the env var
            with pytest.raises((RuntimeError, ValueError)):
                # Patch GenerativeModel to simulate missing key scenario
                with patch(
                    "google.generativeai.configure", side_effect=Exception("No API key")
                ):
                    signups_mod._judge_submission(SAMPLE_PENDING_ROW)


# ---------------------------------------------------------------------------
# Integration tests: POST /admin/judge-pending-submissions
# ---------------------------------------------------------------------------


class TestJudgePendingSubmissionsEndpoint:
    """Tests for the admin endpoint."""

    def _pending_rows_fixture(self):
        return [(2, SAMPLE_PENDING_ROW)]

    _GOOD_JUDGMENT = {
        "summary": "Test summary",
        "rating": 7,
        "verdict": "ACCEPTED",
        "reasoning": "OK",
    }

    def test_dry_run_skips_sheet_write(self, client, test_db, mock_auth_service):
        """In dry_run mode the endpoint logs but does NOT call update_submission_judgment."""
        with (
            patch(
                "app.services.google_sheets.sheets_service.get_pending_submissions_with_rows",
                return_value=self._pending_rows_fixture(),
            ),
            patch(
                "app.utils.config_helper.ConfigHelper.get_dry_run", return_value=True
            ),
            patch(
                "app.services.google_sheets.sheets_service.update_submission_judgment"
            ) as mock_write,
            patch(
                "app.routers.admin.signups._judge_submission",
                return_value=self._GOOD_JUDGMENT,
            ),
            patch("time.sleep"),
        ):
            response = client.post("/admin/judge-pending-submissions")

        assert response.status_code == 200
        assert response.json()["dry_run"] is True
        mock_write.assert_not_called()

    def test_live_run_writes_to_sheet(self, client, test_db, mock_auth_service):
        """When dry_run is False the endpoint calls update_submission_judgment for each row."""
        with (
            patch(
                "app.services.google_sheets.sheets_service.get_pending_submissions_with_rows",
                return_value=self._pending_rows_fixture(),
            ),
            patch(
                "app.utils.config_helper.ConfigHelper.get_dry_run", return_value=False
            ),
            patch(
                "app.services.google_sheets.sheets_service.update_submission_judgment"
            ) as mock_write,
            patch(
                "app.routers.admin.signups._judge_submission",
                return_value=self._GOOD_JUDGMENT,
            ),
            patch("time.sleep"),
        ):
            response = client.post("/admin/judge-pending-submissions")

        assert response.status_code == 200
        mock_write.assert_called_once()
        assert mock_write.call_args[1]["status"] == "ACCEPTED"

    def test_response_contains_counts(self, client, test_db, mock_auth_service):
        """Response includes processed, accepted, rejected, errors, total_pending, remaining."""
        with (
            patch(
                "app.services.google_sheets.sheets_service.get_pending_submissions_with_rows",
                return_value=self._pending_rows_fixture(),
            ),
            patch(
                "app.utils.config_helper.ConfigHelper.get_dry_run", return_value=True
            ),
            patch(
                "app.services.google_sheets.sheets_service.update_submission_judgment"
            ),
            patch(
                "app.routers.admin.signups._judge_submission",
                return_value=self._GOOD_JUDGMENT,
            ),
            patch("time.sleep"),
        ):
            response = client.post("/admin/judge-pending-submissions")

        data = response.json()
        for key in (
            "processed",
            "accepted",
            "rejected",
            "errors",
            "total_pending",
            "remaining",
        ):
            assert key in data, f"Missing key: {key}"

    def test_llm_error_increments_errors_count(
        self, client, test_db, mock_auth_service
    ):
        """When _judge_submission raises, the row is skipped and error count increments."""
        with (
            patch(
                "app.services.google_sheets.sheets_service.get_pending_submissions_with_rows",
                return_value=self._pending_rows_fixture(),
            ),
            patch(
                "app.utils.config_helper.ConfigHelper.get_dry_run", return_value=False
            ),
            patch(
                "app.services.google_sheets.sheets_service.update_submission_judgment"
            ) as mock_write,
            patch(
                "app.routers.admin.signups._judge_submission",
                side_effect=ValueError("Gemini failed"),
            ),
            patch("time.sleep"),
        ):
            response = client.post("/admin/judge-pending-submissions")

        assert response.status_code == 200
        assert response.json()["errors"] == 1
        mock_write.assert_not_called()

    def test_no_pending_rows_returns_zero_counts(
        self, client, test_db, mock_auth_service
    ):
        """Returns zeros when there are no pending submissions to process."""
        with (
            patch(
                "app.services.google_sheets.sheets_service.get_pending_submissions_with_rows",
                return_value=[],
            ),
            patch(
                "app.utils.config_helper.ConfigHelper.get_dry_run", return_value=False
            ),
            patch("time.sleep"),
        ):
            response = client.post("/admin/judge-pending-submissions")

        data = response.json()
        assert data["processed"] == 0
        assert data["accepted"] == 0
        assert data["rejected"] == 0
        assert data["errors"] == 0

    def test_review_and_sync_chains_judge_then_sync(
        self, client, test_db, mock_auth_service
    ):
        """review-and-sync runs LLM judge first then syncs accepted submissions."""
        with (
            patch(
                "app.services.google_sheets.sheets_service.get_pending_submissions_with_rows",
                return_value=self._pending_rows_fixture(),
            ),
            patch(
                "app.utils.config_helper.ConfigHelper.get_dry_run", return_value=False
            ),
            patch(
                "app.services.google_sheets.sheets_service.update_submission_judgment"
            ) as mock_write,
            patch(
                "app.routers.admin.signups._judge_submission",
                return_value=self._GOOD_JUDGMENT,
            ),
            patch(
                "app.services.google_sheets.sheets_service.get_signup_form_submissions",
                return_value=[],
            ),
            patch("time.sleep"),
        ):
            response = client.post("/admin/review-and-sync")

        assert response.status_code == 200
        data = response.json()
        # Judge step ran and wrote to sheet
        mock_write.assert_called_once()
        # Response contains both judge and sync sections
        assert "judge" in data
        assert "sync" in data
        assert data["judge"]["accepted"] == 1

    def test_review_and_sync_reports_failure_when_sync_step_fails(
        self, client, test_db, mock_auth_service
    ):
        """If the sync step can't reach Google Sheets (e.g. a permission error),
        the top-level status must reflect that failure, not claim "success".

        Regression test: review_and_sync previously hardcoded
        {"status": "success"} regardless of what the sync step returned, so a
        cron run that silently failed to sync/email volunteers still reported
        success to Cloud Scheduler and any status-based monitoring.
        """
        with (
            patch(
                "app.services.google_sheets.sheets_service.get_pending_submissions_with_rows",
                return_value=self._pending_rows_fixture(),
            ),
            patch(
                "app.utils.config_helper.ConfigHelper.get_dry_run", return_value=False
            ),
            patch(
                "app.services.google_sheets.sheets_service.update_submission_judgment"
            ),
            patch(
                "app.routers.admin.signups._judge_submission",
                return_value=self._GOOD_JUDGMENT,
            ),
            patch(
                "app.services.google_sheets.sheets_service.get_signup_form_submissions",
                side_effect=Exception(
                    '<HttpError 403 ... "The caller does not have permission">'
                ),
            ),
            patch("time.sleep"),
        ):
            response = client.post("/admin/review-and-sync")

        data = response.json()
        assert data["status"] != "success"
        assert data["sync"]["status"] == "error"
