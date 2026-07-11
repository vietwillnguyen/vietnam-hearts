"""
Admin email management endpoints
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, timedelta

from app.database import get_db, get_db_session
from app.models import (
    Volunteer as VolunteerModel,
    EmailCommunication as EmailCommunicationModel,
)
from app.services.email_service import email_service
from app.services.google_sheets import sheets_service
from app.utils.logging_config import get_api_logger
from app.utils.config_helper import ConfigHelper
from app.routers.admin.helpers import get_email_summary

logger = get_api_logger()

router = APIRouter()


@router.get("/email-logs")
def view_email_logs(db: Session = Depends(get_db)):
    """View all email communications"""
    try:
        communications = db.query(EmailCommunicationModel).options(
            joinedload(EmailCommunicationModel.volunteer)
        ).all()
        email_data = get_email_summary(communications)
        return {"status": "success", "total_emails": len(email_data), "emails": email_data}
    except Exception as e:
        logger.error(f"Failed to fetch email logs: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch email logs: {str(e)}")


@router.get("/reminder-stats")
def get_reminder_stats(db: Session = Depends(get_db)):
    """Get statistics about weekly reminder emails"""
    try:
        reminder_emails = (
            db.query(EmailCommunicationModel)
            .filter(EmailCommunicationModel.email_type == "weekly_reminder")
            .all()
        )
        total_sent = len(reminder_emails)
        successful = sum(1 for e in reminder_emails if e.status == "sent")
        failed = sum(1 for e in reminder_emails if e.status == "failed")

        four_weeks_ago = datetime.now() - timedelta(weeks=4)
        recent_emails = [e for e in reminder_emails if e.sent_at and e.sent_at >= four_weeks_ago]

        weekly_stats = {}
        for email in recent_emails:
            week_start = email.sent_at - timedelta(days=email.sent_at.weekday())
            week_key = week_start.strftime("%Y-%m-%d")
            if week_key not in weekly_stats:
                weekly_stats[week_key] = {"total": 0, "successful": 0, "failed": 0}
            weekly_stats[week_key]["total"] += 1
            if email.status == "sent":
                weekly_stats[week_key]["successful"] += 1
            elif email.status == "failed":
                weekly_stats[week_key]["failed"] += 1

        return {
            "status": "success",
            "statistics": {
                "total_sent": total_sent,
                "successful": successful,
                "failed": failed,
                "success_rate": (successful / total_sent * 100) if total_sent > 0 else 0,
                "weekly_stats": weekly_stats,
            },
        }
    except Exception as e:
        logger.error(f"Failed to get reminder statistics: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get reminder statistics: {str(e)}")


@router.post("/send-confirmation-emails")
async def send_confirmation_emails(request: Request, db: Session = Depends(get_db)):
    """Bulk send confirmation emails to new volunteers"""
    try:
        email_service.send_confirmation_emails(db)
        return {"status": "success", "message": "Confirmation emails sent successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in send confirmation emails: {str(e)}", exc_info=True)
        return {"status": "error", "message": "Failed to send confirmation emails due to unexpected error", "details": {"error": str(e)}}


@router.post("/send-weekly-reminders")
async def send_weekly_reminder_emails(request: Request):
    """Send weekly reminder emails to all active subscribed volunteers.

    Skips the send (returning status "skipped") if weekly reminders are
    disabled globally, or if no class has an open teacher/head TA/assistant
    slot for the current week.
    """
    try:
        with get_db_session() as db:
            if not ConfigHelper.get_weekly_reminders_enabled(db):
                logger.warning("Weekly reminders are globally disabled, skipping bulk send")
                return {
                    "status": "skipped",
                    "message": "Weekly reminders are currently disabled globally. Enable them in the admin settings to send weekly reminders.",
                }

            class_tables = [
                email_service.build_class_table(block)
                for block in sheets_service.get_schedule_blocks(db)
            ]
            current_monday, current_friday = sheets_service.get_current_schedule_dates(db)
            subject = email_service.get_reminder_subject(current_monday, current_friday)

            if not any(ct.get("needs_volunteers") for ct in class_tables):
                logger.info("No open volunteer slots this week, skipping weekly reminder emails")
                return {
                    "status": "skipped",
                    "message": "No volunteer slots need filling this week, weekly reminder emails were not sent.",
                }

            if ConfigHelper.get_dry_run(db):
                logger.info("DRY_RUN is enabled, sending emails to dry run email recipient only")
                dry_run_volunteer = (
                    db.query(VolunteerModel)
                    .filter(VolunteerModel.email == ConfigHelper.get_dry_run_email_recipient(db))
                    .first()
                )
                volunteers = [{"email": dry_run_volunteer.email, "name": dry_run_volunteer.name}]
                volunteer_lookup = {dry_run_volunteer.email: dry_run_volunteer}
            else:
                db_volunteers = (
                    db.query(VolunteerModel)
                    .filter(VolunteerModel.is_active == True)
                    .filter(VolunteerModel.weekly_reminders_subscribed == True)
                    .all()
                )
                volunteers = [{"email": v.email, "name": v.name} for v in db_volunteers]
                volunteer_lookup = {v.email: v for v in db_volunteers}

            logger.info(f"Sending weekly reminder emails to {len(volunteers)} volunteers")

            email_communications = []
            for volunteer in volunteers:
                db_volunteer = volunteer_lookup.get(volunteer["email"])
                if not db_volunteer:
                    logger.warning(f"Volunteer {volunteer['email']} not found in database, skipping")
                    continue

                if not db_volunteer.email_unsubscribe_token:
                    db_volunteer.email_unsubscribe_token = email_service.generate_unsubscribe_token()
                    db.commit()

                first_name = db_volunteer.name.split()[0] if db_volunteer.name else "Volunteer"
                html_body = email_service.email_env.get_template("weekly-reminder-email.html").render(
                    first_name=first_name,
                    class_tables=[ct["table_html"] for ct in class_tables],
                    SCHEDULE_SIGNUP_LINK=ConfigHelper.get_schedule_signup_link(db) or "#",
                    EMAIL_PREFERENCES_LINK=email_service.get_volunteer_unsubscribe_link(db_volunteer, db),
                    INVITE_LINK_ZALO=ConfigHelper.get_invite_link_zalo(db) or "#",
                    ONBOARDING_GUIDE_LINK=ConfigHelper.get_onboarding_guide_link(db) or "#",
                    INSTAGRAM_LINK=ConfigHelper.get_instagram_link(db) or "#",
                    FACEBOOK_PAGE_LINK=ConfigHelper.get_facebook_page_link(db) or "#",
                )

                email_comm = EmailCommunicationModel(
                    volunteer_id=db_volunteer.id,
                    recipient_email=volunteer["email"],
                    email_type="weekly_reminder",
                    subject=subject,
                    status="PENDING",
                )
                email_communications.append((email_comm, html_body))

            for email_comm, _ in email_communications:
                db.add(email_comm)
            db.commit()

            for email_comm, html_body in email_communications:
                try:
                    email_service.send_custom_email(
                        to_email=email_comm.recipient_email,
                        subject=email_comm.subject,
                        html_body=html_body,
                    )
                    email_comm.status = "sent"
                    email_comm.sent_at = datetime.now()
                except Exception as e:
                    logger.error(f"Failed to send weekly reminder to {email_comm.recipient_email}: {str(e)}")
                    email_comm.status = "failed"
                    email_comm.error_message = str(e)

            db.commit()

        return {"status": "success", "message": "Weekly reminder emails sent successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in send weekly reminder emails: {str(e)}", exc_info=True)
        return {"status": "error", "message": "Failed to send weekly reminder emails due to unexpected error", "details": {"error": str(e)}}
