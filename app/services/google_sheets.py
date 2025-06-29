"""
Google Sheets Integration Service

This service handles:
1. Reading form submissions from Google Sheets
2. Processing and validating form data
3. Converting form submissions to volunteer records
4. Managing schedule sheet rotation and visibility
"""

import os
import ssl
from typing import List, Dict, Any, Optional
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta
from pathlib import Path
from app.utils.logging_config import get_api_logger
from app.utils.retry_utils import retry_google_sheets_api, log_ssl_error
from app.services.classes_config import CLASS_CONFIG
from app.config import (
    SCHEDULE_SHEETS_DISPLAY_WEEKS_COUNT,
    GOOGLE_APPLICATION_CREDENTIALS,
    GOOGLE_SHEETS_MAX_RETRIES,
    GOOGLE_SHEETS_BASE_WAIT,
    GOOGLE_SHEETS_MAX_WAIT,
)
from google.auth import default
from google.auth.exceptions import DefaultCredentialsError

logger = get_api_logger()

# Google Sheets API setup
# Update the scopes to include write permissions
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]
from app.config import (
    SCHEDULE_SHEET_ID as SCHEDULE_SHEET_ID,
    NEW_SIGNUPS_SHEET_ID as SIGNUPS_SHEET_ID,
    GOOGLE_SCHEDULE_RANGE as SCHEDULE_DEFAULT_RANGE,
    GOOGLE_SIGNUPS_RANGE as SIGNUPS_DEFAULT_RANGE,
)


