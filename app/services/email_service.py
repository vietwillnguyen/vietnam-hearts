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
from app.config import (
    SCHEDULE_SIGNUP_LINK,
    EMAIL_PREFERENCES_LINK,
    FACEBOOK_MESSENGER_LINK,
    DISCORD_INVITE_LINK,
    ONBOARDING_GUIDE_LINK,
    INSTAGRAM_LINK,
    FACEBOOK_PAGE_LINK,
    DRY_RUN,
    DRY_RUN_EMAIL_RECIPIENT,
)
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

        # External links
        self.schedule_signup_link = SCHEDULE_SIGNUP_LINK
        self.unsubscribe_link = EMAIL_PREFERENCES_LINK

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

    def get_volunteer_unsubscribe_link(self, volunteer: VolunteerModel) -> str:
        """Generate a personalized unsubscribe link for a volunteer"""
        if not volunteer.email_unsubscribe_token:
            # Generate and save token if it doesn't exist
            volunteer.email_unsubscribe_token = self.generate_unsubscribe_token()

        # Use the base unsubscribe link and append the token
        base_url = self.unsubscribe_link.rstrip("/")
        logger.info(
            f"Unsubscribe link: {base_url}?token={volunteer.email_unsubscribe_token}"
        )
        return f"{base_url}?token={volunteer.email_unsubscribe_token}"

    def get_reminder_subject(self, start_date: datetime, end_date: datetime) -> str:
        """Generate the reminder email subject with date range"""
        return self.reminder_email_subject_template.format(
            start_date=start_date.strftime("%m/%d"), end_date=end_date.strftime("%m/%d")
        )

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

            # Prepare template variables
            template_vars = {
                "UserFullName": volunteer.name,
                "SCHEDULE_SIGNUP_LINK": SCHEDULE_SIGNUP_LINK,
                "EMAIL_PREFERENCES_LINK": self.get_volunteer_unsubscribe_link(
                    volunteer
                ),
                "FACEBOOK_MESSENGER_LINK": FACEBOOK_MESSENGER_LINK,
                "DISCORD_INVITE_LINK": DISCORD_INVITE_LINK,
                "ONBOARDING_GUIDE_LINK": ONBOARDING_GUIDE_LINK,
                "INSTAGRAM_LINK": INSTAGRAM_LINK,
                "FACEBOOK_PAGE_LINK": FACEBOOK_PAGE_LINK,
            }

            # Render template with variables
            template = Template(self.welcome_template)
            body = template.render(**template_vars)

            subject = self.welcome_email_subject
            to_email = volunteer.email

            # DRY_RUN logic
            if DRY_RUN and to_email != DRY_RUN_EMAIL_RECIPIENT:
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
            if DRY_RUN and to_email != DRY_RUN_EMAIL_RECIPIENT:
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
