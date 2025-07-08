import secrets

from app.models import Volunteer as VolunteerModel, EmailCommunication as EmailCommunicationModel


class TestDatabaseIntegrity:
    """Test database operations and integrity"""
    
    def test_volunteer_creation_and_retrieval(self, test_db):
        """Test that volunteers can be created and retrieved correctly"""
        volunteer = VolunteerModel(
            name="Database Test Volunteer",
            email="dbtest@example.com",
            positions=["Teacher"],
            teaching_experience="None",
            email_unsubscribe_token=secrets.token_urlsafe(32),
            weekly_reminders_subscribed=True,
            all_emails_subscribed=True,
            is_active=True
        )
        
        test_db.add(volunteer)
        test_db.commit()
        test_db.refresh(volunteer)
        
        # Verify volunteer was created
        assert volunteer.id is not None
        
        # Retrieve volunteer from database
        retrieved_volunteer = test_db.query(VolunteerModel).filter_by(
            email="dbtest@example.com"
        ).first()
        
        assert retrieved_volunteer is not None
        assert retrieved_volunteer.name == "Database Test Volunteer"
        assert retrieved_volunteer.is_active == True
    
    def test_email_communication_logging(self, test_db, mock_volunteer):
        """Test that email communications are properly logged"""
        # Create an email communication record
        email_comm = EmailCommunicationModel(
            volunteer_id=mock_volunteer.id,
            recipient_email=mock_volunteer.email,
            email_type="test_email",
            status="sent",
            subject="Test Email",
            template_name="test_template"
        )
        test_db.add(email_comm)
        test_db.commit()
        test_db.refresh(email_comm)
        
        # Verify email communication was logged
        assert email_comm.id is not None
        
        # Retrieve and verify
        retrieved_comm = test_db.query(EmailCommunicationModel).filter_by(
            volunteer_id=mock_volunteer.id,
            email_type="test_email"
        ).first()
        
        assert retrieved_comm is not None
        assert retrieved_comm.status == "sent"
        assert retrieved_comm.subject == "Test Email"
    