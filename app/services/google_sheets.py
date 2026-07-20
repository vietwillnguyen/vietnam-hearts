"""
Google Sheets Integration Service
"""

import ssl
from datetime import datetime, timedelta
from typing import Any

from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from app.config import (
    GOOGLE_APPLICATION_CREDENTIALS,
)
from app.utils.config_helper import ConfigHelper
from app.utils.google_credentials import default_credentials, get_scoped_credentials
from app.utils.logging_config import get_api_logger
from app.utils.retry_utils import log_ssl_error, safe_api_call
from app.utils.schedule_dates import (
    format_schedule_sheet_title,
    parse_schedule_sheet_title,
)

logger = get_api_logger()

# Google Sheets API setup
# Update the scopes to include write permissions
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# Column mapping for the volunteer signup form sheet.
# Index 0 = col A.  Update here when the form adds/removes columns.
SIGNUP_SHEET_HEADERS = [
    "applicant_status",  # A  – Form Response Status
    "timestamp",  # B  – Timestamp
    "llm_judge_score",  # C  – LLM Judge Score (our output column)
    "email_address",  # D  – Email Address
    "quiz_score",  # E  – Score on Quiz
    "first_name",  # F
    "last_name",  # G
    "passport_id_number",  # H
    "passport_expiry_date",  # I
    "date_of_birth",  # J
    "passport_upload",  # K
    "headshot_upload",  # L
    "social_media_link",  # M  – Facebook or LinkedIn profile
    "location",  # N  – Where are you from?
    "phone_number",  # O
    "position_interest",  # P
    "availability",  # Q
    "start_date",  # R
    "commitment_duration",  # S
    "teaching_experience",  # T  – Prior teaching/TA experience?
    "experience_details",  # U
    "teaching_certificate",  # V
    "vietnamese_speaking",  # W
    "other_support",  # X
    "referral_source",  # Y  – How did you hear about us?
    "motivation",  # Z  – Why interested in volunteering?
    "expected_gain",  # AA – What do you hope to gain?
    "children_experience",  # AB – Worked with children before?
    "preferred_age_group",  # AC
    "safeguarding_discomfort",  # AD – Team member made someone uncomfortable?
    "safeguarding_physical",  # AE – Child seeks physical affection?
    "safeguarding_contact",  # AF – Student asks for phone/social media?
    "teacher_responsibilities",  # AG
    "tuesday_class_focus",  # AH
    "agree_lesson_plan",  # AI
    "warnings_response",  # AJ
    "teacher_help_response",  # AK
    "ta_role",  # AL
    "teacher_absence_response",  # AM
    "true_statement",  # AN
    "agree_1",  # AO
    "agree_2",  # AP
    "agree_3",  # AQ
    "agree_4",  # AR
    "agree_5",  # AS
    "agree_6",  # AT
    "medical_conditions",  # AU
    "agree_medical",  # AV
    "legal_name_confirmation",  # AW
    "ta_per_class",  # AX
    "current_address",  # AY
]


