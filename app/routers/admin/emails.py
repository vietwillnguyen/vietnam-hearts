"""
Admin email management endpoints
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

from app.database import get_db, get_db_session
from app.models import (
    EmailCommunication as EmailCommunicationModel,
)
from app.models import (
    Volunteer as VolunteerModel,
)
from app.routers.admin.helpers import (
    ABANDONED_PENDING_REMINDER_AGE,
    get_email_summary,
    get_weekly_reminder_run_health,
)
from app.services.email_service import email_service
from app.services.google_sheets import sheets_service
from app.utils.config_helper import ConfigHelper
from app.utils.logging_config import get_api_logger

logger = get_api_logger()

router = APIRouter()


@router.get("/email-logs")
def view_email_logs(db: Session = Depends(get_db)):
    """View all email communications"""
    try:
        communications = (
            db.query(EmailCommunicationModel)
            .options(joinedload(EmailCommunicationModel.volunteer))
            .all()
        )
        email_data = get_email_summary(communications)
        return {
            "status": "success",
            "total_emails": len(email_data),
            "emails": email_data,
        }
    except Exception as e:
        logger.error(f"Failed to fetch email logs: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch email logs: {str(e)}"
        ) from e


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
        recent_emails = [
            e for e in reminder_emails if e.sent_at and e.sent_at >= four_weeks_ago
        ]

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
                "success_rate": (successful / total_sent * 100)
                if total_sent > 0
                else 0,
                "weekly_stats": weekly_stats,
            },
        }
    except Exception as e:
        logger.error(f"Failed to get reminder statistics: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get reminder statistics: {str(e)}"
        ) from e


@router.post("/send-confirmation-emails")
async def send_confirmation_emails(request: Request, db: Session = Depends(get_db)):
    """Bulk send confirmation emails to new volunteers"""
    try:
        email_service.send_confirmation_emails(db)
        return {"status": "success", "message": "Confirmation emails sent successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in send confirmation emails: {str(e)}", exc_info=True
        )
        return {
            "status": "error",
            "message": "Failed to send confirmation emails due to unexpected error",
            "details": {"error": str(e)},
        }


def fail_abandoned_pending_reminders(db: Session) -> int:
    """Resolve PENDING reminder rows left behind by a run that never finished.

    Rows are committed PENDING before the batch is sent so the run can be
    resumed, which means a run killed at the request deadline abandons them.
    They would otherwise sit PENDING forever and keep the run-health check
    reporting a failure that nothing can clear.
    """
    cutoff = datetime.now(UTC).replace(tzinfo=None) - ABANDONED_PENDING_REMINDER_AGE
    abandoned = (
        db.query(EmailCommunicationModel)
        .filter(
            EmailCommunicationModel.email_type == "weekly_reminder",
            EmailCommunicationModel.status == "PENDING",
            EmailCommunicationModel.created_at < cutoff,
        )
        .all()
    )
    if not abandoned:
        return 0

    for row in abandoned:
        row.status = "failed"
        row.error_message = (
            "abandoned: the run that queued this email did not finish sending it"
        )
    db.commit()
    logger.warning(
        f"Marked {len(abandoned)} abandoned PENDING weekly reminder rows as failed"
    )
    return len(abandoned)


@router.get("/weekly-reminder-health")
def weekly_reminder_health(db: Session = Depends(get_db)):
    """Report whether the Sunday weekly-reminder run is actually landing."""
    try:
        return get_weekly_reminder_run_health(db)
    except Exception as e:
        logger.error(
            f"Failed to compute weekly reminder health: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to compute weekly reminder health: {str(e)}",
        ) from e


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
                logger.warning(
                    "Weekly reminders are globally disabled, skipping bulk send"
                )
                return {
                    "status": "skipped",
                    "message": "Weekly reminders are currently disabled globally. Enable them in the admin settings to send weekly reminders.",
                }

            fail_abandoned_pending_reminders(db)

            teaching_days = ConfigHelper.get_schedule_teaching_days(db)
            current_monday, current_friday = sheets_service.get_current_schedule_dates(
                db
            )
            class_tables = [
                email_service.build_class_table(block, teaching_days, current_monday)
                for block in sheets_service.get_schedule_blocks(db)
            ]
            subject = email_service.get_reminder_subject(current_monday, current_friday)

            if not any(ct.get("needs_volunteers") for ct in class_tables):
                logger.info(
                    "No open volunteer slots this week, skipping weekly reminder emails"
                )
                return {
                    "status": "skipped",
                    "message": "No volunteer slots need filling this week, weekly reminder emails were not sent.",
                }

            if ConfigHelper.get_dry_run(db):
                logger.info(
                    "DRY_RUN is enabled, sending emails to dry run email recipient only"
                )
                dry_run_volunteer = (
                    db.query(VolunteerModel)
                    .filter(
                        VolunteerModel.email
                        == ConfigHelper.get_dry_run_email_recipient(db)
                    )
                    .first()
                )
                volunteers = [
                    {"email": dry_run_volunteer.email, "name": dry_run_volunteer.name}
                ]
                volunteer_lookup = {dry_run_volunteer.email: dry_run_volunteer}
            else:
                db_volunteers = (
                    db.query(VolunteerModel)
                    .filter(VolunteerModel.is_active.is_(True))
                    .filter(VolunteerModel.weekly_reminders_subscribed.is_(True))
                    .all()
                )
                volunteers = [{"email": v.email, "name": v.name} for v in db_volunteers]
                volunteer_lookup = {v.email: v for v in db_volunteers}

            already_sent = {
                row.recipient_email
                for row in db.query(EmailCommunicationModel.recipient_email)
                .filter(
                    EmailCommunicationModel.email_type == "weekly_reminder",
                    EmailCommunicationModel.subject == subject,
                    EmailCommunicationModel.status == "sent",
                )
                .all()
            }
            if already_sent:
                logger.info(
                    f"{len(already_sent)} volunteers already received this week's "
                    "reminder, resuming with the rest"
                )
                volunteers = [v for v in volunteers if v["email"] not in already_sent]

            logger.info(
                f"Sending weekly reminder emails to {len(volunteers)} volunteers"
            )

            email_communications = []
            for volunteer in volunteers:
                db_volunteer = volunteer_lookup.get(volunteer["email"])
                if not db_volunteer:
                    logger.warning(
                        f"Volunteer {volunteer['email']} not found in database, skipping"
                    )
                    continue

                if not db_volunteer.email_unsubscribe_token:
                    db_volunteer.email_unsubscribe_token = (
                        email_service.generate_unsubscribe_token()
                    )
                    db.commit()

                first_name = (
                    db_volunteer.name.split()[0] if db_volunteer.name else "Volunteer"
                )
                html_body = email_service.email_env.get_template(
                    "weekly-reminder-email.html"
                ).render(
                    first_name=first_name,
                    class_tables=[ct["table_html"] for ct in class_tables],
                    SCHEDULE_SIGNUP_LINK=ConfigHelper.get_schedule_signup_link(db)
                    or "#",
                    EMAIL_PREFERENCES_LINK=email_service.get_volunteer_unsubscribe_link(
                        db_volunteer, db
                    ),
                    INVITE_LINK_ZALO=ConfigHelper.get_invite_link_zalo(db) or "#",
                    INVITE_LINK_WHATSAPP=ConfigHelper.get_invite_link_whatsapp(db),
                    ONBOARDING_GUIDE_LINK=ConfigHelper.get_onboarding_guide_link(db)
                    or "#",
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

            # One shared SMTP session for the batch. A connection per message
            # cost ~1.85s each, which put a 170-volunteer run at 315s - past
            # both the scheduler deadline and Cloud Run's 300s request limit.
            sent_count = 0
            failed_count = 0
            outcomes = email_service.send_html_batch(
                (comm.recipient_email, comm.subject, html_body)
                for comm, html_body in email_communications
            )
            # Pair outcomes with rows by position, not by address: volunteer
            # emails carry no unique constraint, so keying on the address would
            # strand one of two volunteers who share one at PENDING forever.
            for (email_comm, _), (_, error) in zip(
                email_communications, outcomes, strict=True
            ):
                if error is None:
                    email_comm.status = "sent"
                    email_comm.sent_at = datetime.now()
                    sent_count += 1
                else:
                    email_comm.status = "failed"
                    email_comm.error_message = str(error)
                    failed_count += 1

            db.commit()

        logger.info(
            f"Weekly reminder run finished: {sent_count} sent, {failed_count} failed"
        )
        return {
            "status": "success" if not failed_count else "partial",
            "message": (
                f"Weekly reminder emails: {sent_count} sent, {failed_count} failed"
            ),
            "details": {
                "sent": sent_count,
                "failed": failed_count,
                "skipped_already_sent": len(already_sent),
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in send weekly reminder emails: {str(e)}", exc_info=True
        )
        return {
            "status": "error",
            "message": "Failed to send weekly reminder emails due to unexpected error",
            "details": {"error": str(e)},
        }
