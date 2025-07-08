from app.models import EmailCommunication as EmailCommunicationModel

class TestUpdateEmailPreferences:
    """Test the POST /public/unsubscribe endpoint"""
    
    def test_unsubscribe_weekly_reminders(self, client, test_db, mock_volunteer):
        """Test unsubscribing from weekly reminders only"""
        response = client.post(
            f"/public/unsubscribe?token={mock_volunteer.email_unsubscribe_token}",
            data={"unsubscribe_type": "weekly_reminders"}
        )
        
        assert response.status_code == 200
        print(response.text)
        assert "unsubscribed from weekly reminders" in response.text
        
        # Check database state
        test_db.refresh(mock_volunteer)
        assert mock_volunteer.weekly_reminders_subscribed == False
        assert mock_volunteer.all_emails_subscribed == True
        assert mock_volunteer.is_active == True
        
        # Check email communication was logged
        email_comm = test_db.query(EmailCommunicationModel).filter_by(
            volunteer_id=mock_volunteer.id,
            email_type="preference_update_weekly_reminders"
        ).first()
        assert email_comm is not None
        assert email_comm.status == "sent"
    
    def test_unsubscribe_all_emails(self, client, test_db, mock_volunteer):
        """Test unsubscribing from all emails"""
        response = client.post(
            f"/public/unsubscribe?token={mock_volunteer.email_unsubscribe_token}",
            data={"unsubscribe_type": "all_emails"}
        )
        
        assert response.status_code == 200
        assert "unsubscribed from all emails" in response.text
        assert "account has been deactivated" in response.text
        
        # Check database state
        test_db.refresh(mock_volunteer)
        assert mock_volunteer.weekly_reminders_subscribed == False
        assert mock_volunteer.all_emails_subscribed == False
        assert mock_volunteer.is_active == False
        
        # Check email communication was logged
        email_comm = test_db.query(EmailCommunicationModel).filter_by(
            volunteer_id=mock_volunteer.id,
            email_type="preference_update_all_emails"
        ).first()
        assert email_comm is not None
        assert email_comm.status == "sent"
    
    def test_resubscribe_all_emails(self, client, test_db, mock_inactive_volunteer):
        """Test resubscribing to all emails"""
        response = client.post(
            f"/public/unsubscribe?token={mock_inactive_volunteer.email_unsubscribe_token}",
            data={"unsubscribe_type": "resubscribe"}
        )
        
        assert response.status_code == 200
        assert "resubscribed to all emails" in response.text
        assert "account has been reactivated" in response.text
        
        # Check database state
        test_db.refresh(mock_inactive_volunteer)
        assert mock_inactive_volunteer.weekly_reminders_subscribed == True
        assert mock_inactive_volunteer.all_emails_subscribed == True
        assert mock_inactive_volunteer.is_active == True
        
        # Check email communication was logged
        email_comm = test_db.query(EmailCommunicationModel).filter_by(
            volunteer_id=mock_inactive_volunteer.id,
            email_type="preference_update_resubscribe"
        ).first()
        assert email_comm is not None
        assert email_comm.status == "sent"
    
    def test_invalid_unsubscribe_type(self, client, mock_volunteer):
        """Test handling of invalid unsubscribe type"""
        response = client.post(
            f"/public/unsubscribe?token={mock_volunteer.email_unsubscribe_token}",
            data={"unsubscribe_type": "invalid_type"}
        )
        
        # Should return an error or redirect to error page
        assert response.status_code in [400, 422]
    
    def test_post_with_invalid_token(self, client):
        """Test POST request with invalid token"""
        response = client.post(
            "/public/unsubscribe?token=invalid_token",
            data={"unsubscribe_type": "all_emails"}
        )
        
        assert response.status_code == 400
        assert "Invalid or expired unsubscribe link" in response.text
    
    def test_post_without_token(self, client):
        """Test POST request without token"""
        response = client.post(
            "/public/unsubscribe",
            data={"unsubscribe_type": "all_emails"}
        )
        
        assert response.status_code == 422  # FastAPI validation error