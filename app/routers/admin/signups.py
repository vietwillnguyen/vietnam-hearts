"""
Admin form-submission / volunteer sync endpoints
"""

import json
import os
import re
import ssl
import time
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

import google.generativeai as genai

from app.database import get_db
from app.models import Volunteer as VolunteerModel
from app.services.google_sheets import sheets_service
from app.services.email_service import email_service
from app.utils.config_helper import ConfigHelper
from app.utils.logging_config import get_api_logger
from app.utils.retry_utils import log_ssl_error
from app.routers.admin.helpers import create_new_volunteer_object

logger = get_api_logger()

router = APIRouter()

_JUDGE_PROMPT_TEMPLATE = """You are an applicant reviewer for Vietnam Hearts, a children's education charity \
that places volunteers with Vietnamese children. Your job is to do an initial screen \
of applicants before a human does a full review.

Reply ONLY with valid JSON (no markdown):
{{
  "summary": "<2-3 sentence summary of what the applicant submitted>",
  "rating": <integer 1-10>,
  "verdict": "<ACCEPTED or REJECTED>",
  "reasoning": "<one sentence>"
}}

RATING CRITERIA (in order of importance):
1. Identity docs submitted: passport_upload and headshot_upload must both be \
non-empty links — missing either is an automatic 1/10 and REJECTED.
2. Social media link submitted: must be a non-empty link — missing is heavily \
penalised (-3 points).

SAFEGUARDING GATE (evaluated separately — does NOT affect the numeric rating):
Scan free-form answers (experience_details, other_support, referral_source) for \
explicit red flags: grooming language, predatory interest in children, or statements \
suggesting inappropriate contact. Any such red flag → REJECTED regardless of score.

VERDICT: ACCEPTED if rating >= 6 AND no safeguarding red flags. REJECTED otherwise.

Applicant:
Name: {first_name} {last_name}
Passport upload: {passport_upload}
Headshot upload: {headshot_upload}
Social media link: {social_media_link}
Position interest: {position_interest}
Teaching experience: {teaching_experience}
Experience details: {experience_details}
Motivation for volunteering: {motivation}
Expected gain: {expected_gain}
Prior experience with children: {children_experience}
Other support offered: {other_support}
Referral source: {referral_source}"""


def _judge_submission(submission: dict) -> dict:
    """
    Call Gemini to judge a single volunteer submission.

    Returns a dict with keys: summary, rating, verdict, reasoning.
    Raises ValueError if the response cannot be parsed.
    Raises RuntimeError if GEMINI_API_KEY is not configured.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set — cannot call LLM judge")

    try:
        genai.configure(api_key=api_key)
    except Exception as e:
        raise RuntimeError(f"Failed to configure Gemini: {e}") from e

    def _field(key: str) -> str:
        return submission.get(key, "").strip() or "(not provided)"

    prompt = _JUDGE_PROMPT_TEMPLATE.format(
        first_name=submission.get("first_name", ""),
        last_name=submission.get("last_name", ""),
        passport_upload=_field("passport_upload"),
        headshot_upload=_field("headshot_upload"),
        social_media_link=_field("social_media_link"),
        position_interest=_field("position_interest"),
        teaching_experience=_field("teaching_experience"),
        experience_details=_field("experience_details"),
        motivation=_field("motivation"),
        expected_gain=_field("expected_gain"),
        children_experience=_field("children_experience"),
        other_support=_field("other_support"),
        referral_source=_field("referral_source"),
    )

    model = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content(prompt)
    raw_text = response.text.strip()

    # Strip markdown fences if present (```json ... ```)
    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE)
    raw_text = re.sub(r"\s*```$", "", raw_text)

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse Gemini response as JSON: {e}\nRaw: {raw_text}") from e

    return result


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


def _run_llm_judge(db: Session, limit: int) -> dict:
    """
    Inner loop for LLM judgment. Reads PENDING rows, calls Gemini, writes verdicts back.
    Shared by both the manual judge endpoint and the combined review-and-sync cron endpoint.
    """
    dry_run = ConfigHelper.get_dry_run(db)
    all_pending = sheets_service.get_pending_submissions_with_rows(db)
    pending_rows = all_pending[:limit]
    logger.info(f"LLM judge: {len(all_pending)} pending, processing {len(pending_rows)} (dry_run={dry_run})")

    processed = accepted = rejected = errors = 0

    for row_number, submission in pending_rows:
        email = submission.get("email_address", "unknown")
        try:
            judgment = _judge_submission(submission)
            status = judgment.get("verdict", "REJECTED").upper()
            summary = judgment.get("summary", "")
            rating = int(judgment.get("rating", 0))

            logger.info(
                f"Judgment row {row_number} ({email}): "
                f"{status} rating={rating} — {judgment.get('reasoning', '')}"
            )

            if dry_run:
                logger.info(f"[DRY RUN] Would write row {row_number}: {status} {rating}/10")
            else:
                sheets_service.update_submission_judgment(
                    db=db, row_number=row_number,
                    status=status, summary=summary, rating=rating,
                    reasoning=judgment.get("reasoning", ""),
                )

            processed += 1
            if status == "ACCEPTED":
                accepted += 1
            else:
                rejected += 1

        except Exception as e:
            logger.error(f"Failed to judge row {row_number} ({email}): {e}", exc_info=True)
            errors += 1

        # Gemini 2.5 Flash Tier 1: 1K RPM — 1s gap is safe.
        # If on the free tier (15 RPM), increase this to 4s.
        time.sleep(1)

    return {
        "dry_run": dry_run,
        "processed": processed,
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors,
        "total_pending": len(all_pending),
        "remaining": max(0, len(all_pending) - limit),
    }


@router.post(
    "/judge-pending-submissions",
    summary="LLM judge for pending volunteer submissions (manual)",
    description=(
        "Manually trigger the LLM judge. For the automated cron flow use /review-and-sync."
    ),
)
def judge_pending_submissions(request: Request, db: Session = Depends(get_db), limit: int = 20):
    result = _run_llm_judge(db, limit)
    logger.info(f"Manual judge run complete: {result}")
    return {"status": "success", **result}


@router.post(
    "/review-and-sync",
    summary="Cron job: LLM judge pending submissions then sync accepted ones to DB",
    description=(
        "Full pipeline for the scheduled cron job:\n"
        "1. LLM judges up to `limit` PENDING submissions and writes ACCEPTED/REJECTED to the sheet.\n"
        "2. Syncs all ACCEPTED submissions from the sheet into the volunteer database and sends confirmation emails.\n"
        "Respects dry_run — when True, step 1 logs only and step 2 still syncs (it reads the sheet state)."
    ),
)
def review_and_sync(request: Request, db: Session = Depends(get_db), limit: int = 20):
    logger.info(f"Starting review-and-sync cron run (limit={limit})")

    # Step 1: LLM judge — writes verdicts to sheet col A + C
    judge_result = _run_llm_judge(db, limit)
    logger.info(f"Judge step done: {judge_result}")

    # Step 2: Sync — re-reads sheet, imports newly ACCEPTED rows into DB, sends confirmation emails
    sync_result = get_signup_form_submissions(db=db, process_new=True)
    sync_status = sync_result.get("status", "unknown")
    sync_details = sync_result.get("details", {})
    logger.info(f"Sync step done: status={sync_status} details={sync_details}")

    return {
        "status": "success",
        "judge": judge_result,
        "sync": {
            "status": sync_status,
            "volunteers_created": sync_details.get("volunteers_created", 0),
            "accepted_submissions": sync_details.get("accepted_submissions", 0),
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