class GoogleSheetsService:
    def __init__(self):
        """Initialize Google Sheets service with lazy initialization"""
        self._service = None
        self._sheet = None
        self._initialized = False
        logger.info("Google Sheets service created")

    def _validate_config(self):
        """Validate Google Sheets configuration"""
        errors = []
        
        # Check required environment variables
        if not SCHEDULE_SHEET_ID:
            errors.append("SCHEDULE_SHEET_ID environment variable is required")
        
        if not SIGNUPS_SHEET_ID:
            errors.append("NEW_SIGNUPS_SHEET_ID environment variable is required")
        
        # Check for credentials (either ADC or file-based)
        try:
            creds, project = default(scopes=SCOPES)
            logger.info(f"Application Default Credentials available with project: {project}")
            logger.info(f"Service account email: {creds.service_account_email}")
        except Exception:
            # ADC not available, check for file-based credentials
            if not GOOGLE_APPLICATION_CREDENTIALS.exists():
                errors.append(
                    "No Google credentials found. Please either:\n"
                    "1. Run 'gcloud auth application-default login' for local development, or\n"
                    f"2. Set GOOGLE_APPLICATION_CREDENTIALS to point to a valid credentials file"
                )
            else:
                logger.info(f"File-based credentials found at {GOOGLE_APPLICATION_CREDENTIALS}")

        if errors:
            error_msg = "\n".join(errors)
            logger.error(f"Google Sheets configuration validation failed:\n{error_msg}")
            raise ValueError(f"Google Sheets configuration validation failed:\n{error_msg}")

    def _initialize_service(self):
        """Initialize the Google Sheets service"""
        try:
            self._validate_config()
            
            # Try Application Default Credentials first (works in cloud and local with gcloud auth)
            try:
                creds, project = default(scopes=SCOPES)
                logger.info(f"Using Application Default Credentials with project: {project}")
            except DefaultCredentialsError:
                # Fall back to file-based credentials (local development)
                logger.info("ADC not available, falling back to file-based credentials")
                creds = Credentials.from_service_account_file(
                    str(GOOGLE_APPLICATION_CREDENTIALS), scopes=SCOPES
                )
                logger.info(f"Using file-based Google credentials from {GOOGLE_APPLICATION_CREDENTIALS}")

            # Build service
            self._service = build("sheets", "v4", credentials=creds)
            self._sheet = self._service.spreadsheets()
            self._initialized = True
            logger.info(f"Google Sheets service initialized successfully with service account email: {creds.service_account_email}")
            
        except Exception as e:
            logger.error(f"Failed to initialize Google Sheets service: {str(e)}", exc_info=True)
            raise

    def _ensure_initialized(self):
        """Ensure the service is initialized"""
        if not self._initialized:
            self._initialize_service()

    @property
    def service(self):
        """Get the Google Sheets service (lazy initialization)"""
        self._ensure_initialized()
        return self._service

    @property
    def sheet(self):
        """Get the Google Sheets spreadsheet service (lazy initialization)"""
        self._ensure_initialized()
        return self._sheet

    @retry_google_sheets_api(
        max_attempts=GOOGLE_SHEETS_MAX_RETRIES,
        base_wait=GOOGLE_SHEETS_BASE_WAIT,
        max_wait=GOOGLE_SHEETS_MAX_WAIT
    )
    def get_range_from_sheet(self, sheet_id: str, range_name: str) -> List[List[str]]:
        """
        Fetch a specific range from a given sheet (raw values) with retry logic
        Args:
            sheet_id (str): The Google Sheet ID
            range_name (str): The A1 notation range to fetch (e.g., 'B7:G11')
        Returns:
            List[List[str]]: 2D list of cell values
        """
        try:
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

    def get_schedule_range(self, range_name: str = None) -> List[List[str]]:
        """
        Fetch a specific range from the schedule sheet (raw values)
        Args:
            range_name (str): The A1 notation range to fetch (e.g., 'B7:G11')
        Returns:
            List[List[str]]: 2D list of cell values
        """
        return self.get_range_from_sheet(
            SCHEDULE_SHEET_ID, range_name or SCHEDULE_DEFAULT_RANGE
        )

    @retry_google_sheets_api(
        max_attempts=GOOGLE_SHEETS_MAX_RETRIES,
        base_wait=GOOGLE_SHEETS_BASE_WAIT,
        max_wait=GOOGLE_SHEETS_MAX_WAIT
    )
    def get_signup_form_submissions(
        self, range_name: str = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch all form submissions from the signups sheet with retry logic
        Returns:
            List[Dict[str, Any]]: List of form submissions with field mappings
        """
        try:
            logger.info(f"Fetching signups from sheet {SIGNUPS_SHEET_ID} with range {range_name or SIGNUPS_DEFAULT_RANGE}")
            result = (
                self.sheet.values()
                .get(
                    spreadsheetId=SIGNUPS_SHEET_ID,
                    range=range_name or SIGNUPS_DEFAULT_RANGE,
                )
                .execute()
            )
            values = result.get("values", [])

            # Map column headers to field names
            headers = [
                "timestamp",
                "email",
                "full_name",
                "location",
                "phone",
                "position",
                "availability",
                "start_date",
                "commitment_duration",
                "teaching_experience",
                "experience_details",
                "teaching_certificate",
                "vietnamese_speaking",
                "other_support",
                "additional_info",
                "referral_source",
                "guidelines_acknowledged",
                "confirmation_email_sent",
            ]

            # Process each row into a dictionary
            submissions = []
            for row in values:
                # Pad row with empty strings if it's shorter than headers
                row_data = row + [""] * (len(headers) - len(row))
                submission = dict(zip(headers, row_data))

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

            return submissions

        except ssl.SSLEOFError as e:
            log_ssl_error(e, "get_signup_form_submissions")
            logger.error(f"SSL EOF error while fetching form submissions: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Failed to fetch form submissions: {str(e)}", exc_info=True)
            raise

    def update_confirmation_status(self, email: str, status: bool = True) -> bool:
        """
        Update the confirmation email status for a volunteer in the Google Sheet

        Args:
            email (str): The email address of the volunteer
            status (bool): Whether the confirmation email was sent (True) or not (False)

        Returns:
            bool: True if update was successful, False otherwise
        """
        try:
            # First, find the row with the matching email
            result = (
                self.sheet.values()
                .get(spreadsheetId=SIGNUPS_SHEET_ID, range=SIGNUPS_DEFAULT_RANGE)
                .execute()
            )
            values = result.get("values", [])

            # Find the row index (1-based) for the email
            email_column_index = 1  # Email is in column B (index 1)
            row_index = None
            for i, row in enumerate(values):
                if len(row) > email_column_index and row[email_column_index] == email:
                    row_index = i + 1  # Convert to 1-based index
                    break

            if row_index is None:
                logger.warning(f"Email {email} not found in signups sheet")
                return False

            # Update the confirmation email status column (column R, index 17)
            confirmation_column = "R"
            cell_range = f"{confirmation_column}{row_index}"

            # Convert boolean to string for Google Sheets
            status_value = "TRUE" if status else "FALSE"

            body = {"values": [[status_value]]}

            result = (
                self.sheet.values()
                .update(
                    spreadsheetId=SIGNUPS_SHEET_ID,
                    range=cell_range,
                    valueInputOption="RAW",
                    body=body,
                )
                .execute()
            )

            logger.info(f"Updated confirmation status for {email} to {status}")
            return True

        except Exception as e:
            logger.error(
                f"Failed to update confirmation status for {email}: {str(e)}",
                exc_info=True,
            )
            return False

    def update_unsubscribe_status(self, email: str, unsubscribed: bool = True) -> bool:
        """
        Update the unsubscribe status for a volunteer in the Google Sheet
        This is optional and can be used to keep Google Sheets in sync

        Args:
            email (str): The email address of the volunteer
            unsubscribed (bool): Whether the volunteer has unsubscribed (True) or not (False)

        Returns:
            bool: True if update was successful, False otherwise
        """
        try:
            # First, find the row with the matching email
            result = (
                self.sheet.values()
                .get(spreadsheetId=SIGNUPS_SHEET_ID, range=SIGNUPS_DEFAULT_RANGE)
                .execute()
            )
            values = result.get("values", [])

            # Find the row index (1-based) for the email
            email_column_index = 1  # Email is in column B (index 1)
            row_index = None
            for i, row in enumerate(values):
                if len(row) > email_column_index and row[email_column_index] == email:
                    row_index = i + 1  # Convert to 1-based index
                    break

            if row_index is None:
                logger.warning(f"Email {email} not found in signups sheet")
                return False

            # You could add a new column for unsubscribe status
            # For now, we'll just log the action
            logger.info(
                f"Would update unsubscribe status for {email} to {unsubscribed} in Google Sheets, not implemented for now"
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to update unsubscribe status for {email}: {str(e)}",
                exc_info=True,
            )
            return False

    def create_sheet_from_template(
        self, template_sheet_name: str, new_sheet_date: datetime
    ) -> str:
        """
        Create a new sheet from a template, named 'Schedule MM/DD', and insert it after the last schedule sheet.
        """
        try:
            sheet_metadata = self.sheet.get(spreadsheetId=SCHEDULE_SHEET_ID).execute()
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

            new_sheet_title = f"Schedule {new_sheet_date.strftime('%m/%d')}"

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
                spreadsheetId=SCHEDULE_SHEET_ID, body=copy_request
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

    def hide_sheet(self, sheet_name: str):
        """
        Hide a sheet by name
        Args:
            sheet_name: Name of the sheet to hide (in format Schedule MM/DD/YYYY)
        """
        try:
            sheet_metadata = self.sheet.get(spreadsheetId=SCHEDULE_SHEET_ID).execute()
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
                spreadsheetId=SCHEDULE_SHEET_ID, body=request
            ).execute()
            logger.info(f"Successfully hidden sheet: {sheet_name}")
        except Exception as e:
            logger.error(f"Failed to hide sheet: {str(e)}", exc_info=True)
            raise

    def update_sheet_dates(self, sheet_date: datetime):
        """
        Update the blue header and table header dates in the new sheet.
        """
        try:
            sheet_title = f"Schedule {sheet_date.strftime('%m/%d')}"
            sheet_metadata = self.sheet.get(spreadsheetId=SCHEDULE_SHEET_ID).execute()
            sheet_id = next(
                (
                    s["properties"]["sheetId"]
                    for s in sheet_metadata["sheets"]
                    if s["properties"]["title"] == sheet_title
                ),
                None,
            )
            if not sheet_id:
                raise ValueError(f"Sheet {sheet_title} not found")

            # Update blue header (assuming it's cell C1)
            blue_header_range = f"{sheet_title}!C1"
            self.sheet.values().update(
                spreadsheetId=SCHEDULE_SHEET_ID,
                range=blue_header_range,
                valueInputOption="USER_ENTERED",
                body={"values": [[sheet_title]]},
            ).execute()

            self.sheet.values().update(
                spreadsheetId=SCHEDULE_SHEET_ID,
                range=f"{sheet_title}!B1",
                valueInputOption="USER_ENTERED",
                body={
                    "values": [[f"Schedule for Week {sheet_date.strftime('%m/%d')}"]]
                },
            ).execute()

            # Update table header dates for each class
            dates = [
                (sheet_date + timedelta(days=i)).strftime("%m/%d") for i in range(5)
            ]
            for class_name, config in CLASS_CONFIG.items():
                # Get the start cell for the header row (e.g., B7)
                start_cell = config["sheet_range"].split(":")[0]
                header_range = (
                    f"{sheet_title}!{start_cell}:G{start_cell[1:]}"  # e.g., B7:G7
                )
                # Fetch the current values to preserve the first column
                values = self.get_range_from_sheet(SCHEDULE_SHEET_ID, header_range)
                if values and len(values) > 0:
                    header_row = values[0]
                    new_header = [header_row[0]] + dates
                    self.sheet.values().update(
                        spreadsheetId=SCHEDULE_SHEET_ID,
                        range=header_range,
                        valueInputOption="USER_ENTERED",
                        body={"values": [new_header]},
                    ).execute()
                    logger.info(f"Updated {header_range} to {new_header}")
            logger.info(f"Successfully updated dates in sheet {sheet_title}")
        except Exception as e:
            logger.error(f"Failed to update sheet dates: {str(e)}", exc_info=True)
            raise

    def get_sheet_metadata(self) -> Dict:
        """Get metadata for all sheets in the spreadsheet"""
        try:
            return self.sheet.get(spreadsheetId=SCHEDULE_SHEET_ID).execute()
        except Exception as e:
            logger.error(f"Failed to get sheet metadata: {str(e)}", exc_info=True)
            raise

    def get_schedule_sheets(self) -> List[Dict]:
        """Get all schedule sheets and their metadata"""
        metadata = self.get_sheet_metadata()
        return [
            sheet
            for sheet in metadata["sheets"]
            if sheet["properties"]["title"].startswith("Schedule ")
        ]

    def get_sheet_by_date(self, date: datetime) -> Optional[Dict]:
        """Get sheet metadata for a specific date"""
        sheet_name = f"Schedule {date.strftime('%m/%d')}"
        sheets = self.get_schedule_sheets()
        return next((s for s in sheets if s["properties"]["title"] == sheet_name), None)

    def get_current_schedule_dates(self) -> tuple[datetime, datetime]:
        """
        Get the Monday and Friday dates from the current visible schedule sheet.

        Returns:
            tuple[datetime, datetime]: (monday_date, friday_date) from the current schedule
        """
        try:
            # Get all schedule sheets
            schedule_sheets = self.get_schedule_sheets()

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

            # Extract date from sheet title (format: "Schedule MM/DD")
            sheet_title = visible_sheet["properties"]["title"]
            date_str = sheet_title.replace("Schedule ", "")

            try:
                # Parse the date (MM/DD format)
                sheet_date = datetime.strptime(date_str, "%m/%d")
                # Set the year to current year
                current_year = datetime.now().year
                sheet_date = sheet_date.replace(year=current_year)

                # Calculate Monday and Friday for this week
                days_since_monday = sheet_date.weekday()
                monday_date = sheet_date - timedelta(days=days_since_monday)
                friday_date = monday_date + timedelta(days=4)

                logger.info(
                    f"Extracted dates from sheet '{sheet_title}': Monday {monday_date.strftime('%Y-%m-%d')}, Friday {friday_date.strftime('%Y-%m-%d')}"
                )
                return monday_date, friday_date

            except ValueError as e:
                logger.warning(
                    f"Could not parse date from sheet title '{sheet_title}': {e}"
                )
                # Fallback to calculated dates
                now = datetime.now()
                days_since_monday = now.weekday()
                current_monday = now - timedelta(days=days_since_monday)
                current_friday = current_monday + timedelta(days=4)
                return current_monday, current_friday

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

    def set_sheet_visibility(self, sheet_id: int, hidden: bool):
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
                spreadsheetId=SCHEDULE_SHEET_ID, body=request
            ).execute()
            logger.info(
                f"Set sheet {sheet_id} visibility to {'hidden' if hidden else 'visible'}"
            )
        except Exception as e:
            logger.error(f"Failed to set sheet visibility: {str(e)}", exc_info=True)
            raise

    def move_sheet(self, sheet_id: int, new_index: int):
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
                spreadsheetId=SCHEDULE_SHEET_ID, body=request
            ).execute()
            logger.info(f"Moved sheet {sheet_id} to index {new_index}")
        except Exception as e:
            logger.error(f"Failed to move sheet: {str(e)}", exc_info=True)
            raise

    def rotate_schedule_sheets(self) -> Dict[str, Any]:
        """
        Rotate schedule sheets.

        Returns:
            Dict[str, Any]: Detailed information about the rotation operation including:
                - changes: What sheets were added, hidden, unhidden, reordered
                - current_state: Current visible and hidden sheets
                - display_dates: The dates that should be displayed
        """
        try:
            # Get current date and time in Vietnam timezone
            now = datetime.now()

            days_since_monday = now.weekday()
            current_monday = now - timedelta(days=days_since_monday)

            # Get all existing schedule sheets before rotation
            existing_sheets = self.get_schedule_sheets()
            before_state = {
                sheet["properties"]["title"]: {
                    "hidden": sheet["properties"].get("hidden", False),
                    "index": sheet["properties"].get("index", 0),
                }
                for sheet in existing_sheets
            }

            # Calculate the date range to display
            # Start from next Monday since current week is over
            next_monday = current_monday + timedelta(days=7)
            display_dates = [
                next_monday + timedelta(days=7 * i)
                for i in range(SCHEDULE_SHEETS_DISPLAY_WEEKS_COUNT)
            ]

            # Create a set of sheet names that should be visible
            visible_sheet_names = {
                f"Schedule {date.strftime('%m/%d')}" for date in display_dates
            }

            # Track what we're doing
            sheets_created = []
            sheets_made_visible = []
            sheets_made_hidden = []
            sheets_reordered = []

            # Process each date in the display range
            for i, date in enumerate(display_dates):
                sheet_name = f"Schedule {date.strftime('%m/%d')}"
                existing_sheet = next(
                    (
                        s
                        for s in existing_sheets
                        if s["properties"]["title"] == sheet_name
                    ),
                    None,
                )

                if existing_sheet:
                    # Sheet exists, ensure it's visible and in the correct position
                    was_hidden = existing_sheet["properties"].get("hidden", False)
                    old_index = existing_sheet["properties"].get("index", 0)

                    self.set_sheet_visibility(
                        existing_sheet["properties"]["sheetId"], False
                    )  # False = visible
                    self.move_sheet(
                        existing_sheet["properties"]["sheetId"], i + 1
                    )  # +1 to account for template sheet

                    # Track changes
                    if was_hidden:
                        sheets_made_visible.append(sheet_name)
                    if old_index != i + 1:
                        sheets_reordered.append(sheet_name)
                else:
                    # Create new sheet and ensure it's visible
                    new_sheet_id = self.create_sheet_from_template(
                        "Schedule Template", date
                    )
                    self.update_sheet_dates(date)
                    self.set_sheet_visibility(new_sheet_id, False)  # False = visible
                    self.move_sheet(new_sheet_id, i + 1)  # Move to correct position
                    sheets_created.append(sheet_name)

            # Handle visibility for all sheets
            for sheet in existing_sheets:
                title = sheet["properties"]["title"]
                # Skip template sheet and any other non-date sheets
                if not title.startswith("Schedule ") or title == "Schedule Template":
                    continue

                try:
                    # Set visibility based on whether the sheet should be in the display range
                    should_be_visible = title in visible_sheet_names
                    current_visibility = not sheet["properties"].get(
                        "hidden", False
                    )  # Convert hidden to visible

                    # Only update if visibility needs to change
                    if current_visibility != should_be_visible:
                        self.set_sheet_visibility(
                            sheet["properties"]["sheetId"], not should_be_visible
                        )  # True = hidden

                        # Track the change
                        if should_be_visible:
                            sheets_made_visible.append(title)
                        else:
                            sheets_made_hidden.append(title)

                        logger.info(
                            f"Set sheet {title} visibility to {'visible' if should_be_visible else 'hidden'}"
                        )
                except ValueError:
                    # Skip sheets that don't match the date format
                    logger.warning(f"Skipping sheet with invalid date format: {title}")
                    continue

            # Get final state after all changes
            final_sheets = self.get_schedule_sheets()
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
                "display_dates": [date.strftime("%m/%d") for date in display_dates],
                "display_weeks_count": SCHEDULE_SHEETS_DISPLAY_WEEKS_COUNT,
            }

            logger.info(
                f"Successfully rotated schedule sheets for {SCHEDULE_SHEETS_DISPLAY_WEEKS_COUNT} weeks"
            )
            return result

        except Exception as e:
            logger.error(f"Failed to rotate schedule sheets: {str(e)}", exc_info=True)
            raise


# Create a singleton instance
sheets_service = GoogleSheetsService()
