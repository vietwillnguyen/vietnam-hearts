"""
Email service for sending automated emails to volunteers
"""

import os
import smtplib
import secrets
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import Session
from app.models import (
    EmailCommunication as EmailCommunicationModel,
    Volunteer as VolunteerModel,
)
from app.utils.logging_config import get_api_logger
from app.utils.config_helper import config, ConfigHelper
from jinja2 import Template

logger = get_api_logger()


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
        self.welcome_email_subject = "Welcome to Vietnam Hearts! ❤️🇻🇳"
        self.reminder_email_subject_template = "🗓️ Weekly Volunteer Reminder – Schedule Update ({start_date} to {end_date})"

        # External links - these will be loaded dynamically from database
        self.schedule_signup_link = None  # Will be loaded per request
        self.unsubscribe_link = None  # Will be loaded per request

        # Load HTML templates
        templates_dir = Path(__file__).parent.parent.parent / "templates" / "email"

        # Welcome email template
        welcome_template_path = templates_dir / "confirmation-email.html"
        with open(welcome_template_path, "r") as f:
            self.welcome_template = f.read()

        # Weekly reminder template
        reminder_template_path = templates_dir / "weekly-reminder-email.html"
        with open(reminder_template_path, "r") as f:
            self.reminder_template = f.read()

    def generate_unsubscribe_token(self) -> str:
        """Generate a secure unsubscribe token"""
        return secrets.token_urlsafe(32)

    def get_volunteer_unsubscribe_link(self, volunteer: VolunteerModel, db: Session) -> str:
        """Generate a personalized unsubscribe link for a volunteer"""
        if not volunteer.email_unsubscribe_token:
            # Generate and save token if it doesn't exist
            volunteer.email_unsubscribe_token = self.generate_unsubscribe_token()

        # Use API_URL directly with the unsubscribe endpoint
        from app.config import API_URL
        base_url = f"{API_URL.rstrip('/')}/public/unsubscribe"
        
        logger.info(
            f"Unsubscribe link: {base_url}?token={volunteer.email_unsubscribe_token}"
        )
        return f"{base_url}?token={volunteer.email_unsubscribe_token}"

    def get_reminder_subject(self, start_date: datetime, end_date: datetime) -> str:
        """Generate the reminder email subject with date range"""
        return self.reminder_email_subject_template.format(
            start_date=start_date.strftime("%m/%d"), end_date=end_date.strftime("%m/%d")
        )

    def build_class_table(self, class_name: str, config: dict, sheet_service, db: Session) -> dict:
        """Build HTML table for a specific class, with Day/Teacher/Assistant(s)/Status columns"""
        try:
            sheet_range = config.get("sheet_range")
            class_time = config.get("time", "")
            if not sheet_range:
                logger.error(f"No sheet_range configured for class {class_name}")
                return {
                    "class_name": class_name,
                    "table_html": f"<p>No sheet range configured for {class_name}</p>",
                    "has_data": False,
                }
            class_data = sheet_service.get_schedule_range(db, sheet_range)
            if not class_data or len(class_data) < 3:  # MIN_REQUIRED_ROWS = 3
                return {
                    "class_name": class_name,
                    "table_html": f"<p>No data available for {class_name}</p>",
                    "has_data": False,
                }

            # Transpose data: columns are days, rows are header/teacher/assistant
            # class_data: [header_row, teacher_row, assistant_row]
            days = class_data[0][1:]  # skip first col (label)
            teachers = class_data[1][1:]
            assistants = class_data[2][1:]

            # Table header
            table_html = f"<h3>{class_name} ({class_time})</h3>"
            table_html += "<table border='1' style='border-collapse: collapse; width: 100%;'>"
            table_html += "<thead><tr style='background-color: #f0f0f0;'>"
            table_html += "<th style='padding: 8px; text-align: center;'>Day</th>"
            table_html += "<th style='padding: 8px; text-align: center;'>Teacher</th>"
            table_html += "<th style='padding: 8px; text-align: center;'>Assistant(s)</th>"
            table_html += "<th style='padding: 8px; text-align: center;'>Status</th>"
            table_html += "</tr></thead><tbody>"

            for i, day in enumerate(days):
                teacher = teachers[i] if i < len(teachers) else ""
                assistant = assistants[i] if i < len(assistants) else ""
                # Status logic
                status = ""
                bg_color = ""
                teacher_lower = teacher.strip().lower() if teacher else ""
                assistant_lower = assistant.strip().lower() if assistant else ""
                if "optional" in teacher_lower:
                    status = "optional day, volunteers welcome to support existing classes"
                    bg_color = "#f5f5f5"
                elif "no class" in teacher_lower:
                    if "holiday" in teacher_lower:
                        status = "No class: holiday"
                    else:
                        status = "No class"
                    bg_color = "#f5f5f5"
                elif "need volunteers" in teacher_lower:
                    status = "❌ Missing Teacher"
                    bg_color = "#ffcccc"
                elif "need volunteers" in assistant_lower:
                    status = "❌ Missing TA's"
                    bg_color = "#fff3cd"
                else:
                    status = "✅ Fully Covered, TA's welcome to join"
                    bg_color = "#d4edda"

                table_html += f"<tr style='background-color: {bg_color};'>"
                table_html += f"<td style='padding: 8px; text-align: center;'>{day}</td>"
                table_html += f"<td style='padding: 8px; text-align: center;'>{teacher}</td>"
                table_html += f"<td style='padding: 8px; text-align: center;'>{assistant}</td>"
                table_html += f"<td style='padding: 8px; text-align: center;'>{status}</td>"
                table_html += "</tr>"

            table_html += "</tbody></table>"
            return {"class_name": class_name, "table_html": table_html, "has_data": True}

        except Exception as e:
            logger.error(f"Failed to build table for {class_name}: {str(e)}")
            return {
                "class_name": class_name,
                "table_html": f"<p>Error loading data for {class_name}</p>",
                "has_data": False,
            }

    def build_weekly_reminder_content(self, volunteer: VolunteerModel, db: Session) -> tuple[str, str]:
        """
        Build the HTML content and subject for a weekly reminder email
        
        Returns:
            tuple: (html_body, subject)
        """
        from app.services.classes_config import CLASS_CONFIG
        from app.services.google_sheets import sheets_service
        from datetime import datetime, timedelta

        # Calculate date range for the reminder (current week)
        today = datetime.now()
        start_date = today - timedelta(days=today.weekday())  # Monday
        end_date = start_date + timedelta(days=6)  # Sunday

        # Get the reminder subject
        subject = self.get_reminder_subject(start_date, end_date)

        # Build class tables
        class_tables = []
        for class_name, config in CLASS_CONFIG.items():
            class_tables.append(
                self.build_class_table(class_name, config, sheets_service, db)
            )

        # Get volunteer's first name
        first_name = volunteer.name.split()[0] if volunteer.name else "there"

        # Generate unsubscribe token if not exists
        if not volunteer.email_unsubscribe_token:
            volunteer.email_unsubscribe_token = self.generate_unsubscribe_token()
            db.commit()

        # Render template with class tables and all variables
        html_body = Template(self.reminder_template).render(
            first_name=first_name,
            class_tables=[ct['table_html'] for ct in class_tables],
            SCHEDULE_SIGNUP_LINK=ConfigHelper.get_schedule_signup_link(db) or "#",
            EMAIL_PREFERENCES_LINK=self.get_volunteer_unsubscribe_link(volunteer, db),
            FACEBOOK_MESSENGER_LINK=ConfigHelper.get_facebook_messenger_link(db) or "#",
            DISCORD_INVITE_LINK=ConfigHelper.get_discord_invite_link(db) or "#",
            ONBOARDING_GUIDE_LINK=ConfigHelper.get_onboarding_guide_link(db) or "#",
            INSTAGRAM_LINK=ConfigHelper.get_instagram_link(db) or "#",
            FACEBOOK_PAGE_LINK=ConfigHelper.get_facebook_page_link(db) or "#",
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
            facebook_messenger_link = config.get_facebook_messenger_link(db)
            discord_invite_link = config.get_discord_invite_link(db)
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
                "FACEBOOK_MESSENGER_LINK": facebook_messenger_link or "#",
                "DISCORD_INVITE_LINK": discord_invite_link or "#",
                "ONBOARDING_GUIDE_LINK": onboarding_guide_link or "#",
                "INSTAGRAM_LINK": instagram_link or "#",
                "FACEBOOK_PAGE_LINK": facebook_page_link or "#",
            }

            # Render template with variables
            template = Template(self.welcome_template)
            body = template.render(**template_vars)

            subject = self.welcome_email_subject
            to_email = volunteer.email

            # DRY_RUN logic
            if ConfigHelper.get_dry_run(db) and to_email != ConfigHelper.get_dry_run_email_recipient(db):
                logger.info(f"[DRY_RUN] Would send confirmation email to: {to_email} (subject: {subject}), logging email communications to database")
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
                return True

            # Create message
            message = MIMEMultipart()
            message["From"] = f"{self.sender_name} <{self.email_sender}>"
            message["To"] = to_email
            message["Subject"] = subject
            message.attach(MIMEText(body, "html"))

            # Connect to SMTP server and send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()  # Secure the connection
                server.login(self.smtp_username, self.smtp_password)
                server.send_message(message)

            logger.info(f"Confirmation email sent to {to_email}")
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
                    VolunteerModel.is_active == True,
                    ~VolunteerModel.email_communications.any(
                        EmailCommunicationModel.email_type == "volunteer_confirmation"
                    ),
                )
                .all()
            )

            for volunteer in volunteers:
                logger.info(f"Volunteer is a volunteer without a confirmation email, sending confirmation email to {volunteer.email}")
                self.send_confirmation_email(db, volunteer)

        except Exception as e:
            logger.error(
                f"Failed to Send emails to new volunteers: {str(e)}", exc_info=True
            )

    def send_custom_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        db: Optional[Session] = None,
        volunteer_id: Optional[int] = None,
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
            # DRY_RUN logic
            if ConfigHelper.get_dry_run(db) and to_email != ConfigHelper.get_dry_run_email_recipient(db):
                logger.info(f"[DRY_RUN] Would send custom email to: {to_email} (subject: {subject}), logging email communications to database")
                email_comm = EmailCommunicationModel(
                    volunteer_id=volunteer_id,
                    recipient_email=to_email,
                    email_type=email_type,
                    subject=subject,
                    template_name=None,
                    status="sent",
                    sent_at=datetime.now(),
                )
                if db is not None:
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

            logger.info(f"Custom email sent to {to_email}")
            return True
        except Exception as e:
            logger.error(f"Failed to send custom email: {str(e)}", exc_info=True)
            return False


# Create singleton instance
email_service = EmailService()
