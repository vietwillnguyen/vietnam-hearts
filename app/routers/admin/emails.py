"""
Admin email management endpoints
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import update
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
    WEEKLY_REMINDER_BULK_TEMPLATE,
    WEEKLY_REMINDER_DRY_RUN_TEMPLATE,
    get_email_summary,
    get_weekly_reminder_run_health,
    is_dry_run_email,
)
from app.services.email_service import email_service, reminder_week_key
from app.services.volunteer_activity import select_weekly_reminder_recipients
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
        real_emails = [e for e in reminder_emails if not is_dry_run_email(e)]
        dry_run = len(reminder_emails) - len(real_emails)
        total_sent = len(real_emails)
        successful = sum(1 for e in real_emails if e.status == "sent")
        failed = sum(1 for e in real_emails if e.status == "failed")

        four_weeks_ago = datetime.now() - timedelta(weeks=4)
        recent_emails = [
            e for e in reminder_emails if e.sent_at and e.sent_at >= four_weeks_ago
        ]

        weekly_stats = {}
        for email in recent_emails:
            week_start = email.sent_at - timedelta(days=email.sent_at.weekday())
            week_key = week_start.strftime("%Y-%m-%d")
            if week_key not in weekly_stats:
                weekly_stats[week_key] = {
                    "total": 0,
                    "successful": 0,
                    "failed": 0,
                    "dry_run": 0,
                }
            if is_dry_run_email(email):
                weekly_stats[week_key]["dry_run"] += 1
                continue
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
                "dry_run": dry_run,
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
def send_confirmation_emails(request: Request, db: Session = Depends(get_db)):
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
def send_weekly_reminder_emails(request: Request):
    """Send weekly reminder emails to recently active subscribed volunteers.

    Recipients come from select_weekly_reminder_recipients, which adds a
    recency window (REMINDER_ACTIVE_WEEKS) on top of the subscription flags so
    volunteers who signed up once and never came back stop being emailed
    forever.

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

            # Everything that is the same for every recipient - both Sheets
            # reads, every class table and every settings link - is built once
            # here. Building it per volunteer cost ~6 Supabase round trips a
            # head, which is what put a 176-recipient run past Cloud Run's
            # 300s request limit even after the SMTP session was shared.
            content = email_service.build_weekly_reminder_shared_content(db)
            current_monday, current_friday = content.week_start, content.week_end
            subject = email_service.get_reminder_subject(
                current_monday, current_friday, content.open_slots
            )

            if not content.needs_volunteers:
                logger.info(
                    "No open volunteer slots this week, skipping weekly reminder emails"
                )
                return {
                    "status": "skipped",
                    "message": "No volunteer slots need filling this week, weekly reminder emails were not sent.",
                }

            dry_run = ConfigHelper.get_dry_run(db)
            if dry_run:
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
                db_volunteers = select_weekly_reminder_recipients(db)
                volunteers = [{"email": v.email, "name": v.name} for v in db_volunteers]
                volunteer_lookup = {v.email: v for v in db_volunteers}

            # Matched on the week, not the whole subject: the subject leads
            # with the open-slot count, so a volunteer signing up between a run
            # cut off at the deadline and its resume would change it - and an
            # exact match would then re-email everyone the first attempt had
            # already reached.
            week_key = reminder_week_key(current_monday, current_friday)
            already_sent = {
                row.recipient_email
                for row in db.query(EmailCommunicationModel.recipient_email)
                .filter(
                    EmailCommunicationModel.email_type == "weekly_reminder",
                    EmailCommunicationModel.subject.like(f"%{week_key}"),
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

            recipients = []
            for volunteer in volunteers:
                db_volunteer = volunteer_lookup.get(volunteer["email"])
                if not db_volunteer:
                    logger.warning(
                        "A selected reminder recipient has no volunteer record, skipping"
                    )
                    continue
                recipients.append((volunteer, db_volunteer))

            # Not committed here: a commit expires every loaded volunteer, so
            # each one would reload its row before it could be rendered. The
            # tokens persist with the PENDING rows' commit below, before any
            # email carrying them is sent.
            email_service.ensure_unsubscribe_tokens(
                [db_volunteer for _, db_volunteer in recipients], db
            )

            email_communications = []
            for volunteer, db_volunteer in recipients:
                html_body = email_service.render_weekly_reminder(
                    db_volunteer, content, db, default_first_name="Volunteer"
                )

                email_comm = EmailCommunicationModel(
                    volunteer_id=db_volunteer.id,
                    recipient_email=volunteer["email"],
                    email_type="weekly_reminder",
                    subject=subject,
                    template_name=(
                        WEEKLY_REMINDER_DRY_RUN_TEMPLATE
                        if dry_run
                        else WEEKLY_REMINDER_BULK_TEMPLATE
                    ),
                    status="PENDING",
                )
                email_communications.append(
                    (
                        email_comm,
                        html_body,
                        email_service.get_one_click_unsubscribe_link(db_volunteer, db),
                    )
                )

            messages = [
                (comm.recipient_email, comm.subject, html_body, unsubscribe_link)
                for comm, html_body, unsubscribe_link in email_communications
            ]
            for email_comm, _, _ in email_communications:
                db.add(email_comm)
            db.flush()
            row_ids = [email_comm.id for email_comm, _, _ in email_communications]
            db.commit()

            # One shared SMTP session for the batch. A connection per message
            # cost ~1.85s each, which put a 170-volunteer run at 315s - past
            # both the scheduler deadline and Cloud Run's 300s request limit.
            sent_count = 0
            failed_count = 0
            outcomes = email_service.send_html_batch(messages)
            # Pair outcomes with rows by position, not by address: volunteer
            # emails carry no unique constraint, so keying on the address would
            # strand one of two volunteers who share one at PENDING forever.
            # Each outcome is committed as it arrives so a run cut off at the
            # deadline leaves only the untouched remainder PENDING. It is
            # written on an autocommit connection: inside a session transaction
            # every outcome cost three round trips (BEGIN, UPDATE, COMMIT),
            # which at Supabase latency was most of the send phase.
            with (
                db.get_bind()
                .connect()
                .execution_options(isolation_level="AUTOCOMMIT")
            ) as outcome_conn:
                for row_id, (_, error) in zip(row_ids, outcomes, strict=True):
                    if error is None:
                        outcome = {
                            "status": "dry_run" if dry_run else "sent",
                            "sent_at": datetime.now(),
                        }
                        sent_count += 1
                    else:
                        outcome = {"status": "failed", "error_message": str(error)}
                        failed_count += 1
                    outcome_conn.execute(
                        update(EmailCommunicationModel)
                        .where(EmailCommunicationModel.id == row_id)
                        .values(outcome)
                    )

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
