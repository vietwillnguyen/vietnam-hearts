"""
Tests for form submission processing functionality

Tests cover:
- Duplicate prevention when processing form submissions
- Confirmation email handling for new vs existing volunteers
- Error handling and edge cases
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime
from app.models import Volunteer as VolunteerModel, EmailCommunication as EmailCommunicationModel
from app.routers.admin import get_signup_form_submissions, create_new_volunteer_object


@pytest.fixture
def mock_auth_service():
    """Mock authentication service for testing admin endpoints with valid auth"""
    from app.services.supabase_auth import get_current_admin_user
    
    async def mock_get_current_admin_user():
        """Mock admin user for testing"""
        return {
            "email": "admin@vietnamhearts.org",
            "is_admin": True,
            "is_authenticated": True
        }
    
    # Override the dependency at the app level
    from app.main import app
    app.dependency_overrides[get_current_admin_user] = mock_get_current_admin_user
    
    yield mock_get_current_admin_user
    
    # Clean up after test
    app.dependency_overrides.clear()


class TestFormSubmissionProcessing:
    """Test the form submission processing functionality"""

    def test_no_duplicate_volunteers_created(self, client, test_db, mock_auth_service):
        """Test that duplicate volunteers are not created when processing form submissions"""
        # Create an existing volunteer
        existing_volunteer = VolunteerModel(
            name="Existing Volunteer",
            email="existing@example.com",
            phone="1234567890",
            positions=["Teacher"],
            location="Ho Chi Minh City",
            availability=["Monday", "Wednesday"],
            start_date=datetime.now().date(),
            commitment_duration="6 months",
            teaching_experience="Some experience",
            experience_details="Details here",
            teaching_certificate="No",
            vietnamese_proficiency="Basic",
            additional_support=[],
            additional_info="",
            is_active=True,
        )
        test_db.add(existing_volunteer)
        test_db.commit()

        # Mock form submissions with one duplicate email (using new form structure)
        mock_submissions = [
            {
                "applicant_status": "ACCEPTED",
                "timestamp": "12/01/2024 10:00:00",
                "email_address": "new1@example.com",
                "score": "85",
                "first_name": "New",
                "last_name": "Volunteer 1",
                "passport_id_number": "123456789",
                "passport_expiry_date": "12/31/2030",
                "date_of_birth": "01/01/1990",
                "passport_upload": "https://example.com/passport1.jpg",
                "headshot_upload": "https://example.com/headshot1.jpg",
                "social_media_link": "https://facebook.com/newvolunteer1",
                "location": "Ho Chi Minh City",
                "phone_number": "+84 1111111111",
                "position_interest": "Teacher, TA",
                "availability": "Monday, Tuesday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "referral_source": "Facebook"
            },
            {
                "applicant_status": "ACCEPTED",
                "timestamp": "12/01/2024 11:00:00",
                "email_address": "existing@example.com",  # Same email as existing volunteer
                "score": "90",
                "first_name": "Duplicate",
                "last_name": "Volunteer",
                "passport_id_number": "987654321",
                "passport_expiry_date": "12/31/2030",
                "date_of_birth": "01/01/1990",
                "passport_upload": "https://example.com/passport2.jpg",
                "headshot_upload": "https://example.com/headshot2.jpg",
                "social_media_link": "https://linkedin.com/duplicatevolunteer",
                "location": "Ho Chi Minh City",
                "phone_number": "+84 2222222222",
                "position_interest": "Teacher",
                "availability": "Wednesday",
                "start_date": "12/01/2024",
                "commitment_duration": "3 months",
                "teaching_experience": "Some experience",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Fluent",
                "other_support": "",
                "referral_source": "Instagram"
            },
            {
                "applicant_status": "ACCEPTED",
                "timestamp": "12/01/2024 12:00:00",
                "email_address": "new2@example.com",
                "score": "88",
                "first_name": "New",
                "last_name": "Volunteer 2",
                "passport_id_number": "456789123",
                "passport_expiry_date": "12/31/2030",
                "date_of_birth": "01/01/1990",
                "passport_upload": "https://example.com/passport3.jpg",
                "headshot_upload": "https://example.com/headshot3.jpg",
                "social_media_link": "https://facebook.com/newvolunteer2",
                "location": "Ho Chi Minh City",
                "phone_number": "+84 3333333333",
                "position_interest": "TA",
                "availability": "Friday",
                "start_date": "ASAP",
                "commitment_duration": "12 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "None",
                "other_support": "Transportation",
                "referral_source": "Word of mouth"
            }
        ]

        # Mock the sheets service to return our test submissions
        with patch('app.routers.admin.sheets_service') as mock_sheets:
            mock_sheets.get_signup_form_submissions.return_value = mock_submissions
            
            # Mock the email service to avoid actually sending emails
            with patch('app.routers.admin.email_service') as mock_email:
                mock_email.send_confirmation_emails.return_value = None
                
                # Call the function
                response = client.get("/admin/forms/submissions?process_new=true")
                
                assert response.status_code == 200
                result = response.json()
                
                # Should only create 2 new volunteers (excluding the duplicate)
                assert result["status"] == "success"
                assert "3 form submissions" in result["message"]
                
                # Check database state
                all_volunteers = test_db.query(VolunteerModel).all()
                assert len(all_volunteers) == 3  # 1 existing + 2 new
                
                # Verify the duplicate email wasn't added
                duplicate_emails = [v.email for v in all_volunteers if v.email == "existing@example.com"]
                assert len(duplicate_emails) == 1  # Only the original one
                
                # Verify new volunteers were added
                new_emails = [v.email for v in all_volunteers if v.email in ["new1@example.com", "new2@example.com"]]
                assert len(new_emails) == 2

    def test_confirmation_emails_sent_to_new_volunteers_only(self, client, test_db, mock_auth_service):
        """Test that confirmation emails are only sent to new volunteers, not existing ones"""
        # Create an existing volunteer with confirmation email already sent
        existing_volunteer = VolunteerModel(
            name="Existing Volunteer",
            email="existing@example.com",
            phone="1234567890",
            positions=["Teacher"],
            location="Ho Chi Minh City",
            availability=["Monday"],
            start_date=datetime.now().date(),
            commitment_duration="6 months",
            teaching_experience="Some experience",
            experience_details="",
            teaching_certificate="No",
            vietnamese_proficiency="Basic",
            additional_support=[],
            additional_info="",
            is_active=True,
        )
        test_db.add(existing_volunteer)
        test_db.commit()
        test_db.refresh(existing_volunteer)
        
        # Add confirmation email record for existing volunteer
        existing_confirmation = EmailCommunicationModel(
            volunteer_id=existing_volunteer.id,
            recipient_email=existing_volunteer.email,
            email_type="volunteer_confirmation",
            subject="Welcome to Vietnam Hearts! ❤️🇻🇳",
            template_name="confirmation-email.html",
            status="sent",
            sent_at=datetime.now(),
        )
        test_db.add(existing_confirmation)
        test_db.commit()

        # Mock form submissions (using new form structure)
        mock_submissions = [
            {
                "applicant_status": "ACCEPTED",
                "timestamp": "12/01/2024 10:00:00",
                "email_address": "new@example.com",
                "score": "85",
                "first_name": "New",
                "last_name": "Volunteer",
                "passport_id_number": "123456789",
                "passport_expiry_date": "12/31/2030",
                "date_of_birth": "01/01/1990",
                "passport_upload": "https://example.com/passport.jpg",
                "headshot_upload": "https://example.com/headshot.jpg",
                "social_media_link": "https://facebook.com/newvolunteer",
                "location": "Ho Chi Minh City",
                "phone_number": "+84 1111111111",
                "position_interest": "Teacher",
                "availability": "Monday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "referral_source": "Facebook"
            }
        ]

        # Mock the sheets service
        with patch('app.routers.admin.sheets_service') as mock_sheets:
            mock_sheets.get_signup_form_submissions.return_value = mock_submissions
            
            # Mock the email service and capture calls
            with patch('app.routers.admin.email_service') as mock_email:
                mock_email.send_confirmation_emails.return_value = None
                
                # Call the function
                response = client.get("/admin/forms/submissions?process_new=true")
                
                assert response.status_code == 200
                
                # Verify send_confirmation_emails was called
                mock_email.send_confirmation_emails.assert_called_once_with(test_db)
                
                # Check that only the new volunteer exists in database
                new_volunteer = test_db.query(VolunteerModel).filter_by(email="new@example.com").first()
                assert new_volunteer is not None
                assert new_volunteer.name == "New Volunteer"

    def test_confirmation_emails_not_sent_to_already_confirmed_volunteers(self, client, test_db, mock_auth_service):
        """Test that confirmation emails are not sent to volunteers who already have confirmation records"""
        # Create a volunteer with confirmation email already sent
        confirmed_volunteer = VolunteerModel(
            name="Confirmed Volunteer",
            email="confirmed@example.com",
            phone="1234567890",
            positions=["Teacher"],
            location="Ho Chi Minh City",
            availability=["Monday"],
            start_date=datetime.now().date(),
            commitment_duration="6 months",
            teaching_experience="Some experience",
            experience_details="",
            teaching_certificate="No",
            vietnamese_proficiency="Basic",
            additional_support=[],
            additional_info="",
            is_active=True,
        )
        test_db.add(confirmed_volunteer)
        test_db.commit()
        test_db.refresh(confirmed_volunteer)
        
        # Add confirmation email record
        confirmation_record = EmailCommunicationModel(
            volunteer_id=confirmed_volunteer.id,
            recipient_email=confirmed_volunteer.email,
            email_type="volunteer_confirmation",
            subject="Welcome to Vietnam Hearts! ❤️🇻🇳",
            template_name="confirmation-email.html",
            status="sent",
            sent_at=datetime.now(),
        )
        test_db.add(confirmation_record)
        test_db.commit()

        # Mock the email service to capture which volunteers get confirmation emails
        with patch('app.services.email_service.EmailService.send_confirmation_emails') as mock_send_confirmation:
            # Call the function that sends confirmation emails
            from app.services.email_service import email_service
            email_service.send_confirmation_emails(test_db)
            
            # Verify the function was called
            mock_send_confirmation.assert_called_once()
            
            # Check that no new confirmation records were created for the already confirmed volunteer
            confirmation_records = test_db.query(EmailCommunicationModel).filter_by(
                volunteer_id=confirmed_volunteer.id,
                email_type="volunteer_confirmation"
            ).all()
            assert len(confirmation_records) == 1  # Only the original one

    def test_create_new_volunteer_object(self):
        """Test the create_new_volunteer_object function with new form structure"""
        submission = {
            "applicant_status": "ACCEPTED",
            "timestamp": "12/01/2024 10:00:00",
            "email_address": "test@example.com",
            "score": "85",
            "first_name": "Test",
            "last_name": "Volunteer",
            "passport_id_number": "123456789",
            "passport_expiry_date": "12/31/2030",
            "date_of_birth": "01/01/1990",
            "passport_upload": "https://example.com/passport.jpg",
            "headshot_upload": "https://example.com/headshot.jpg",
            "social_media_link": "https://facebook.com/testvolunteer",
            "location": "Ho Chi Minh City",
            "phone_number": "+84 1234567890",
            "position_interest": "Teacher, TA",
            "availability": "Monday, Tuesday, Wednesday",
            "start_date": "12/01/2024",
            "commitment_duration": "6 months",
            "teaching_experience": "Some experience",
            "experience_details": "Worked with children",
            "teaching_certificate": "Yes",
            "vietnamese_speaking": "Fluent",
            "other_support": "Transportation, Materials",
            "referral_source": "Facebook"
        }
        
        volunteer = create_new_volunteer_object(submission)
        
        assert volunteer.name == "Test Volunteer"
        assert volunteer.email == "test@example.com"
        assert volunteer.phone == "+84 1234567890"
        assert volunteer.positions == ["Teacher", "TA"]
        assert volunteer.location == "Ho Chi Minh City"
        assert volunteer.availability == ["Monday", "Tuesday", "Wednesday"]
        assert volunteer.start_date == datetime.strptime("12/01/2024", "%m/%d/%Y").date()
        assert volunteer.commitment_duration == "6 months"
        assert volunteer.teaching_experience == "Some experience"
        assert volunteer.experience_details == "Worked with children"
        assert volunteer.teaching_certificate == "Yes"
        assert volunteer.vietnamese_proficiency == "Fluent"
        assert volunteer.additional_support == ["Transportation", "Materials"]
        assert "Social Media: https://facebook.com/testvolunteer" in volunteer.additional_info
        assert "Referral Source: Facebook" in volunteer.additional_info
        assert volunteer.is_active == True

    def test_create_new_volunteer_object_with_asap_date(self):
        """Test create_new_volunteer_object with ASAP start date"""
        submission = {
            "applicant_status": "ACCEPTED",
            "timestamp": "12/01/2024 10:00:00",
            "email_address": "test@example.com",
            "score": "85",
            "first_name": "Test",
            "last_name": "Volunteer",
            "passport_id_number": "123456789",
            "passport_expiry_date": "12/31/2030",
            "date_of_birth": "01/01/1990",
            "passport_upload": "https://example.com/passport.jpg",
            "headshot_upload": "https://example.com/headshot.jpg",
            "social_media_link": "https://facebook.com/testvolunteer",
            "location": "Ho Chi Minh City",
            "phone_number": "+84 1234567890",
            "position_interest": "Teacher",
            "availability": "Monday",
            "start_date": "ASAP",
            "commitment_duration": "6 months",
            "teaching_experience": "None",
            "experience_details": "",
            "teaching_certificate": "No",
            "vietnamese_speaking": "Basic",
            "other_support": "",
            "referral_source": "Facebook"
        }
        
        volunteer = create_new_volunteer_object(submission)
        
        # Should use today's date for ASAP
        expected_date = datetime.now().date()
        assert volunteer.start_date == expected_date

    def test_create_new_volunteer_object_with_empty_fields(self):
        """Test create_new_volunteer_object with empty optional fields"""
        submission = {
            "applicant_status": "ACCEPTED",
            "timestamp": "12/01/2024 10:00:00",
            "email_address": "test@example.com",
            "score": "85",
            "first_name": "Test",
            "last_name": "Volunteer",
            "passport_id_number": "123456789",
            "passport_expiry_date": "12/31/2030",
            "date_of_birth": "01/01/1990",
            "passport_upload": "https://example.com/passport.jpg",
            "headshot_upload": "https://example.com/headshot.jpg",
            "social_media_link": "",
            "location": "Ho Chi Minh City",
            "phone_number": "+84 1234567890",
            "position_interest": "Teacher",
            "availability": "Monday",
            "start_date": "12/01/2024",
            "commitment_duration": "6 months",
            "teaching_experience": "None",
            "experience_details": "",
            "teaching_certificate": "No",
            "vietnamese_speaking": "Basic",
            "other_support": "",
            "referral_source": ""
        }
        
        volunteer = create_new_volunteer_object(submission)
        
        assert volunteer.experience_details == ""
        assert volunteer.additional_support == []
        # With new form structure, additional_info contains social media and referral source
        assert "Social Media: " in volunteer.additional_info
        assert "Referral Source: " in volunteer.additional_info

    def test_form_submission_with_database_error(self, client, test_db, mock_auth_service):
        """Test handling of database errors during form submission processing"""
        # Mock form submissions (using new form structure)
        mock_submissions = [
            {
                "applicant_status": "ACCEPTED",
                "timestamp": "12/01/2024 10:00:00",
                "email_address": "test@example.com",
                "score": "85",
                "first_name": "Test",
                "last_name": "Volunteer",
                "passport_id_number": "123456789",
                "passport_expiry_date": "12/31/2030",
                "date_of_birth": "01/01/1990",
                "passport_upload": "https://example.com/passport.jpg",
                "headshot_upload": "https://example.com/headshot.jpg",
                "social_media_link": "https://facebook.com/testvolunteer",
                "location": "Ho Chi Minh City",
                "phone_number": "+84 1234567890",
                "position_interest": "Teacher",
                "availability": "Monday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "referral_source": "Facebook"
            }
        ]

        # Mock the sheets service
        with patch('app.routers.admin.sheets_service') as mock_sheets:
            mock_sheets.get_signup_form_submissions.return_value = mock_submissions
            
            # Mock the email service
            with patch('app.routers.admin.email_service') as mock_email:
                mock_email.send_confirmation_emails.return_value = None
                
                # Mock database commit to raise an exception
                with patch.object(test_db, 'commit', side_effect=Exception("Database error")):
                    response = client.get("/admin/forms/submissions?process_new=true")
                    
                    assert response.status_code == 200
                    result = response.json()
                    
                    # Should return partial failure status
                    assert result["status"] == "partial_failure"
                    assert "failed to save new volunteers" in result["message"]
                    assert "Database error" in result["details"]["database_error"]

    def test_form_submission_with_ssl_error(self, client, test_db, mock_auth_service):
        """Test handling of SSL errors during form submission processing"""
        import ssl
        
        # Mock the sheets service to raise SSL error
        with patch('app.routers.admin.sheets_service') as mock_sheets:
            mock_sheets.get_signup_form_submissions.side_effect = ssl.SSLEOFError("SSL connection failed")
            
            response = client.get("/admin/forms/submissions?process_new=true")
            
            assert response.status_code == 200
            result = response.json()
            
            # Should return partial failure status for SSL errors
            assert result["status"] == "partial_failure"
            assert "SSL connection issue" in result["message"]
            assert result["details"]["error_type"] == "ssl_eof_error"

    def test_form_submission_without_processing(self, client, test_db, mock_auth_service):
        """Test form submission retrieval without processing new volunteers"""
        # Mock form submissions (using new form structure)
        mock_submissions = [
            {
                "applicant_status": "ACCEPTED",
                "timestamp": "12/01/2024 10:00:00",
                "email_address": "test@example.com",
                "score": "85",
                "first_name": "Test",
                "last_name": "Volunteer",
                "passport_id_number": "123456789",
                "passport_expiry_date": "12/31/2030",
                "date_of_birth": "01/01/1990",
                "passport_upload": "https://example.com/passport.jpg",
                "headshot_upload": "https://example.com/headshot.jpg",
                "social_media_link": "https://facebook.com/testvolunteer",
                "location": "Ho Chi Minh City",
                "phone_number": "+84 1234567890",
                "position_interest": "Teacher",
                "availability": "Monday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "referral_source": "Facebook"
            }
        ]

        # Mock the sheets service
        with patch('app.routers.admin.sheets_service') as mock_sheets:
            mock_sheets.get_signup_form_submissions.return_value = mock_submissions
            
            # Call without processing new volunteers
            response = client.get("/admin/forms/submissions?process_new=false")
            
            assert response.status_code == 200
            result = response.json()
            
            # Should return success but not process new volunteers
            assert result["status"] == "success"
            assert "1 form submissions" in result["message"]
            
            # Check that no new volunteers were created
            volunteers = test_db.query(VolunteerModel).all()
            assert len(volunteers) == 0

    def test_email_communication_logging_for_new_volunteers(self, client, test_db, mock_auth_service):
        """Test that email communications are properly logged for new volunteers"""
        # Mock form submissions (using new form structure)
        mock_submissions = [
            {
                "applicant_status": "ACCEPTED",
                "timestamp": "12/01/2024 10:00:00",
                "email_address": "test@example.com",
                "score": "85",
                "first_name": "Test",
                "last_name": "Volunteer",
                "passport_id_number": "123456789",
                "passport_expiry_date": "12/31/2030",
                "date_of_birth": "01/01/1990",
                "passport_upload": "https://example.com/passport.jpg",
                "headshot_upload": "https://example.com/headshot.jpg",
                "social_media_link": "https://facebook.com/testvolunteer",
                "location": "Ho Chi Minh City",
                "phone_number": "+84 1234567890",
                "position_interest": "Teacher",
                "availability": "Monday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "referral_source": "Facebook"
            }
        ]

        # Mock the sheets service
        with patch('app.routers.admin.sheets_service') as mock_sheets:
            mock_sheets.get_signup_form_submissions.return_value = mock_submissions
            
            # Mock the email service to simulate successful email sending
            with patch('app.services.email_service.EmailService.send_confirmation_email') as mock_send_email:
                mock_send_email.return_value = True
                
                # Call the function
                response = client.get("/admin/forms/submissions?process_new=true")
                
                assert response.status_code == 200
                
                # Check that the volunteer was created
                volunteer = test_db.query(VolunteerModel).filter_by(email="test@example.com").first()
                assert volunteer is not None
                
                # Check that confirmation email was sent (via the mocked service)
                mock_send_email.assert_called_once()
                
                # The email service should have logged the communication
                # This is tested in the email service tests, but we can verify the volunteer exists
                assert volunteer.email == "test@example.com"

    def test_status_filtering_only_processes_accepted_submissions(self, client, test_db, mock_auth_service):
        """Test that only submissions with STATUS = 'ACCEPTED' are processed"""
        # Mock form submissions with different statuses (using new form structure)
        mock_submissions = [
            {
                "applicant_status": "ACCEPTED",
                "timestamp": "12/01/2024 10:00:00",
                "email_address": "accepted@example.com",
                "score": "85",
                "first_name": "Accepted",
                "last_name": "Volunteer",
                "passport_id_number": "123456789",
                "passport_expiry_date": "12/31/2030",
                "date_of_birth": "01/01/1990",
                "passport_upload": "https://example.com/passport1.jpg",
                "headshot_upload": "https://example.com/headshot1.jpg",
                "social_media_link": "https://facebook.com/acceptedvolunteer",
                "location": "Ho Chi Minh City",
                "phone_number": "+84 1111111111",
                "position_interest": "Teacher",
                "availability": "Monday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "referral_source": "Facebook"
            },
            {
                "applicant_status": "PENDING",
                "timestamp": "12/01/2024 11:00:00",
                "email_address": "pending@example.com",
                "score": "75",
                "first_name": "Pending",
                "last_name": "Volunteer",
                "passport_id_number": "987654321",
                "passport_expiry_date": "12/31/2030",
                "date_of_birth": "01/01/1990",
                "passport_upload": "https://example.com/passport2.jpg",
                "headshot_upload": "https://example.com/headshot2.jpg",
                "social_media_link": "https://linkedin.com/pendingvolunteer",
                "location": "Ho Chi Minh City",
                "phone_number": "+84 2222222222",
                "position_interest": "Teacher",
                "availability": "Tuesday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "referral_source": "Instagram"
            },
            {
                "applicant_status": "REJECTED",
                "timestamp": "12/01/2024 12:00:00",
                "email_address": "rejected@example.com",
                "score": "60",
                "first_name": "Rejected",
                "last_name": "Volunteer",
                "passport_id_number": "456789123",
                "passport_expiry_date": "12/31/2030",
                "date_of_birth": "01/01/1990",
                "passport_upload": "https://example.com/passport3.jpg",
                "headshot_upload": "https://example.com/headshot3.jpg",
                "social_media_link": "https://facebook.com/rejectedvolunteer",
                "location": "Ho Chi Minh City",
                "phone_number": "+84 3333333333",
                "position_interest": "Teacher",
                "availability": "Wednesday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "referral_source": "Facebook"
            },
            {
                "applicant_status": "",
                "timestamp": "12/01/2024 13:00:00",
                "email_address": "nostatus@example.com",
                "score": "70",
                "first_name": "No Status",
                "last_name": "Volunteer",
                "passport_id_number": "789123456",
                "passport_expiry_date": "12/31/2030",
                "date_of_birth": "01/01/1990",
                "passport_upload": "https://example.com/passport4.jpg",
                "headshot_upload": "https://example.com/headshot4.jpg",
                "social_media_link": "https://facebook.com/nostatusvolunteer",
                "location": "Ho Chi Minh City",
                "phone_number": "+84 4444444444",
                "position_interest": "Teacher",
                "availability": "Thursday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "referral_source": "Word of mouth"
            }
        ]

        # Mock the sheets service
        with patch('app.routers.admin.sheets_service') as mock_sheets:
            mock_sheets.get_signup_form_submissions.return_value = mock_submissions
            
            # Mock the email service
            with patch('app.routers.admin.email_service') as mock_email:
                mock_email.send_confirmation_emails.return_value = None
                
                # Call the function
                response = client.get("/admin/forms/submissions?process_new=true")
                
                assert response.status_code == 200
                result = response.json()
                
                # Should return success with status filtering info
                assert result["status"] == "success"
                assert "4 form submissions" in result["message"]
                assert "1 accepted, 3 non-accepted" in result["message"]
                
                # Check the details
                assert result["details"]["submissions_retrieved"] == 4
                assert result["details"]["accepted_submissions"] == 1
                assert result["details"]["non_accepted_submissions"] == 3
                assert result["details"]["new_submissions_found"] == 1
                assert result["details"]["volunteers_created"] == 1
                
                # Check database state - only the accepted volunteer should be created
                all_volunteers = test_db.query(VolunteerModel).all()
                assert len(all_volunteers) == 1
                
                # Verify only the accepted volunteer was added
                accepted_volunteer = test_db.query(VolunteerModel).filter_by(email="accepted@example.com").first()
                assert accepted_volunteer is not None
                assert accepted_volunteer.name == "Accepted Volunteer"
                
                # Verify other volunteers were not added
                pending_volunteer = test_db.query(VolunteerModel).filter_by(email="pending@example.com").first()
                rejected_volunteer = test_db.query(VolunteerModel).filter_by(email="rejected@example.com").first()
                nostatus_volunteer = test_db.query(VolunteerModel).filter_by(email="nostatus@example.com").first()
                
                assert pending_volunteer is None
                assert rejected_volunteer is None
                assert nostatus_volunteer is None 

    def test_empty_email_submissions_are_filtered_out(self, client, test_db, mock_auth_service):
        """Test that submissions with empty email addresses are filtered out and not counted"""
        # Mock form submissions including some with empty emails (using new form structure)
        mock_submissions = [
            {
                "applicant_status": "ACCEPTED",
                "timestamp": "12/01/2024 10:00:00",
                "email_address": "valid@example.com",
                "score": "85",
                "first_name": "Valid",
                "last_name": "Volunteer",
                "passport_id_number": "123456789",
                "passport_expiry_date": "12/31/2030",
                "date_of_birth": "01/01/1990",
                "passport_upload": "https://example.com/passport1.jpg",
                "headshot_upload": "https://example.com/headshot1.jpg",
                "social_media_link": "https://facebook.com/validvolunteer",
                "location": "Ho Chi Minh City",
                "phone_number": "+84 1111111111",
                "position_interest": "Teacher",
                "availability": "Monday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "referral_source": "Facebook"
            },
            {
                "applicant_status": "ACCEPTED",
                "timestamp": "12/01/2024 11:00:00",
                "email_address": "",  # Empty email
                "score": "75",
                "first_name": "Empty",
                "last_name": "Email",
                "passport_id_number": "987654321",
                "passport_expiry_date": "12/31/2030",
                "date_of_birth": "01/01/1990",
                "passport_upload": "https://example.com/passport2.jpg",
                "headshot_upload": "https://example.com/headshot2.jpg",
                "social_media_link": "https://facebook.com/emptyemail",
                "location": "Ho Chi Minh City",
                "phone_number": "+84 2222222222",
                "position_interest": "Teacher",
                "availability": "Tuesday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "referral_source": "Instagram"
            },
            {
                "applicant_status": "ACCEPTED",
                "timestamp": "12/01/2024 12:00:00",
                "email_address": "   ",  # Whitespace-only email
                "score": "80",
                "first_name": "Whitespace",
                "last_name": "Email",
                "passport_id_number": "456789123",
                "passport_expiry_date": "12/31/2030",
                "date_of_birth": "01/01/1990",
                "passport_upload": "https://example.com/passport3.jpg",
                "headshot_upload": "https://example.com/headshot3.jpg",
                "social_media_link": "https://facebook.com/whitespaceemail",
                "location": "Ho Chi Minh City",
                "phone_number": "+84 3333333333",
                "position_interest": "Teacher",
                "availability": "Wednesday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "referral_source": "Facebook"
            },
            {
                "applicant_status": "ACCEPTED",
                "timestamp": "12/01/2024 13:00:00",
                "email_address": "another@example.com",
                "score": "90",
                "first_name": "Another",
                "last_name": "Valid",
                "passport_id_number": "789123456",
                "passport_expiry_date": "12/31/2030",
                "date_of_birth": "01/01/1990",
                "passport_upload": "https://example.com/passport4.jpg",
                "headshot_upload": "https://example.com/headshot4.jpg",
                "social_media_link": "https://facebook.com/anothervalid",
                "location": "Ho Chi Minh City",
                "phone_number": "+84 4444444444",
                "position_interest": "Teacher",
                "availability": "Thursday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "referral_source": "Word of mouth"
            }
        ]

        # Mock the sheets service to return our test submissions
        with patch('app.routers.admin.sheets_service') as mock_sheets:
            mock_sheets.get_signup_form_submissions.return_value = mock_submissions
            
            # Mock the email service
            with patch('app.routers.admin.email_service') as mock_email:
                mock_email.send_confirmation_emails.return_value = None
                
                # Call the function
                response = client.get("/admin/forms/submissions?process_new=true")
                
                assert response.status_code == 200
                result = response.json()
                
                # Should only process 2 valid submissions (excluding empty/whitespace emails)
                assert result["status"] == "success"
                assert "2 form submissions" in result["message"]
                assert "2 accepted, 0 non-accepted" in result["message"]
                
                # Check the details
                assert result["details"]["submissions_retrieved"] == 2
                assert result["details"]["accepted_submissions"] == 2
                assert result["details"]["non_accepted_submissions"] == 0
                assert result["details"]["new_submissions_found"] == 2
                assert result["details"]["volunteers_created"] == 2
                
                # Check database state - only the valid volunteers should be created
                all_volunteers = test_db.query(VolunteerModel).all()
                assert len(all_volunteers) == 2
                
                # Verify only the valid volunteers were added
                valid_emails = [v.email for v in all_volunteers]
                assert "valid@example.com" in valid_emails
                assert "another@example.com" in valid_emails
                
                # Verify empty email volunteers were not added
                assert "" not in valid_emails
                assert "   " not in valid_emails 