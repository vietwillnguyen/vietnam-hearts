"""
Admin volunteer management endpoints
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List, Dict, Any
from datetime import datetime

from app.database import get_db
from app.models import (
    Volunteer as VolunteerModel,
    EmailCommunication as EmailCommunicationModel,
)
from app.schemas import Volunteer
from app.services.email_service import email_service
from app.utils.logging_config import get_api_logger
from app.utils.config_helper import ConfigHelper
from app.routers.admin.helpers import get_volunteer_summary

logger = get_api_logger()

router = APIRouter()


@router.get("/volunteers")
def view_volunteers(db: Session = Depends(get_db)):
    """View all volunteers and their email status"""
    try:
        volunteers = db.query(VolunteerModel).options(
            joinedload(VolunteerModel.email_communications)
        ).all()
        volunteer_data = get_volunteer_summary(volunteers)
        return {
            "status": "success",
            "total_volunteers": len(volunteer_data),
            "volunteers": volunteer_data,
        }
    except Exception as e:
        logger.error(f"Failed to fetch volunteer data: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch volunteer data: {str(e)}")


@router.get(
    "/volunteers/active",
    summary="List active volunteers",
    response_model=List[Volunteer],
)
def list_active_volunteers(db: Session = Depends(get_db)):
    """Get only active volunteers"""
    return db.query(VolunteerModel).filter(VolunteerModel.is_active == True).all()


@router.get("/volunteers/announcement-recipients")
def get_announcement_recipients(db: Session = Depends(get_db)):
    """Get all active volunteers subscribed to announcements"""
    try:
        recipients = (
            db.query(VolunteerModel)
            .filter(VolunteerModel.is_active == True)
            .filter(VolunteerModel.all_emails_subscribed == True)
            .order_by(VolunteerModel.name)
            .all()
        )
        recipient_data = [
            {
                "id": v.id,
                "name": v.name,
                "email": v.email,
                "weekly_reminders_subscribed": v.weekly_reminders_subscribed,
                "all_emails_subscribed": v.all_emails_subscribed,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in recipients
        ]
        return {
            "status": "success",
            "total_recipients": len(recipient_data),
            "recipients": recipient_data,
            "message": f"Found {len(recipient_data)} volunteers subscribed to announcements",
        }
    except Exception as e:
        logger.error(f"Failed to get announcement recipients: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get announcement recipients: {str(e)}")


@router.get(
    "/volunteers/{volunteer_id}",
    summary="Get volunteer details",
    response_model=Volunteer,
)
def get_volunteer_by_id(volunteer_id: int, db: Session = Depends(get_db)):
    volunteer = db.query(VolunteerModel).filter(VolunteerModel.id == volunteer_id).first()
    if not volunteer:
        raise HTTPException(status_code=404, detail="Volunteer not found")
    return volunteer


@router.post("/volunteers/{volunteer_id}/send-confirmation")
def send_confirmation_email_to_volunteer(volunteer_id: int, db: Session = Depends(get_db)):
    """Manually send a confirmation email to a specific volunteer"""
    try:
        volunteer = db.query(VolunteerModel).filter(VolunteerModel.id == volunteer_id).first()
        if not volunteer:
            raise HTTPException(status_code=404, detail="Volunteer not found")

        existing_confirmation = (
            db.query(EmailCommunicationModel)
            .filter(
                EmailCommunicationModel.volunteer_id == volunteer_id,
                EmailCommunicationModel.email_type == "volunteer_confirmation",
                EmailCommunicationModel.status == "sent",
            )
            .first()
        )
        if existing_confirmation:
            return {
                "status": "info",
                "message": f"Confirmation email already sent to {volunteer.email} on {existing_confirmation.sent_at}",
                "volunteer_email": volunteer.email,
                "last_sent": existing_confirmation.sent_at.isoformat(),
            }

        success = email_service.send_confirmation_email(db, volunteer)
        if success:
            return {
                "status": "success",
                "message": f"Confirmation email sent successfully to {volunteer.email}",
                "volunteer_email": volunteer.email,
            }
        raise HTTPException(status_code=500, detail=f"Failed to send confirmation email to {volunteer.email}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to send confirmation email to volunteer {volunteer_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to send confirmation email: {str(e)}")


@router.post("/volunteers/{volunteer_id}/reset-confirmation")
def reset_confirmation_email_status(volunteer_id: int, db: Session = Depends(get_db)):
    """Reset confirmation email status for a volunteer"""
    try:
        volunteer = db.query(VolunteerModel).filter(VolunteerModel.id == volunteer_id).first()
        if not volunteer:
            raise HTTPException(status_code=404, detail="Volunteer not found")

        deleted_count = (
            db.query(EmailCommunicationModel)
            .filter(
                EmailCommunicationModel.volunteer_id == volunteer_id,
                EmailCommunicationModel.email_type == "volunteer_confirmation",
            )
            .delete()
        )
        db.commit()
        return {
            "status": "success",
            "message": f"Confirmation email status reset for {volunteer.email}",
            "volunteer_email": volunteer.email,
            "deleted_records": deleted_count,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to reset confirmation status for volunteer {volunteer_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to reset confirmation status: {str(e)}")


@router.post("/volunteers/{volunteer_id}/resubscribe")
def resubscribe_volunteer(volunteer_id: int, db: Session = Depends(get_db)):
    """Resubscribe a volunteer to all emails"""
    try:
        volunteer = db.query(VolunteerModel).filter(VolunteerModel.id == volunteer_id).first()
        if not volunteer:
            raise HTTPException(status_code=404, detail="Volunteer not found")

        volunteer.weekly_reminders_subscribed = True
        volunteer.all_emails_subscribed = True
        db.add(EmailCommunicationModel(
            volunteer_id=volunteer.id,
            recipient_email=volunteer.email,
            email_type="resubscribe_all",
            subject="Resubscribe Request - All Emails",
            status="sent",
            sent_at=datetime.now(),
        ))
        db.commit()
        logger.info(f"Volunteer {volunteer.email} resubscribed to all emails by admin")
        return {
            "status": "success",
            "message": f"Volunteer {volunteer.email} has been resubscribed to all emails",
            "volunteer_email": volunteer.email,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to resubscribe volunteer {volunteer_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to resubscribe volunteer: {str(e)}")


@router.post("/volunteers/{volunteer_id}/resubscribe-weekly")
def resubscribe_volunteer_weekly(volunteer_id: int, db: Session = Depends(get_db)):
    """Resubscribe a volunteer to weekly reminders only"""
    try:
        volunteer = db.query(VolunteerModel).filter(VolunteerModel.id == volunteer_id).first()
        if not volunteer:
            raise HTTPException(status_code=404, detail="Volunteer not found")

        volunteer.weekly_reminders_subscribed = True
        db.add(EmailCommunicationModel(
            volunteer_id=volunteer.id,
            recipient_email=volunteer.email,
            email_type="resubscribe_weekly",
            subject="Resubscribe Request - Weekly Reminders",
            status="sent",
            sent_at=datetime.now(),
        ))
        db.commit()
        logger.info(f"Volunteer {volunteer.email} resubscribed to weekly reminders by admin")
        return {
            "status": "success",
            "message": f"Volunteer {volunteer.email} has been resubscribed to weekly reminders",
            "volunteer_email": volunteer.email,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to resubscribe volunteer {volunteer_id} to weekly: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to resubscribe volunteer: {str(e)}")


@router.post("/volunteers/{volunteer_id}/deactivate")
def deactivate_volunteer(volunteer_id: int, db: Session = Depends(get_db)):
    """Soft-delete a volunteer by setting is_active to False"""
    try:
        volunteer = db.query(VolunteerModel).filter(VolunteerModel.id == volunteer_id).first()
        if not volunteer:
            raise HTTPException(status_code=404, detail="Volunteer not found")

        if not volunteer.is_active:
            return {"status": "info", "message": f"Volunteer {volunteer.email} is already deactivated", "volunteer_email": volunteer.email}

        volunteer.is_active = False
        db.add(EmailCommunicationModel(
            volunteer_id=volunteer.id,
            recipient_email=volunteer.email,
            email_type="volunteer_deactivated",
            subject="Volunteer Account Deactivated",
            status="sent",
            sent_at=datetime.now(),
        ))
        db.commit()
        logger.info(f"Volunteer {volunteer.email} deactivated by admin")
        return {"status": "success", "message": f"Volunteer {volunteer.email} has been deactivated", "volunteer_email": volunteer.email}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to deactivate volunteer {volunteer_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to deactivate volunteer: {str(e)}")


@router.post("/volunteers/{volunteer_id}/reactivate")
def reactivate_volunteer(volunteer_id: int, db: Session = Depends(get_db)):
    """Reactivate a volunteer"""
    try:
        volunteer = db.query(VolunteerModel).filter(VolunteerModel.id == volunteer_id).first()
        if not volunteer:
            raise HTTPException(status_code=404, detail="Volunteer not found")

        if volunteer.is_active:
            return {"status": "info", "message": f"Volunteer {volunteer.email} is already active", "volunteer_email": volunteer.email}

        volunteer.is_active = True
        db.add(EmailCommunicationModel(
            volunteer_id=volunteer.id,
            recipient_email=volunteer.email,
            email_type="volunteer_reactivated",
            subject="Volunteer Account Reactivated",
            status="sent",
            sent_at=datetime.now(),
        ))
        db.commit()
        logger.info(f"Volunteer {volunteer.email} reactivated by admin")
        return {"status": "success", "message": f"Volunteer {volunteer.email} has been reactivated", "volunteer_email": volunteer.email}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to reactivate volunteer {volunteer_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to reactivate volunteer: {str(e)}")


@router.post("/volunteers/{volunteer_id}/send-weekly-reminder")
def send_weekly_reminder_to_volunteer(volunteer_id: int, db: Session = Depends(get_db)):
    """Send a weekly reminder email to a specific volunteer"""
    try:
        if not ConfigHelper.get_weekly_reminders_enabled(db):
            raise HTTPException(
                status_code=400,
                detail="Weekly reminders are currently disabled globally. Enable them in the Settings tab to send weekly reminders.",
            )

        volunteer = db.query(VolunteerModel).filter(VolunteerModel.id == volunteer_id).first()
        if not volunteer:
            raise HTTPException(status_code=404, detail="Volunteer not found")
        if not volunteer.is_active:
            raise HTTPException(status_code=400, detail=f"Volunteer {volunteer.email} is not active")
        if not volunteer.weekly_reminders_subscribed:
            raise HTTPException(status_code=400, detail=f"Volunteer {volunteer.email} is not subscribed to weekly reminders")

        html_body, subject = email_service.build_weekly_reminder_content(volunteer, db)
        success = email_service.send_custom_email(
            to_email=volunteer.email,
            subject=subject,
            html_body=html_body,
            db=db,
            volunteer_id=volunteer.id,
            email_type="weekly_reminder",
        )
        if success:
            logger.info(f"Weekly reminder email sent to {volunteer.email}")
            return {
                "status": "success",
                "message": f"Weekly reminder email sent to {volunteer.email}",
                "volunteer_id": volunteer_id,
                "email": volunteer.email,
            }
        raise HTTPException(status_code=500, detail=f"Failed to send weekly reminder email to {volunteer.email}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to send weekly reminder to volunteer {volunteer_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to send weekly reminder email: {str(e)}")


@router.post("/volunteers/cleanup-malformed-emails")
def cleanup_malformed_emails(db: Session = Depends(get_db), dry_run: bool = True):
    """
    Find and remove volunteer records whose email field does not contain '@'.
    These were created by syncs that ran with an incorrect column mapping
    (email field was reading the timestamp or LLM judge score column instead).

    Pass dry_run=false to actually delete. Default is dry_run=true (preview only).
    After deletion, re-run sync to re-import correctly from the sheet.
    """
    bad_volunteers = (
        db.query(VolunteerModel)
        .filter(~VolunteerModel.email.contains("@"))
        .all()
    )

    preview = [
        {"id": v.id, "name": v.name, "bad_email": v.email}
        for v in bad_volunteers
    ]

    if dry_run:
        return {
            "status": "preview",
            "dry_run": True,
            "count": len(preview),
            "records": preview,
            "message": f"Would delete {len(preview)} volunteers. Pass dry_run=false to confirm.",
        }

    # Delete email_communications first (FK constraint)
    deleted_comms = 0
    for v in bad_volunteers:
        deleted_comms += (
            db.query(EmailCommunicationModel)
            .filter(EmailCommunicationModel.volunteer_id == v.id)
            .delete()
        )

    deleted_volunteers = (
        db.query(VolunteerModel)
        .filter(~VolunteerModel.email.contains("@"))
        .delete()
    )
    db.commit()

    logger.info(
        f"Cleaned up {deleted_volunteers} malformed-email volunteers "
        f"and {deleted_comms} related email communication records"
    )
    return {
        "status": "success",
        "dry_run": False,
        "deleted_volunteers": deleted_volunteers,
        "deleted_email_communications": deleted_comms,
        "message": (
            f"Deleted {deleted_volunteers} volunteers with malformed emails. "
            "Run sync-volunteers to re-import them with correct data."
        ),
    }
