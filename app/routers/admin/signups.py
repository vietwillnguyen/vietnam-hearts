"""
Admin form-submission / volunteer sync endpoints
"""

import ssl
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Volunteer as VolunteerModel
from app.services.google_sheets import sheets_service
from app.services.email_service import email_service
from app.utils.logging_config import get_api_logger
from app.utils.retry_utils import log_ssl_error
from app.routers.admin.helpers import create_new_volunteer_object

logger = get_api_logger()

router = APIRouter()


@router.get(
    "/forms/submissions",
    summary="Get form submissions",
    description="Fetches and optionally processes volunteer signup form submissions, syncing to the database and sending confirmation emails to new volunteers",
)
def get_signup_form_submissions(db: Session = Depends(get_db), process_new: bool = True):
    """
    Fetch and optionally process volunteer signup form submissions from Google Sheets.
    """
    try:
        logger.info("Fetching form submissions from Google Sheets...")
        submissions = sheets_service.get_signup_form_submissions(db)
        logger.info(f"Found {len(submissions)} form submissions from Google Sheets")

        new_submissions = []
        new_volunteers = []
        failed_submissions = []
        has_empty_emails = False

        total_submissions = len(submissions)
        accepted_submissions = len([s for s in submissions if s.get("applicant_status", "").upper() == "ACCEPTED"])
        non_accepted_submissions = total_submissions - accepted_submissions

        if process_new:
            existing_emails = set(email[0] for email in db.query(VolunteerModel.email).all())
            logger.info(f"Found {len(existing_emails)} existing emails in database")

            new_submissions = [
                s for s in submissions
                if (
                    s["email_address"] not in existing_emails
                    and s.get("applicant_status", "").upper() == "ACCEPTED"
                    and s.get("email_address", "").strip()
                )
            ]

            valid_accepted = len([s for s in submissions if s.get("applicant_status", "").upper() == "ACCEPTED" and s.get("email_address", "").strip()])
            valid_non_accepted = len([s for s in submissions if s.get("applicant_status", "").upper() != "ACCEPTED" and s.get("email_address", "").strip()])
            has_empty_emails = any(not s.get("email_address", "").strip() for s in submissions)

            logger.info(f"Status filtering: {total_submissions} total, {accepted_submissions} accepted, {non_accepted_submissions} non-accepted")
            logger.info(f"After email validation: {valid_accepted} valid accepted, {valid_non_accepted} valid non-accepted")

            if non_accepted_submissions > 0:
                logger.info(f"Skipped non-accepted: {[{'email': s.get('email_address'), 'status': s.get('applicant_status')} for s in submissions if s.get('applicant_status', '').upper() != 'ACCEPTED']}")

            logger.info(f"Found {len(new_submissions)} new submissions to process")

            for submission in new_submissions:
                try:
                    new_volunteers.append(create_new_volunteer_object(submission))
                except Exception as e:
                    logger.error(f"Failed to process submission for {submission.get('email_address', 'unknown')}: {str(e)}")
                    failed_submissions.append({"email": submission.get("email_address", "unknown"), "error": str(e)})

            if new_volunteers:
                try:
                    db.bulk_save_objects(new_volunteers)
                    db.commit()
                    logger.info(f"Added {len(new_volunteers)} new volunteers to database")
                    for v in new_volunteers:
                        logger.info(f"New Volunteer {v.email} created with id: {v.id}")
                except Exception as e:
                    logger.error(f"Failed to save new volunteers to database: {str(e)}")
                    db.rollback()
                    return {
                        "status": "partial_failure",
                        "message": f"Retrieved {len(submissions)} form submissions but failed to save new volunteers",
                        "data": submissions,
                        "details": {
                            "submissions_retrieved": len(submissions),
                            "accepted_submissions": accepted_submissions,
                            "non_accepted_submissions": non_accepted_submissions,
                            "new_submissions_found": len(new_submissions),
                            "volunteers_created": 0,
                            "failed_submissions": failed_submissions,
                            "database_error": str(e),
                        },
                    }

            try:
                email_service.send_confirmation_emails(db)
            except Exception as e:
                logger.error(f"Failed to send confirmation emails: {str(e)}")

        if failed_submissions:
            return {
                "status": "partial_failure",
                "message": f"Retrieved {len(submissions)} form submissions with some processing issues",
                "data": submissions,
                "details": {
                    "submissions_retrieved": len(submissions),
                    "accepted_submissions": accepted_submissions,
                    "non_accepted_submissions": non_accepted_submissions,
                    "new_submissions_found": len(new_submissions),
                    "volunteers_created": len(new_volunteers),
                    "failed_submissions": failed_submissions,
                },
            }

        if process_new and has_empty_emails:
            message = f"Retrieved {valid_accepted} form submissions ({valid_accepted} accepted, {valid_non_accepted} non-accepted)"
            details = {
                "submissions_retrieved": valid_accepted,
                "accepted_submissions": valid_accepted,
                "non_accepted_submissions": valid_non_accepted,
                "new_submissions_found": len(new_submissions),
                "volunteers_created": len(new_volunteers),
            }
        else:
            message = f"Retrieved {len(submissions)} form submissions ({accepted_submissions} accepted, {non_accepted_submissions} non-accepted)"
            details = {
                "submissions_retrieved": len(submissions),
                "accepted_submissions": accepted_submissions,
                "non_accepted_submissions": non_accepted_submissions,
                "new_submissions_found": len(new_submissions),
                "volunteers_created": len(new_volunteers),
            }

        return {"status": "success", "message": message, "data": submissions, "details": details}

    except ssl.SSLEOFError as e:
        log_ssl_error(e, "get_signup_form_submissions")
        return {
            "status": "partial_failure",
            "message": "Failed to fetch form submissions due to SSL connection issue",
            "data": [],
            "details": {
                "error_type": "ssl_eof_error",
                "error_message": str(e),
                "submissions_retrieved": 0,
                "accepted_submissions": 0,
                "non_accepted_submissions": 0,
                "new_submissions_found": 0,
                "volunteers_created": 0,
            },
        }
    except Exception as e:
        logger.error(f"Failed to fetch form submissions: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to fetch form submissions: {str(e)}",
            "data": [],
            "details": {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "submissions_retrieved": 0,
                "accepted_submissions": 0,
                "non_accepted_submissions": 0,
                "new_submissions_found": 0,
                "volunteers_created": 0,
            },
        }


@router.post("/sync-volunteers")
async def sync_volunteers(request: Request, db: Session = Depends(get_db)):
    """Sync volunteers from Google Sheets and process new signups"""
    try:
        result = get_signup_form_submissions(db=db, process_new=True)
        status = result.get("status")
        if status == "success":
            return {"status": "success", "message": "Volunteers synced successfully"}
        elif status == "partial_failure":
            return {"status": "partial_success", "message": "Volunteers synced with some issues", "details": result.get("details", {})}
        return {"status": "error", "message": "Failed to sync volunteers", "details": result.get("details", {})}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in sync volunteers: {str(e)}", exc_info=True)
        return {"status": "error", "message": "Failed to sync volunteers due to unexpected error", "details": {"error": str(e)}}
