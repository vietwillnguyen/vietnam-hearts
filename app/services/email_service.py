"""
Email service for sending automated emails to volunteers
"""

import os
import re
import secrets
import smtplib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.models import (
    EmailCommunication as EmailCommunicationModel,
)
from app.models import (
    Volunteer as VolunteerModel,
)
from app.services.schedule_parser import ClassBlock
from app.utils.config_helper import config
from app.utils.logging_config import get_api_logger, mask_email
from app.utils.schedule_dates import is_teaching_day

logger = get_api_logger()


# The reminder subject leads with the number of open slots, which changes the
# moment anyone signs up. The week's date range is the part that does not, and
# two things key off it: resuming a run cut off at the Cloud Run deadline, and
# grouping a run's rows in the health report. Both would re-email or
# mis-report a whole list if they keyed on the volatile part, so the week key
# is named here rather than left implicit in the subject string.
def reminder_week_key(start_date: datetime, end_date: datetime) -> str:
    """The stable `(dd/mm-dd/mm)` suffix identifying the week a reminder is for."""
    return f"({start_date.strftime('%d/%m')}-{end_date.strftime('%d/%m')})"


@dataclass(frozen=True)
class WeeklyReminderContent:
    """The parts of a weekly reminder that are identical for every recipient.

    Built once per run and reused for all of it. Everything here costs a
    network round trip to build - two Google Sheets reads for the schedule
    plus one Supabase read per settings link - and none of it depends on who
    is being emailed, so building it per volunteer is what put the Sunday run
    past Cloud Run's 300s request limit at 176 recipients.
    """

    class_tables: list[dict]
    # The Monday and Friday of the week the tables above were read from.
    week_start: datetime
    week_end: datetime
    # Template variables that are the same for every recipient.
    links: dict[str, str]

    @property
    def tables_html(self) -> list[str]:
        return [table["table_html"] for table in self.class_tables]

    @property
    def open_slots(self) -> int:
        return sum(table["open_slot_count"] for table in self.class_tables)

    @property
    def needs_volunteers(self) -> bool:
        return any(table.get("needs_volunteers") for table in self.class_tables)


# Errors that mean the SMTP session itself is gone, so the message is worth
# retrying on a fresh connection. A rejected recipient or a refused message is
# deliberately absent: the session still works, so reconnecting would only buy
# back the per-message login cost that send_html_batch exists to avoid.
SESSION_LEVEL_SMTP_ERRORS = (
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPConnectError,
    smtplib.SMTPHeloError,
    smtplib.SMTPAuthenticationError,
    ConnectionError,
    TimeoutError,
)


def _is_session_level_error(error: Exception) -> bool:
    return isinstance(error, SESSION_LEVEL_SMTP_ERRORS)


def _close_quietly(server: smtplib.SMTP | None) -> None:
    """Close a session without letting teardown mask the real failure."""
    if server is None:
        return
    try:
        server.quit()
    except Exception:
        try:
            server.close()
        except Exception:
            logger.debug("Ignoring error while closing SMTP session", exc_info=True)


