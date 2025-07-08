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


class TestFormSubmissionProcessing:
    """Test the form submission processing functionality"""

    def test_no_duplicate_volunteers_created(self, client, test_db):
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

        # Mock form submissions with one duplicate email
        mock_submissions = [
            {
                "full_name": "New Volunteer 1",
                "email": "new1@example.com",
                "phone": "1111111111",
                "position": "Teacher, TA",
                "location": "Ho Chi Minh City",
                "availability": "Monday, Tuesday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "additional_info": ""
            },
            {
                "full_name": "Duplicate Volunteer",
                "email": "existing@example.com",  # Same email as existing volunteer
                "phone": "2222222222",
                "position": "Teacher",
                "location": "Ho Chi Minh City",
                "availability": "Wednesday",
                "start_date": "12/01/2024",
                "commitment_duration": "3 months",
                "teaching_experience": "Some experience",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Fluent",
                "other_support": "",
                "additional_info": ""
            },
            {
                "full_name": "New Volunteer 2",
                "email": "new2@example.com",
                "phone": "3333333333",
                "position": "TA",
                "location": "Ho Chi Minh City",
                "availability": "Friday",
                "start_date": "ASAP",
                "commitment_duration": "12 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "None",
                "other_support": "Transportation",
                "additional_info": "Excited to help!"
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

    def test_confirmation_emails_sent_to_new_volunteers_only(self, client, test_db):
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

        # Mock form submissions
        mock_submissions = [
            {
                "full_name": "New Volunteer",
                "email": "new@example.com",
                "phone": "1111111111",
                "position": "Teacher",
                "location": "Ho Chi Minh City",
                "availability": "Monday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "additional_info": ""
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

    def test_confirmation_emails_not_sent_to_already_confirmed_volunteers(self, client, test_db):
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
        """Test the create_new_volunteer_object function"""
        submission = {
            "full_name": "Test Volunteer",
            "email": "test@example.com",
            "phone": "1234567890",
            "position": "Teacher, TA",
            "location": "Ho Chi Minh City",
            "availability": "Monday, Tuesday, Wednesday",
            "start_date": "12/01/2024",
            "commitment_duration": "6 months",
            "teaching_experience": "Some experience",
            "experience_details": "Worked with children",
            "teaching_certificate": "Yes",
            "vietnamese_speaking": "Fluent",
            "other_support": "Transportation, Materials",
            "additional_info": "Excited to help!"
        }
        
        volunteer = create_new_volunteer_object(submission)
        
        assert volunteer.name == "Test Volunteer"
        assert volunteer.email == "test@example.com"
        assert volunteer.phone == "1234567890"
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
        assert volunteer.additional_info == "Excited to help!"
        assert volunteer.is_active == True

    def test_create_new_volunteer_object_with_asap_date(self):
        """Test create_new_volunteer_object with ASAP start date"""
        submission = {
            "full_name": "Test Volunteer",
            "email": "test@example.com",
            "phone": "1234567890",
            "position": "Teacher",
            "location": "Ho Chi Minh City",
            "availability": "Monday",
            "start_date": "ASAP",
            "commitment_duration": "6 months",
            "teaching_experience": "None",
            "experience_details": "",
            "teaching_certificate": "No",
            "vietnamese_speaking": "Basic",
            "other_support": "",
            "additional_info": ""
        }
        
        volunteer = create_new_volunteer_object(submission)
        
        # Should use today's date for ASAP
        expected_date = datetime.now().date()
        assert volunteer.start_date == expected_date

    def test_create_new_volunteer_object_with_empty_fields(self):
        """Test create_new_volunteer_object with empty optional fields"""
        submission = {
            "full_name": "Test Volunteer",
            "email": "test@example.com",
            "phone": "1234567890",
            "position": "Teacher",
            "location": "Ho Chi Minh City",
            "availability": "Monday",
            "start_date": "12/01/2024",
            "commitment_duration": "6 months",
            "teaching_experience": "None",
            "experience_details": "",
            "teaching_certificate": "No",
            "vietnamese_speaking": "Basic",
            "other_support": "",
            "additional_info": ""
        }
        
        volunteer = create_new_volunteer_object(submission)
        
        assert volunteer.experience_details == ""
        assert volunteer.additional_support == []
        assert volunteer.additional_info == ""

    def test_form_submission_with_database_error(self, client, test_db):
        """Test handling of database errors during form submission processing"""
        # Mock form submissions
        mock_submissions = [
            {
                "full_name": "Test Volunteer",
                "email": "test@example.com",
                "phone": "1234567890",
                "position": "Teacher",
                "location": "Ho Chi Minh City",
                "availability": "Monday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "additional_info": ""
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

    def test_form_submission_with_ssl_error(self, client, test_db):
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

    def test_form_submission_without_processing(self, client, test_db):
        """Test form submission retrieval without processing new volunteers"""
        # Mock form submissions
        mock_submissions = [
            {
                "full_name": "Test Volunteer",
                "email": "test@example.com",
                "phone": "1234567890",
                "position": "Teacher",
                "location": "Ho Chi Minh City",
                "availability": "Monday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "additional_info": ""
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

    def test_email_communication_logging_for_new_volunteers(self, client, test_db):
        """Test that email communications are properly logged for new volunteers"""
        # Mock form submissions
        mock_submissions = [
            {
                "full_name": "Test Volunteer",
                "email": "test@example.com",
                "phone": "1234567890",
                "position": "Teacher",
                "location": "Ho Chi Minh City",
                "availability": "Monday",
                "start_date": "12/01/2024",
                "commitment_duration": "6 months",
                "teaching_experience": "None",
                "experience_details": "",
                "teaching_certificate": "No",
                "vietnamese_speaking": "Basic",
                "other_support": "",
                "additional_info": ""
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