class GoogleSheetsService:
    def __init__(self):
        """Initialize Google Sheets service with lazy initialization"""
        self._service = None
        self._sheet = None
        self._initialized = False
        logger.info("Google Sheets service created")

    def _validate_config(self, db: Session | None = None):
        """Validate Google Sheets configuration"""
        errors = []

        # Check required environment variables
        if db:
            # Use database settings if available
            if not ConfigHelper.get_schedule_sheet_id(db):
                errors.append("SCHEDULE_SIGNUP_LINK setting is required")

            if not ConfigHelper.get_new_signups_sheet_id(db):
                errors.append("NEW_SIGNUPS_RESPONSES_LINK setting is required")
        else:
            # Fallback to environment variables or skip validation
            logger.warning(
                "No database session provided for config validation, skipping dynamic settings check"
            )

        # Check for credentials (either a service-account key file, or ADC on Cloud Run)
        if GOOGLE_APPLICATION_CREDENTIALS.exists():
            logger.info(
                f"File-based credentials found at {GOOGLE_APPLICATION_CREDENTIALS}"
            )
        else:
            try:
                creds, project = default_credentials()
                logger.info(
                    f"Application Default Credentials available with project: {project}"
                )
            except Exception:
                errors.append(
                    "No Google credentials found. Please either:\n"
                    "1. Set GOOGLE_APPLICATION_CREDENTIALS to point to a valid credentials file, or\n"
                    "2. Ensure Application Default Credentials are available (e.g. Cloud Run's attached service account)"
                )

        if errors:
            error_msg = "\n".join(errors)
            logger.error(f"Google Sheets configuration validation failed:\n{error_msg}")
            raise ValueError(
                f"Google Sheets configuration validation failed:\n{error_msg}"
            )

    def _initialize_service(self, db: Session | None = None):
        """Initialize the Google Sheets service"""
        try:
            self._validate_config(db)

            creds = get_scoped_credentials(SCOPES)

            # Build service with modern parameters to avoid file_cache warnings
            self._service = build(
                "sheets", "v4", credentials=creds, cache_discovery=False
            )
            self._sheet = self._service.spreadsheets()
            self._initialized = True
            logger.info(
                f"Google Sheets service initialized successfully with service account email: {creds.service_account_email}"
            )

        except Exception as e:
            logger.error(
                f"Failed to initialize Google Sheets service: {str(e)}", exc_info=True
            )
            raise

    def _ensure_initialized(self, db: Session | None = None):
        """Ensure the service is initialized"""
        if not self._initialized:
            self._initialize_service(db)

    @property
    def service(self) -> Any:
        """Get the Google Sheets service (lazy initialization)"""
        self._ensure_initialized()
        return self._service

    @property
    def sheet(self) -> Any:
        """Get the Google Sheets spreadsheet service (lazy initialization)"""
        self._ensure_initialized()
        return self._sheet

    def get_range_from_sheet(
        self, db: Session, sheet_id: str, range_name: str
    ) -> list[list[str]]:
        """
        Fetch a specific range from a given sheet (raw values) with retry logic
        Args:
            db: Database session for configuration
            sheet_id (str): The Google Sheet ID
            range_name (str): The A1 notation range to fetch (e.g., 'B7:G11')
        Returns:
            List[List[str]]: 2D list of cell values
        """
        # Get retry configuration from database
        max_attempts = ConfigHelper.get_google_sheets_max_retries(db)

        def _fetch_range():
            result = (
                self.sheet.values()
                .get(spreadsheetId=sheet_id, range=range_name)
                .execute()
            )
            values = result.get("values", [])
            logger.info(
                f"Fetched range {range_name} from sheet {sheet_id} with {len(values)} rows"
            )
            logger.info(f"Values: {values}")
            return values

        try:
            return safe_api_call(
                _fetch_range,
                max_attempts=max_attempts,
                context=f"fetch range {range_name} from sheet {sheet_id}",
            )
        except ssl.SSLEOFError as e:
            log_ssl_error(e, f"get_range_from_sheet({sheet_id}, {range_name})")
            logger.error(
                f"SSL EOF error while fetching range {range_name} from sheet {sheet_id}: {str(e)}"
            )
            raise
        except Exception as e:
            logger.error(
                f"Failed to fetch range {range_name} from sheet {sheet_id}: {str(e)}",
                exc_info=True,
            )
            return []

    def get_schedule_range(
        self, db: Session, range_name: str | None = None
    ) -> list[list[str]]:
        """
        Fetch a specific range from the schedule sheet (raw values)
        Args:
            db: Database session for configuration
            range_name (str): The A1 notation range to fetch (e.g., 'B7:G11')
        Returns:
            List[List[str]]: 2D list of cell values
        """
        sheet_id = ConfigHelper.get_schedule_sheet_id(db)
        if not range_name:
            raise ValueError("range_name is required for get_schedule_range")
        return self.get_range_from_sheet(db, sheet_id, range_name)

    def get_schedule_blocks(self, db: Session, range_name: str = "B1:G100"):
        """
        Auto-discover class blocks from the current schedule sheet.

        The schedule tab is the single source of truth: this fetches the whole
        class area (starting at column B so labels are at index 0) and parses it
        into ClassBlock objects, tolerating missing rows, blank separators, and
        the Sheets API's trailing-empty trimming. No hardcoded per-class ranges.
        """
        from app.services.schedule_parser import discover_schedule_blocks

        rows = self.get_schedule_range(db, range_name)
        return discover_schedule_blocks(rows)

    def get_signup_form_submissions(
        self, db: Session, range_name: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Fetch all form submissions from the signups sheet with retry logic
        Args:
            db: Database session for configuration
            range_name (str): The A1 notation range to fetch
        Returns:
            List[Dict[str, Any]]: List of form submissions with field mappings
        """
        # Get retry configuration from database
        max_attempts = ConfigHelper.get_google_sheets_max_retries(db)

        def _fetch_submissions():
            sheet_id = ConfigHelper.get_new_signups_sheet_id(db)
            if not sheet_id:
                raise ValueError(
                    "NEW_SIGNUPS_RESPONSES_LINK is not configured. Please set it in the admin settings."
                )
            full_range = range_name or "A2:ZZ"
            logger.info(
                f"Fetching signups from sheet {sheet_id} with range {full_range}"
            )
            result = (
                self.sheet.values()
                .get(
                    spreadsheetId=sheet_id,
                    range=full_range,
                )
                .execute()
            )
            values = result.get("values", [])
            headers = SIGNUP_SHEET_HEADERS

            # Process each row into a dictionary
            submissions = []
            skipped_count = 0
            for row in values:
                # Pad row with empty strings if it's shorter than headers
                row_data = row + [""] * (len(headers) - len(row))
                submission = dict(zip(headers, row_data, strict=False))

                # Skip submissions with empty email addresses or missing essential fields
                email_address = submission.get("email_address", "").strip()
                if not email_address:
                    logger.debug(
                        f"Skipping submission with empty email address: {submission}"
                    )
                    skipped_count += 1
                    continue

                # Skip submissions that are completely empty (no meaningful data)
                has_meaningful_data = any(
                    [
                        submission.get("first_name", "").strip(),
                        submission.get("last_name", "").strip(),
                        submission.get("phone_number", "").strip(),
                        submission.get("position_interest", "").strip(),
                        submission.get("availability", "").strip(),
                    ]
                )

                if not has_meaningful_data:
                    logger.debug(
                        f"Skipping submission with no meaningful data: {submission}"
                    )
                    skipped_count += 1
                    continue

                # Convert timestamp string to datetime
                if submission["timestamp"]:
                    try:
                        submission["timestamp"] = datetime.strptime(
                            submission["timestamp"], "%m/%d/%Y %H:%M:%S"
                        )
                    except ValueError:
                        logger.warning(
                            f"Invalid timestamp format: {submission['timestamp']}"
                        )

                submissions.append(submission)

            logger.info(
                f"Processed {len(values)} total rows: {len(submissions)} valid submissions, {skipped_count} skipped"
            )
            return submissions

        try:
            return safe_api_call(
                _fetch_submissions,
                max_attempts=max_attempts,
                context="fetch signup form submissions",
            )
        except ssl.SSLEOFError as e:
            log_ssl_error(e, "get_signup_form_submissions")
            logger.error(f"SSL EOF error while fetching form submissions: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Failed to fetch form submissions: {str(e)}", exc_info=True)
            raise

    def create_sheet_from_template(
        self, template_sheet_name: str, new_sheet_date: datetime, db: Session
    ) -> str:
        """
        Create a new sheet from a template, named 'Schedule MM/DD', and insert it after the last schedule sheet.
        """
        try:
            sheet_metadata = self.sheet.get(
                spreadsheetId=ConfigHelper.get_schedule_sheet_id(db)
            ).execute()
            template_sheet_id = next(
                (
                    s["properties"]["sheetId"]
                    for s in sheet_metadata["sheets"]
                    if s["properties"]["title"] == template_sheet_name
                ),
                None,
            )
            if not template_sheet_id:
                raise ValueError(f"Template sheet {template_sheet_name} not found")

            new_sheet_title = format_schedule_sheet_title(new_sheet_date)

            # Check if sheet already exists
            for sheet in sheet_metadata["sheets"]:
                if sheet["properties"]["title"] == new_sheet_title:
                    logger.info(
                        f"Sheet {new_sheet_title} already exists, skipping creation"
                    )
                    return sheet["properties"]["sheetId"]

            # Find the last schedule sheet index
            schedule_sheet_indices = [
                i
                for i, s in enumerate(sheet_metadata["sheets"])
                if s["properties"]["title"].startswith("Schedule ")
            ]
            if schedule_sheet_indices:
                insert_index = max(schedule_sheet_indices) + 1
            else:
                insert_index = 0  # If none exist, insert at the beginning

            copy_request = {
                "requests": [
                    {
                        "duplicateSheet": {
                            "sourceSheetId": template_sheet_id,
                            "insertSheetIndex": insert_index,
                            "newSheetName": new_sheet_title,
                        }
                    }
                ]
            }
            response = self.sheet.batchUpdate(
                spreadsheetId=ConfigHelper.get_schedule_sheet_id(db), body=copy_request
            ).execute()
            new_sheet_id = response["replies"][0]["duplicateSheet"]["properties"][
                "sheetId"
            ]
            logger.info(
                f"Successfully created new sheet: {new_sheet_title} at index {insert_index}"
            )
            return new_sheet_id
        except Exception as e:
            logger.error(
                f"Failed to create sheet from template: {str(e)}", exc_info=True
            )
            raise

    def hide_sheet(self, sheet_name: str, db: Session):
        """
        Hide a sheet by name
        Args:
            sheet_name: Name of the sheet to hide (in format Schedule MM/DD/YYYY)
        """
        try:
            sheet_metadata = self.sheet.get(
                spreadsheetId=ConfigHelper.get_schedule_sheet_id(db)
            ).execute()
            available_sheets = [
                sheet["properties"]["title"] for sheet in sheet_metadata["sheets"]
            ]
            logger.info(f"Available sheets: {available_sheets}")
            sheet_id = next(
                (
                    s["properties"]["sheetId"]
                    for s in sheet_metadata["sheets"]
                    if s["properties"]["title"] == sheet_name
                ),
                None,
            )
            if not sheet_id:
                raise ValueError(
                    f"Sheet {sheet_name} not found. Available sheets: {available_sheets}"
                )
            request = {
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {"sheetId": sheet_id, "hidden": True},
                            "fields": "hidden",
                        }
                    }
                ]
            }
            self.sheet.batchUpdate(
                spreadsheetId=ConfigHelper.get_schedule_sheet_id(db), body=request
            ).execute()
            logger.info(f"Successfully hidden sheet: {sheet_name}")
        except Exception as e:
            logger.error(f"Failed to hide sheet: {str(e)}", exc_info=True)
            raise

    def update_sheet_dates(self, sheet_date: datetime, db: Session):
        """
        Update the blue header and table header dates in the new sheet.
        """
        try:
            sheet_title = format_schedule_sheet_title(sheet_date)
            spreadsheet_id = ConfigHelper.get_schedule_sheet_id(db)
            sheet_metadata = self.sheet.get(spreadsheetId=spreadsheet_id).execute()
            individual_sheet_id = next(
                (
                    s["properties"]["sheetId"]
                    for s in sheet_metadata["sheets"]
                    if s["properties"]["title"] == sheet_title
                ),
                None,
            )
            if not individual_sheet_id:
                raise ValueError(f"Sheet {sheet_title} not found")

            # Update blue header (assuming it's cell C1)
            blue_header_range = f"{sheet_title}!C1"
            self.sheet.values().update(
                spreadsheetId=spreadsheet_id,
                range=blue_header_range,
                valueInputOption="USER_ENTERED",
                body={"values": [[sheet_title]]},
            ).execute()

            self.sheet.values().update(
                spreadsheetId=spreadsheet_id,
                range=f"{sheet_title}!B1",
                valueInputOption="USER_ENTERED",
                body={
                    "values": [[f"Schedule for Week {sheet_date.strftime('%d/%m/%Y')}"]]
                },
            ).execute()

            # Update table header dates for each class. Discover class header rows
            # by scanning the sheet (column B holds the title, days follow) rather
            # than relying on hardcoded per-class ranges.
            from app.services.schedule_parser import row_is_class_header

            dates = [
                (sheet_date + timedelta(days=i)).strftime("%d/%m") for i in range(5)
            ]
            grid = self.get_range_from_sheet(
                db, spreadsheet_id, f"{sheet_title}!A1:G100"
            )
            for offset, row in enumerate(grid):
                # title is in column B (index 1) when fetched from column A
                if not row_is_class_header(row, title_index=1):
                    continue
                row_num = offset + 1  # 1-based sheet row
                # Write the 5 dates into C:G, preserving the title in column B.
                header_range = f"{sheet_title}!C{row_num}:G{row_num}"
                self.sheet.values().update(
                    spreadsheetId=spreadsheet_id,
                    range=header_range,
                    valueInputOption="USER_ENTERED",
                    body={"values": [dates]},
                ).execute()
                logger.info(f"Updated {header_range} to {dates}")
            logger.info(f"Successfully updated dates in sheet {sheet_title}")
        except Exception as e:
            logger.error(f"Failed to update sheet dates: {str(e)}", exc_info=True)
            raise

    def get_sheet_metadata(self, db: Session) -> dict:
        """Get metadata for all sheets in the spreadsheet"""
        try:
            return self.sheet.get(
                spreadsheetId=ConfigHelper.get_schedule_sheet_id(db)
            ).execute()
        except Exception as e:
            logger.error(f"Failed to get sheet metadata: {str(e)}", exc_info=True)
            raise

    def get_schedule_sheets(self, db: Session) -> list[dict]:
        """Get all schedule sheets and their metadata"""
        metadata = self.get_sheet_metadata(db)
        return [
            sheet
            for sheet in metadata["sheets"]
            if sheet["properties"]["title"].startswith("Schedule ")
        ]

    def get_sheet_by_date(self, date: datetime, db: Session) -> dict | None:
        """Get sheet metadata for a specific date (matches both title formats)"""
        sheets = self.get_schedule_sheets(db)
        for sheet in sheets:
            parsed = parse_schedule_sheet_title(sheet["properties"]["title"])
            if parsed and parsed.date() == date.date():
                return sheet
        return None

    def get_current_schedule_dates(self, db: Session) -> tuple[datetime, datetime]:
        """
        Get the Monday and Friday dates from the current visible schedule sheet.

        Returns:
            tuple[datetime, datetime]: (monday_date, friday_date) from the current schedule
        """
        try:
            # Get all schedule sheets
            schedule_sheets = self.get_schedule_sheets(db)

            # Find the first visible schedule sheet (they should be ordered by date)
            visible_sheet = None
            for sheet in schedule_sheets:
                if not sheet["properties"].get("hidden", False):
                    visible_sheet = sheet
                    break

            if not visible_sheet:
                logger.warning(
                    "No visible schedule sheet found, falling back to calculated dates"
                )
                # Fallback to calculated dates
                now = datetime.now()
                days_since_monday = now.weekday()
                current_monday = now - timedelta(days=days_since_monday)
                current_friday = current_monday + timedelta(days=4)
                return current_monday, current_friday

            # Extract date from sheet title (DD/MM/YYYY, or legacy MM/DD)
            sheet_title = visible_sheet["properties"]["title"]
            sheet_date = parse_schedule_sheet_title(sheet_title)

            if sheet_date is None:
                logger.warning(f"Could not parse date from sheet title '{sheet_title}'")
                # Fallback to calculated dates
                now = datetime.now()
                days_since_monday = now.weekday()
                current_monday = now - timedelta(days=days_since_monday)
                current_friday = current_monday + timedelta(days=4)
                return current_monday, current_friday

            # Calculate Monday and Friday for this week
            days_since_monday = sheet_date.weekday()
            monday_date = sheet_date - timedelta(days=days_since_monday)
            friday_date = monday_date + timedelta(days=4)

            logger.info(
                f"Extracted dates from sheet '{sheet_title}': Monday {monday_date.strftime('%Y-%m-%d')}, Friday {friday_date.strftime('%Y-%m-%d')}"
            )
            return monday_date, friday_date

        except Exception as e:
            logger.error(
                f"Failed to get current schedule dates: {str(e)}", exc_info=True
            )
            # Fallback to calculated dates
            now = datetime.now()
            days_since_monday = now.weekday()
            current_monday = now - timedelta(days=days_since_monday)
            current_friday = current_monday + timedelta(days=4)
            return current_monday, current_friday

    def set_sheet_visibility(self, sheet_id: int, hidden: bool, db: Session):
        """Set the visibility of a sheet"""
        try:
            request = {
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {"sheetId": sheet_id, "hidden": hidden},
                            "fields": "hidden",
                        }
                    }
                ]
            }
            self.sheet.batchUpdate(
                spreadsheetId=ConfigHelper.get_schedule_sheet_id(db), body=request
            ).execute()
            logger.info(
                f"Set sheet {sheet_id} visibility to {'hidden' if hidden else 'visible'}"
            )
        except Exception as e:
            logger.error(f"Failed to set sheet visibility: {str(e)}", exc_info=True)
            raise

    def rename_sheet(self, sheet_id: int, new_title: str, db: Session):
        """Rename a sheet (used to migrate legacy MM/DD titles to DD/MM/YYYY)"""
        try:
            request = {
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {"sheetId": sheet_id, "title": new_title},
                            "fields": "title",
                        }
                    }
                ]
            }
            self.sheet.batchUpdate(
                spreadsheetId=ConfigHelper.get_schedule_sheet_id(db), body=request
            ).execute()
            logger.info(f"Renamed sheet {sheet_id} to '{new_title}'")
        except Exception as e:
            logger.error(f"Failed to rename sheet: {str(e)}", exc_info=True)
            raise

    def move_sheet(self, sheet_id: int, new_index: int, db: Session):
        """
        Move a sheet to a new position in the spreadsheet.

        Args:
            sheet_id (int): The ID of the sheet to move
            new_index (int): The new position (0-based index)
        """
        try:
            request = {
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {"sheetId": sheet_id, "index": new_index},
                            "fields": "index",
                        }
                    }
                ]
            }
            self.sheet.batchUpdate(
                spreadsheetId=ConfigHelper.get_schedule_sheet_id(db), body=request
            ).execute()
            logger.info(f"Moved sheet {sheet_id} to index {new_index}")
        except Exception as e:
            logger.error(f"Failed to move sheet: {str(e)}", exc_info=True)
            raise

    def get_pending_submissions_with_rows(self, db: Session) -> list[tuple]:
        """
        Fetch signup submissions that have not yet been reviewed.

        A row is pending when it is a real form entry (non-empty timestamp and
        email) that has no LLM judgement yet (blank llm_judge_score) and has
        not reached a final state (ACCEPTED/REJECTED).

        Returns:
            List of (row_number, submission_dict) tuples.
            row_number is 1-based with the header occupying row 1, so values[0] → row 2.
        """
        self._ensure_initialized(db)
        sheet_id = ConfigHelper.get_new_signups_sheet_id(db)
        max_attempts = ConfigHelper.get_google_sheets_max_retries(db)

        headers = SIGNUP_SHEET_HEADERS

        def _fetch():
            result = (
                self._sheet.values()
                .get(spreadsheetId=sheet_id, range="A2:ZZ")
                .execute()
            )
            return result.get("values", [])

        try:
            raw_rows = safe_api_call(
                _fetch, max_attempts=max_attempts, context="fetch pending submissions"
            )
        except Exception as e:
            logger.error(f"Failed to fetch pending submissions: {e}", exc_info=True)
            return []

        pending = []
        for index, row in enumerate(raw_rows):
            row_data = row + [""] * (len(headers) - len(row))
            submission = dict(zip(headers, row_data, strict=False))
            status = submission.get("applicant_status", "").strip().upper()
            email = submission.get("email_address", "").strip()
            timestamp = submission.get("timestamp", "").strip()
            judgement = submission.get("llm_judge_score", "").strip()
            # A pending row is a real form entry (timestamp + email present)
            # that has no judgement yet and hasn't reached a final state.
            # New Google Form submissions arrive with a blank status, so we check
            # for the absence of final states rather than presence of "PENDING".
            if (
                timestamp
                and email
                and not judgement
                and status not in ("ACCEPTED", "REJECTED")
            ):
                row_number = index + 2  # header is row 1
                pending.append((row_number, submission))

        logger.info(
            f"Found {len(pending)} pending submissions out of {len(raw_rows)} total rows"
        )
        return pending

    def update_submission_judgment(
        self,
        db: Session,
        row_number: int,
        status: str,
        summary: str,
        rating: int,
        reasoning: str = "",
    ) -> None:
        """
        Write LLM judgment back to the signups sheet for a single row.

        Writes:
            Column A → status (ACCEPTED or REJECTED)
            Column B → LLM summary
            Column D → rating (1-10)
        """
        self._ensure_initialized(db)
        sheet_id = ConfigHelper.get_new_signups_sheet_id(db)
        max_attempts = ConfigHelper.get_google_sheets_max_retries(db)

        parts = [f"[{status}] {rating}/10"]
        if reasoning:
            parts.append(reasoning)
        if summary:
            parts.append(summary)
        llm_text = " | ".join(parts)
        body = {
            "valueInputOption": "RAW",
            "data": [
                {"range": f"A{row_number}", "values": [[status]]},
                {"range": f"C{row_number}", "values": [[llm_text]]},
            ],
        }

        def _write():
            return (
                self._sheet.values()
                .batchUpdate(spreadsheetId=sheet_id, body=body)
                .execute()
            )

        try:
            safe_api_call(
                _write,
                max_attempts=max_attempts,
                context=f"update judgment for row {row_number}",
            )
            logger.info(
                f"Wrote judgment to row {row_number}: {status} / rating={rating}"
            )
        except Exception as e:
            logger.error(
                f"Failed to write judgment for row {row_number}: {e}", exc_info=True
            )
            raise

    def rotate_schedule_sheets(
        self, db: Session, display_weeks_override: int | None = None
    ) -> dict[str, Any]:
        """
        Sync schedule sheets to the current date: exactly `display_weeks_count`
        dated sheets are visible, starting from the Monday of the week
        containing "now" and running forward in chronological order. Every
        other dated sheet is hidden. This is idempotent reconciliation, not
        incremental rotation - it can be called at any time, on any day of
        the week, and always converges on the same target state for "now".

        Args:
            db: Database session
            display_weeks_override: Optional override for the number of weeks to display (overrides setting)

        Returns:
            Dict[str, Any]: Detailed information about the rotation operation including:
                - changes: What sheets were added, hidden, unhidden, reordered
                - current_state: Current visible and hidden sheets
                - display_dates: The dates that should be displayed
        """
        try:
            now = datetime.now()

            # Anchor to the Monday of the week containing "now" - not next
            # Monday - so the display is always accurate to today's date
            # regardless of which day of the week this runs on.
            days_since_monday = now.weekday()
            current_monday = now - timedelta(days=days_since_monday)

            # Get all existing schedule sheets before rotation
            existing_sheets = self.get_schedule_sheets(db)
            before_state = {
                sheet["properties"]["title"]: {
                    "hidden": sheet["properties"].get("hidden", False),
                    "index": sheet["properties"].get("index", 0),
                }
                for sheet in existing_sheets
            }

            # Use override if provided, otherwise use setting
            display_weeks_count = (
                display_weeks_override
                if display_weeks_override is not None
                else ConfigHelper.get_schedule_sheets_display_weeks_count(db)
            )

            display_dates = [
                current_monday + timedelta(days=7 * i)
                for i in range(display_weeks_count)
            ]

            # Dates that should be visible after rotation
            display_date_set = {date.date() for date in display_dates}

            sheets_renamed = []
            sheets_failed = []

            # Hide the 'Schedule Template' sheet if it exists
            template_sheet = next(
                (
                    s
                    for s in existing_sheets
                    if s["properties"]["title"] == "Schedule Template"
                ),
                None,
            )
            if template_sheet and not template_sheet["properties"].get("hidden", False):
                try:
                    self.set_sheet_visibility(
                        template_sheet["properties"]["sheetId"], True, db
                    )
                    logger.info("Set 'Schedule Template' sheet to hidden")
                except Exception as e:
                    logger.error(
                        f"Failed to hide 'Schedule Template' sheet: {str(e)}",
                        exc_info=True,
                    )

            # PASS 1: Backfill every dated sheet's title to the canonical
            # DD/MM/YYYY format, then hide all dated schedule sheets outside
            # the display range. Backfill runs regardless of display range so
            # legacy "Schedule MM/DD" titles get migrated even on sheets that
            # are about to be hidden - otherwise a sheet that rotates out of
            # view keeps its legacy title forever, since it will never pass
            # through PASS 2 again.
            # Running hide before show ensures visible count never exceeds display_weeks_count,
            # even if the operation is interrupted partway through.
            # Only sheets whose title parses as a date are considered, which
            # naturally excludes "Schedule Template" and "Schedule Config".
            # A failure on one sheet (e.g. a protected sheet the service
            # account cannot edit) must never abort the whole rotation.
            for sheet in existing_sheets:
                title = sheet["properties"]["title"]
                sheet_date = parse_schedule_sheet_title(title)
                if sheet_date is None:
                    continue

                canonical_title = format_schedule_sheet_title(sheet_date)
                if title != canonical_title:
                    try:
                        self.rename_sheet(
                            sheet["properties"]["sheetId"], canonical_title, db
                        )
                        sheets_renamed.append(canonical_title)
                        title = canonical_title
                    except Exception as e:
                        sheets_failed.append(
                            {"title": title, "action": "rename", "error": str(e)}
                        )
                        logger.warning(
                            f"Could not rename sheet '{title}', continuing: {e}"
                        )

                if sheet_date.date() not in display_date_set:
                    currently_visible = not sheet["properties"].get("hidden", False)
                    if currently_visible:
                        try:
                            self.set_sheet_visibility(
                                sheet["properties"]["sheetId"], True, db
                            )
                            logger.info(f"Set sheet {title} visibility to hidden")
                        except Exception as e:
                            sheets_failed.append(
                                {"title": title, "action": "hide", "error": str(e)}
                            )
                            logger.warning(
                                f"Could not hide sheet '{title}', continuing rotation: {e}"
                            )

            # PASS 2: Make each display-range sheet visible and move it into
            # position, in chronological order. Existing sheets are matched
            # by parsed date (not title text) so sheets already migrated to
            # DD/MM/YYYY in PASS 1 are still found and reused instead of
            # duplicated.
            for i, date in enumerate(display_dates):
                sheet_name = format_schedule_sheet_title(date)
                existing_sheet = next(
                    (
                        s
                        for s in existing_sheets
                        if (
                            parsed := parse_schedule_sheet_title(
                                s["properties"]["title"]
                            )
                        )
                        and parsed.date() == date.date()
                    ),
                    None,
                )

                try:
                    if existing_sheet:
                        self.set_sheet_visibility(
                            existing_sheet["properties"]["sheetId"], False, db
                        )
                        self.move_sheet(
                            existing_sheet["properties"]["sheetId"], i + 1, db
                        )  # +1 to account for template sheet at index 0
                    else:
                        new_sheet_id = self.create_sheet_from_template(
                            "Schedule Template", date, db
                        )
                        self.update_sheet_dates(date, db)
                        self.set_sheet_visibility(int(new_sheet_id), False, db)
                        self.move_sheet(int(new_sheet_id), i + 1, db)
                except Exception as e:
                    sheets_failed.append(
                        {"title": sheet_name, "action": "show", "error": str(e)}
                    )
                    logger.error(
                        f"Failed to prepare sheet '{sheet_name}', continuing rotation: {e}",
                        exc_info=True,
                    )

            # Get final state after all changes
            final_sheets = self.get_schedule_sheets(db)
            after_state = {
                sheet["properties"]["title"]: {
                    "hidden": sheet["properties"].get("hidden", False),
                    "index": sheet["properties"].get("index", 0),
                }
                for sheet in final_sheets
            }

            # Calculate final changes by comparing before and after
            changes = {
                "sheets_added": [
                    title for title in after_state if title not in before_state
                ],
                "sheets_hidden": [
                    title
                    for title in before_state
                    if title in after_state
                    and not before_state[title]["hidden"]
                    and after_state[title]["hidden"]
                ],
                "sheets_unhidden": [
                    title
                    for title in before_state
                    if title in after_state
                    and before_state[title]["hidden"]
                    and not after_state[title]["hidden"]
                ],
                "sheets_reordered": [
                    title
                    for title in before_state
                    if title in after_state
                    and before_state[title]["index"] != after_state[title]["index"]
                ],
            }

            result = {
                "changes": changes,
                "current_state": {
                    "visible_sheets": [
                        title
                        for title, state in after_state.items()
                        if not state["hidden"]
                    ],
                    "hidden_sheets": [
                        title for title, state in after_state.items() if state["hidden"]
                    ],
                },
                "display_dates": [date.strftime("%d/%m/%Y") for date in display_dates],
                "display_weeks_count": display_weeks_count,
                "display_weeks_override_used": display_weeks_override is not None,
                "sheets_renamed": sheets_renamed,
                "sheets_failed": sheets_failed,
            }

            if sheets_failed:
                logger.warning(
                    f"Rotated schedule sheets for {display_weeks_count} weeks with "
                    f"{len(sheets_failed)} failure(s): {sheets_failed}"
                )
            else:
                logger.info(
                    f"Successfully rotated schedule sheets for {display_weeks_count} weeks"
                )
            return result

        except Exception as e:
            logger.error(f"Failed to rotate schedule sheets: {str(e)}", exc_info=True)
            raise


# Create a singleton instance
sheets_service = GoogleSheetsService()