class EmailService:
    def __init__(self):
        self.email_sender = os.getenv("EMAIL_SENDER")
        self.sender_name = "Vietnam Hearts"
        self.smtp_server = "smtp.gmail.com"
        self.smtp_port = 587
        self.smtp_username = self.email_sender
        self.smtp_password = os.getenv(
            "GMAIL_APP_PASSWORD"
        )  # Use App Password, not regular password

        # Email subjects
        self.welcome_email_subject = "[ACTION REQUIRED] Welcome to Vietnam Hearts! ❤️🇻🇳"
        # Leads with what the volunteer needs to decide whether to open it.
        # The trailing week key must stay last: reminder_week_key_of() reads it
        # off the end to identify the run.
        self.reminder_email_subject_template = (
            "{open_slots} open slot{plural} this week {week_key}"
        )

        # External links - these will be loaded dynamically from database
        self.schedule_signup_link = None  # Will be loaded per request
        self.unsubscribe_link = None  # Will be loaded per request

        # Load HTML templates
        templates_dir = config.EMAIL_TEMPLATES_PATH

        # Create Jinja2 environment for email templates
        self.email_env = Environment(loader=FileSystemLoader(str(templates_dir)))

        # Welcome email template
        welcome_EMAIL_TEMPLATES_PATH = (
            config.EMAIL_TEMPLATES_PATH / "confirmation-email.html"
        )
        with open(welcome_EMAIL_TEMPLATES_PATH) as f:
            self.welcome_template = f.read()

        # Weekly reminder template
        reminder_EMAIL_TEMPLATES_PATH = (
            config.EMAIL_TEMPLATES_PATH / "weekly-reminder-email.html"
        )
        with open(reminder_EMAIL_TEMPLATES_PATH) as f:
            self.reminder_template = f.read()

    def generate_unsubscribe_token(self) -> str:
        """Generate a secure unsubscribe token"""
        return secrets.token_urlsafe(32)

    def get_volunteer_unsubscribe_link(
        self, volunteer: VolunteerModel, db: Session
    ) -> str:
        """Generate a personalized unsubscribe link for a volunteer"""
        if not volunteer.email_unsubscribe_token:
            # Generate and save token if it doesn't exist
            volunteer.email_unsubscribe_token = self.generate_unsubscribe_token()

        # Use API_URL for the unsubscribe link
        from app.config import API_URL

        base_url = f"{API_URL.rstrip('/')}/unsubscribe"
        # The token is the whole credential for this link, so it is not logged.
        # CredentialRedactingFilter does rewrite "?token=..." to "***" on every
        # sink, but a call site should not hand a secret to the logger and rely
        # on the net catching it - and at one line per recipient this was also
        # the noisiest INFO in a Sunday run.
        logger.info(f"Built unsubscribe link for volunteer id={volunteer.id}")
        return f"{base_url}?token={volunteer.email_unsubscribe_token}"

    def get_one_click_unsubscribe_link(
        self, volunteer: VolunteerModel, db: Session
    ) -> str:
        """The RFC 8058 one-click URL for this volunteer's List-Unsubscribe header.

        Separate from the preferences page because the two are different
        things: that page is a form a human reads, while this URL is POSTed by
        the mail provider with no interface at all, and stops the weekly
        reminder only.
        """
        if not volunteer.email_unsubscribe_token:
            volunteer.email_unsubscribe_token = self.generate_unsubscribe_token()

        from app.config import API_URL

        return (
            f"{API_URL.rstrip('/')}/unsubscribe/one-click"
            f"?token={volunteer.email_unsubscribe_token}"
        )

    def get_reminder_subject(
        self, start_date: datetime, end_date: datetime, open_slots: int
    ) -> str:
        """The reminder subject: how many slots are open, and for which week.

        ``open_slots`` is required rather than defaulted because the count is
        the point of the line - a silent default would announce a wrong number
        to the whole list. Callers get it by summing ``open_slot_count`` over
        the week's class tables.
        """
        return self.reminder_email_subject_template.format(
            open_slots=open_slots,
            plural="" if open_slots == 1 else "s",
            week_key=reminder_week_key(start_date, end_date),
        )

    def build_class_table(
        self,
        block: "ClassBlock",
        teaching_days: Iterable[str] | None = None,
        week_monday: datetime | None = None,
    ) -> dict:
        """
        Build the HTML table for a single class from a parsed ClassBlock.

        Columns: Day / Teacher / [Head Assistant] / Assistant(s) / Status.
        The Head Assistant column is only rendered for classes that actually have
        a head TA row (block.has_head_ta). max_assistants of None means no limit.

        `teaching_days` are the weekdays the organization actually teaches on
        (default DEFAULT_TEACHING_DAYS); a blank teacher cell on any other day
        is a day with no class rather than an unfilled slot. `week_monday` is
        the Monday of the tab the block was read from, which dates a bare d/m
        day label such as "19/10" so it too can be placed on its weekday.

        The returned dict includes a `needs_volunteers` flag (True if any day is
        missing a teacher, head TA, or assistant) that callers use to decide
        whether a class still has an open slot, and `open_slot_count`, how many
        unfilled roles there are - a teaching day missing its teacher, its head
        TA and its assistants counts three - and the reminder's subject line is
        the sum of those over the week's classes. On parse failure the flag defaults to True so
        a parsing bug never silently suppresses a reminder, and the count to 1
        so the subject cannot announce "0 open slots" on an email sent
        precisely because slots might be open.
        """
        try:
            days = block.days
            teachers = block.teacher
            head_tas = block.head_ta
            assistants = block.assistants
            max_assistants = block.max_assistants

            cap_label = (
                f"Max {max_assistants} Assistants"
                if max_assistants is not None
                else "No assistant limit"
            )
            cell = "padding: 8px; text-align: center;"

            table_html = f"<h3>{block.name} ({block.time}) - {cap_label}</h3>"
            table_html += (
                "<table border='1' style='border-collapse: collapse; width: 100%;'>"
            )
            table_html += "<thead><tr style='background-color: #f0f0f0;'>"
            table_html += f"<th style='{cell}'>Day</th>"
            table_html += f"<th style='{cell}'>Teacher</th>"
            if block.has_head_ta:
                table_html += f"<th style='{cell}'>Head Assistant</th>"
            table_html += f"<th style='{cell}'>Assistant(s)</th>"
            table_html += f"<th style='{cell}'>Status</th>"
            table_html += "</tr></thead><tbody>"

            needs_volunteers = False
            open_slot_count = 0

            for i, day in enumerate(days):
                teacher = teachers[i] if i < len(teachers) else ""
                head_ta = head_tas[i] if i < len(head_tas) else ""
                assistant = assistants[i] if i < len(assistants) else ""

                # Count current assistants (split by comma/semicolon, ignore blanks)
                current_assistants = 0
                if assistant and assistant.strip():
                    assistant_list = [
                        a.strip() for a in re.split(r"[,;]", assistant) if a.strip()
                    ]
                    current_assistants = len(assistant_list)

                status = ""
                bg_color = ""
                teacher_lower = teacher.strip().lower() if teacher else ""
                head_ta_lower = head_ta.strip().lower() if head_ta else ""
                assistant_lower = assistant.strip().lower() if assistant else ""
                teacher_missing = (
                    not teacher_lower or "need volunteers" in teacher_lower
                )
                head_ta_missing = block.has_head_ta and (
                    not head_ta or "need volunteers" in head_ta_lower
                )
                assistants_missing = "need volunteers" in assistant_lower
                day_open_slots = sum(
                    (teacher_missing, head_ta_missing, assistants_missing)
                )

                if "optional" in teacher_lower:
                    status = (
                        "optional day, volunteers welcome to support existing classes"
                    )
                    bg_color = "#f5f5f5"
                elif "no class" in teacher_lower:
                    status = (
                        "No class: holiday"
                        if "holiday" in teacher_lower
                        else "No class"
                    )
                    bg_color = "#f5f5f5"
                elif teacher_missing:
                    if is_teaching_day(day, teaching_days, week_monday):
                        status = "❌ Missing Teacher"
                        bg_color = "#ffcccc"
                        needs_volunteers = True
                        open_slot_count += day_open_slots
                    else:
                        # Days the organization does not teach on keep their
                        # column in the sheet and are simply left blank, so an
                        # empty cell here is not an open slot. The row stays -
                        # dropping it would make the email disagree with the
                        # grid volunteers read alongside it - but it renders
                        # neutrally and raises no call for volunteers.
                        status = "No class"
                        bg_color = "#f5f5f5"
                elif head_ta_missing:
                    status = "❌ Missing Head TA"
                    bg_color = "#ffe5b4"
                    needs_volunteers = True
                    open_slot_count += day_open_slots
                elif assistants_missing:
                    status = "❌ Missing TA's"
                    bg_color = "#fff3cd"
                    needs_volunteers = True
                    open_slot_count += day_open_slots
                elif max_assistants is None:
                    status = f"✅ {current_assistants} assistant(s) signed up - No limit, TA's welcome to join"
                    bg_color = "#d4edda"
                elif current_assistants >= max_assistants:
                    status = f"✅ Fully Covered ({current_assistants}/{max_assistants} assistants)"
                    bg_color = "#d4edda"
                else:
                    status = f"✅ Partially Covered ({current_assistants}/{max_assistants} assistants) - TA's welcome to join"
                    bg_color = "#d4edda"

                table_html += f"<tr style='background-color: {bg_color};'>"
                table_html += f"<td style='{cell}'>{day}</td>"
                table_html += f"<td style='{cell}'>{teacher}</td>"
                if block.has_head_ta:
                    table_html += f"<td style='{cell}'>{head_ta}</td>"
                table_html += f"<td style='{cell}'>{assistant}</td>"
                table_html += f"<td style='{cell}'>{status}</td>"
                table_html += "</tr>"

            table_html += "</tbody></table>"
            return {
                "class_name": block.name,
                "table_html": table_html,
                "has_data": True,
                "needs_volunteers": needs_volunteers,
                "open_slot_count": open_slot_count,
            }

        except Exception as e:
            logger.error(
                f"Failed to build table for {getattr(block, 'name', '?')}: {str(e)}"
            )
            # Unknown slot state on parse failure must not silently suppress reminders.
            return {
                "class_name": getattr(block, "name", "?"),
                "table_html": f"<p>Error loading data for {getattr(block, 'name', 'this class')}</p>",
                "has_data": False,
                "needs_volunteers": True,
                "open_slot_count": 1,
            }

    def build_weekly_reminder_shared_content(
        self, db: Session
    ) -> WeeklyReminderContent:
        """Build the recipient-independent half of this week's reminder.

        Call this once per run, not once per recipient: it makes two Google
        Sheets round trips and one Supabase read per settings link, and
        returns the same thing every time within a run.
        """
        from app.services.google_sheets import sheets_service
        from app.utils.config_helper import ConfigHelper

        # Auto-discover class blocks from the schedule sheet (single source of truth)
        class_blocks = sheets_service.get_schedule_blocks(db)
        teaching_days = ConfigHelper.get_schedule_teaching_days(db)
        week_start, week_end = sheets_service.get_current_schedule_dates(db)

        return WeeklyReminderContent(
            class_tables=[
                self.build_class_table(block, teaching_days, week_start)
                for block in class_blocks
            ],
            week_start=week_start,
            week_end=week_end,
            links={
                "SCHEDULE_SIGNUP_LINK": ConfigHelper.get_schedule_signup_link(db)
                or "#",
                "INVITE_LINK_ZALO": ConfigHelper.get_invite_link_zalo(db) or "#",
                "INVITE_LINK_WHATSAPP": ConfigHelper.get_invite_link_whatsapp(db),
                "ONBOARDING_GUIDE_LINK": ConfigHelper.get_onboarding_guide_link(db)
                or "#",
                "INSTAGRAM_LINK": ConfigHelper.get_instagram_link(db) or "#",
                "FACEBOOK_PAGE_LINK": ConfigHelper.get_facebook_page_link(db) or "#",
            },
        )

    def render_weekly_reminder(
        self,
        volunteer: VolunteerModel,
        content: WeeklyReminderContent,
        db: Session,
        *,
        default_first_name: str,
    ) -> str:
        """Render one recipient's copy of an already-built weekly reminder.

        Only the greeting and the preferences link vary per recipient, so this
        is the whole of the per-volunteer work and makes no network call.
        ``default_first_name`` differs between the bulk run and a single send
        and is therefore the caller's to supply.
        """
        first_name = volunteer.name.split()[0] if volunteer.name else default_first_name
        return self.email_env.get_template("weekly-reminder-email.html").render(
            first_name=first_name,
            class_tables=content.tables_html,
            EMAIL_PREFERENCES_LINK=self.get_volunteer_unsubscribe_link(volunteer, db),
            **content.links,
        )

    def ensure_unsubscribe_tokens(
        self, volunteers: Iterable[VolunteerModel], db: Session
    ) -> int:
        """Give every volunteer that lacks one an unsubscribe token, in one commit.

        One commit per volunteer is two costs, not one: the round trip itself,
        and the expiry of every loaded ORM object it triggers, which makes the
        next volunteer's attributes reload from the database.
        """
        issued = 0
        for volunteer in volunteers:
            if not volunteer.email_unsubscribe_token:
                volunteer.email_unsubscribe_token = self.generate_unsubscribe_token()
                issued += 1
        if issued:
            db.commit()
            logger.info(f"Issued {issued} missing unsubscribe tokens")
        return issued

    def build_weekly_reminder_content(
        self, volunteer: VolunteerModel, db: Session
    ) -> tuple[str, str]:
        """
        Build the HTML content and subject for a weekly reminder email

        Single-recipient path (preview and admin test sends); the bulk run
        builds the shared content once itself and renders per volunteer.

        Returns:
            tuple: (html_body, subject)
        """
        from datetime import timedelta

        content = self.build_weekly_reminder_shared_content(db)

        # The subject must name the week the tables above were actually read
        # from, and those come from the leading visible schedule tab. That tab
        # only turns over when rotation next runs, while the computed week
        # anchor turns over the instant Friday starts, so deriving the subject
        # from the anchor announces a week the body does not show for as long
        # as rotation lags. Reading the same tab the body did keeps the two
        # together and matches the bulk send; its own fallback is that shared
        # anchor, evaluated in the organization's timezone.
        end_date = content.week_start + timedelta(days=6)  # Sunday
        subject = self.get_reminder_subject(
            content.week_start, end_date, content.open_slots
        )

        self.ensure_unsubscribe_tokens([volunteer], db)
        html_body = self.render_weekly_reminder(
            volunteer, content, db, default_first_name="there"
        )
        return html_body, subject

    def send_confirmation_email(self, db: Session, volunteer: VolunteerModel) -> bool:
        """
        Send a confirmation email to a new volunteer
        Returns True if email was sent successfully
        """
        try:
            # Generate unsubscribe token if not exists
            if not volunteer.email_unsubscribe_token:
                volunteer.email_unsubscribe_token = self.generate_unsubscribe_token()
                db.commit()

            # Get dynamic settings from database
            schedule_signup_link = config.get_schedule_signup_link(db)
            INVITE_LINK_ZALO = config.get_invite_link_zalo(db)
            INVITE_LINK_WHATSAPP = config.get_invite_link_whatsapp(db)
            onboarding_guide_link = config.get_onboarding_guide_link(db)
            instagram_link = config.get_instagram_link(db)
            facebook_page_link = config.get_facebook_page_link(db)

            # Prepare template variables
            template_vars = {
                "UserFullName": volunteer.name,
                "SCHEDULE_SIGNUP_LINK": schedule_signup_link or "#",
                "EMAIL_PREFERENCES_LINK": self.get_volunteer_unsubscribe_link(
                    volunteer, db
                ),
                "INVITE_LINK_ZALO": INVITE_LINK_ZALO or "#",
                "INVITE_LINK_WHATSAPP": INVITE_LINK_WHATSAPP,
                "ONBOARDING_GUIDE_LINK": onboarding_guide_link or "#",
                "INSTAGRAM_LINK": instagram_link or "#",
                "FACEBOOK_PAGE_LINK": facebook_page_link or "#",
            }

            # Render template with variables
            template = self.email_env.get_template("confirmation-email.html")
            body = template.render(**template_vars)

            subject = self.welcome_email_subject
            to_email = volunteer.email

            # DRY_RUN logic
            if config.get_dry_run(
                db
            ) and to_email != config.get_dry_run_email_recipient(db):
                logger.info(
                    f"[DRY_RUN] Would send confirmation email to volunteer id={volunteer.id} (subject: {subject}), logging email communications to database"
                )
                return True

            # Create message
            message = MIMEMultipart()
            message["From"] = f"{self.sender_name} <{self.email_sender}>"
            message["To"] = to_email
            message["Subject"] = subject

            # Create HTML part
            html_part = MIMEText(body, "html")
            message.attach(html_part)

            # Attach images as embedded content
            try:
                # Banner image
                banner_path = (
                    Path(__file__).parent.parent.parent / "static" / "banner.jpg"
                )
                if banner_path.exists():
                    with open(banner_path, "rb") as f:
                        banner_img = MIMEImage(f.read(), _subtype="jpeg")
                        banner_img.add_header("Content-ID", "<banner_image>")
                        banner_img.add_header(
                            "Content-Disposition", "inline", filename="banner.jpg"
                        )
                        message.attach(banner_img)

                # Logo image
                logo_path = (
                    Path(__file__).parent.parent.parent
                    / "static"
                    / "vietnam-hearts-logo.ico"
                )
                if logo_path.exists():
                    with open(logo_path, "rb") as f:
                        logo_img = MIMEImage(f.read(), _subtype="x-icon")
                        logo_img.add_header("Content-ID", "<logo_image>")
                        logo_img.add_header(
                            "Content-Disposition",
                            "inline",
                            filename="vietnam-hearts-logo.ico",
                        )
                        message.attach(logo_img)

                # Field day goodbye image
                field_day_path = (
                    Path(__file__).parent.parent.parent
                    / "static"
                    / "field-day-goodbye.png"
                )
                if field_day_path.exists():
                    with open(field_day_path, "rb") as f:
                        field_day_img = MIMEImage(f.read(), _subtype="png")
                        field_day_img.add_header("Content-ID", "<field_day_image>")
                        field_day_img.add_header(
                            "Content-Disposition",
                            "inline",
                            filename="field-day-goodbye.png",
                        )
                        message.attach(field_day_img)

            except Exception as e:
                logger.warning(
                    f"Failed to attach images to confirmation email: {str(e)}"
                )
                # Continue without images if attachment fails

            # Connect to SMTP server and send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()  # Secure the connection
                server.login(self.smtp_username, self.smtp_password)
                server.send_message(message)

            logger.info(f"Confirmation email sent to volunteer id={volunteer.id}")
            # Log the email communication in database
            email_comm = EmailCommunicationModel(
                volunteer_id=volunteer.id,
                recipient_email=to_email,
                email_type="volunteer_confirmation",
                subject=subject,
                template_name="confirmation-email.html",
                status="sent",
                sent_at=datetime.now(),
            )
            db.add(email_comm)
            db.commit()
            # Database is now the source of truth; no write-back to Sheets.
            return True

        except Exception as e:
            logger.error(f"Failed to send confirmation email: {str(e)}", exc_info=True)
            # Database is now the source of truth; no write-back to Sheets.
            return False

    def send_confirmation_emails(self, db: Session) -> None:
        """
        Send emails to all new volunteers who haven't received confirmation emails
        """
        try:
            # Find volunteers who haven't received confirmation emails
            volunteers = (
                db.query(VolunteerModel)
                .filter(
                    VolunteerModel.is_active.is_(True),
                    VolunteerModel.email.contains("@"),
                    ~VolunteerModel.email_communications.any(
                        EmailCommunicationModel.email_type == "volunteer_confirmation"
                    ),
                )
                .all()
            )

            for volunteer in volunteers:
                if "@" not in (volunteer.email or ""):
                    logger.warning(
                        f"Skipping malformed email for volunteer id={volunteer.id}"
                    )
                    continue
                logger.info(
                    f"Volunteer is a volunteer without a confirmation email, sending confirmation email to volunteer id={volunteer.id}"
                )
                self.send_confirmation_email(db, volunteer)

        except Exception as e:
            logger.error(
                f"Failed to Send emails to new volunteers: {str(e)}", exc_info=True
            )

    def _open_smtp_session(self) -> smtplib.SMTP:
        """Connect, secure and authenticate one SMTP session."""
        server = smtplib.SMTP(self.smtp_server, self.smtp_port)
        try:
            server.starttls()
            server.login(self.smtp_username, self.smtp_password)
        except Exception:
            _close_quietly(server)
            raise
        return server

    def _build_html_message(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        one_click_unsubscribe_link: str | None = None,
    ) -> MIMEMultipart:
        message = MIMEMultipart()
        message["From"] = f"{self.sender_name} <{self.email_sender}>"
        message["To"] = to_email
        message["Subject"] = subject
        if one_click_unsubscribe_link:
            # RFC 8058. Gmail and Yahoo's bulk-sender rules expect these on a
            # subscribed mailing, and together they put the provider's own
            # "Unsubscribe" control next to the sender name - a far more
            # visible way out than a link at the foot of the mail, and one that
            # is not reported as spam instead.
            #
            # Only set for list mail. Advertising a list unsubscribe on a
            # confirmation email would offer to unsubscribe someone from a list
            # they are not on.
            message["List-Unsubscribe"] = f"<{one_click_unsubscribe_link}>"
            message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        message.attach(MIMEText(html_body, "html"))
        return message

    def send_html_batch(
        self, messages: Iterable[tuple[str, str, str, str | None]]
    ) -> Iterator[tuple[str, Exception | None]]:
        """Deliver (to_email, subject, html_body, unsubscribe_link) rows over one
        SMTP session.

        The fourth element is this recipient's RFC 8058 one-click unsubscribe
        URL, or None for mail that is not a subscribed list mailing. It travels
        per message rather than being looked up from the address, because
        volunteers.email has no unique constraint - the same reason outcomes
        below are paired to rows by position.

        Yields (to_email, error) per message, error being None on success, so
        the caller can record each outcome as it happens rather than inferring
        it. Unlike send_custom_email this never reports a refused send as a
        success - mistaking one for the other is what filled
        email_communications with rows marked "sent" during the four Sundays
        the job was failing.

        Gmail charges a TCP connect, a STARTTLS handshake and an AUTH round
        trip per connection - about 1.7s each from Cloud Run - so a connection
        per message made the run grow linearly with the volunteer list and pass
        the request deadline at around 160 recipients. One shared session
        amortises that over the whole batch.

        A shared session can still be dropped mid-batch, so a connection-level
        error reconnects and retries that message once. A permanent rejection
        does not: the session is fine, and reconnecting per bad address would
        put back the per-message login cost this exists to remove.
        """
        server: smtplib.SMTP | None = None
        # Set once the session cannot be established at all. That is a
        # run-level fault, so the remaining messages are reported against it
        # instead of each paying for another doomed connection attempt.
        connect_error: Exception | None = None
        try:
            for to_email, subject, html_body, unsubscribe_link in messages:
                if connect_error is not None:
                    yield to_email, connect_error
                    continue

                error: Exception | None = None
                for attempt in (1, 2):
                    if server is None:
                        try:
                            server = self._open_smtp_session()
                        except Exception as e:
                            logger.error(f"Failed to open SMTP session: {str(e)}")
                            connect_error = e
                            error = e
                            break

                    try:
                        server.send_message(
                            self._build_html_message(
                                to_email, subject, html_body, unsubscribe_link
                            )
                        )
                        error = None
                        break
                    except Exception as e:
                        error = e
                        if attempt == 2 or not _is_session_level_error(e):
                            logger.error(
                                f"Failed to send email to {mask_email(to_email)}: {str(e)}"
                            )
                            break
                        logger.warning(
                            f"SMTP session lost before {mask_email(to_email)}, reconnecting: {str(e)}"
                        )
                        _close_quietly(server)
                        server = None

                yield to_email, error
        finally:
            _close_quietly(server)

    def send_custom_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        db: Session | None = None,
        volunteer_id: int | None = None,
        email_type: str = "custom",
    ) -> bool:
        """
        Send a custom HTML email to a recipient.
        Returns True if email was sent successfully

        Args:
            to_email: Recipient's email address
            subject: Email subject
            html_body: HTML content of the email
            db: Optional database session for tracking the communication
            volunteer_id: Optional volunteer ID if this is for a specific volunteer
            email_type: Type of email being sent (e.g., "custom", "reminder", "newsletter")
        """
        try:
            # DRY_RUN logic - only check if db is provided
            if (
                db is not None
                and config.get_dry_run(db)
                and to_email != config.get_dry_run_email_recipient(db)
            ):
                logger.info(
                    f"[DRY_RUN] Would send custom email to: {mask_email(to_email)} (subject: {subject}), logging email communications to database"
                )
                email_comm = EmailCommunicationModel(
                    volunteer_id=volunteer_id,
                    recipient_email=to_email,
                    email_type=email_type,
                    subject=subject,
                    template_name=None,
                    status="dry_run",
                    sent_at=datetime.now(),
                )
                db.add(email_comm)
                db.commit()
                return True

            message = MIMEMultipart()
            message["From"] = f"{self.sender_name} <{self.email_sender}>"
            message["To"] = to_email
            message["Subject"] = subject
            message.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_username, self.smtp_password)
                server.send_message(message)

            # Track the communication if database session is provided
            if db is not None:
                email_comm = EmailCommunicationModel(
                    volunteer_id=volunteer_id,
                    recipient_email=to_email,
                    email_type=email_type,
                    subject=subject,
                    template_name=None,  # Custom emails don't use templates
                    status="sent",
                    sent_at=datetime.now(),
                )
                db.add(email_comm)
                db.commit()

            logger.info(f"Custom email sent to {mask_email(to_email)}")
            return True
        except Exception as e:
            logger.error(f"Failed to send custom email: {str(e)}", exc_info=True)
            return False


# Create singleton instance
email_service = EmailService()
