"""
Tests for email service functionality

Tests cover:
- Max assistants enforcement in class tables
- Email template rendering
- Assistant counting logic
"""

import pytest
from unittest.mock import patch, MagicMock
from app.services.email_service import EmailService
from app.models import Volunteer as VolunteerModel


class TestEmailService:
    """Test the email service functionality"""

    def test_build_class_table_with_max_assistants_enforcement(self):
        """Test that build_class_table enforces max_assistants limit from config"""
        email_service = EmailService()
        
        # Mock config with max_assistants = 2
        config = {
            "sheet_range": "B7:G11",
            "time": "9:30 - 10:30 AM",
            "room": "Downstairs",
            "max_assistants": 2,
            "notes": "Test class"
        }
        
        # Mock sheet service to return test data
        mock_sheet_service = MagicMock()
        mock_sheet_service.get_schedule_range.return_value = [
            ["", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],  # Header row
            ["Teacher", "John Doe", "Jane Smith", "Bob Wilson", "Alice Brown", "Charlie Davis"],  # Teacher row
            ["Head TA", "Head TA 1", "Head TA 2", "Head TA 3", "Head TA 4", "Head TA 5"],  # Head TA row
            ["Assistants", "TA1, TA2, TA3", "TA1", "TA1, TA2", "", "TA1, TA2, TA3, TA4"]  # Assistant row
        ]
        
        # Mock database session
        mock_db = MagicMock()
        
        # Call the method
        result = email_service.build_class_table("Test Class", config, mock_sheet_service, mock_db)
        
        # Verify the result
        assert result["has_data"] == True
        assert result["class_name"] == "Test Class"
        
        # Check that the table HTML includes max assistants info
        table_html = result["table_html"]
        assert "Max 2 Assistants" in table_html
        
        # Check that the status messages reflect the assistant count
        assert "Fully Covered (3/2 assistants)" in table_html  # Monday: 3 assistants > max 2
        assert "Partially Covered (1/2 assistants)" in table_html  # Tuesday: 1 assistant < max 2
        assert "Fully Covered (2/2 assistants)" in table_html  # Wednesday: 2 assistants = max 2
        assert "Partially Covered (0/2 assistants)" in table_html  # Thursday: 0 assistants < max 2
        assert "Fully Covered (4/2 assistants)" in table_html  # Friday: 4 assistants > max 2

    def test_build_class_table_assistant_counting_logic(self):
        """Test that assistant counting logic works correctly with various formats"""
        email_service = EmailService()
        
        # Mock config
        config = {
            "sheet_range": "B7:G11",
            "time": "9:30 - 10:30 AM",
            "max_assistants": 3,
            "notes": "Test class"
        }
        
        # Mock sheet service with various assistant formats
        mock_sheet_service = MagicMock()
        mock_sheet_service.get_schedule_range.return_value = [
            ["", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            ["Teacher", "John Doe", "Jane Smith", "Bob Wilson", "Alice Brown", "Charlie Davis"],
            ["Head TA", "Head TA 1", "Head TA 2", "Head TA 3", "Head TA 4", "Head TA 5"],
            ["Assistants", "TA1, TA2, TA3", "TA1", "TA1, TA2, TA3, TA4", "", "TA1, TA2"]
        ]
        
        mock_db = MagicMock()
        
        # Call the method
        result = email_service.build_class_table("Test Class", config, mock_sheet_service, mock_db)
        
        # Verify assistant counting
        table_html = result["table_html"]
        
        # Monday: 3 assistants = max 3
        assert "Fully Covered (3/3 assistants)" in table_html
        
        # Tuesday: 1 assistant < max 3
        assert "Partially Covered (1/3 assistants)" in table_html
        
        # Wednesday: 4 assistants > max 3
        assert "Fully Covered (4/3 assistants)" in table_html
        
        # Thursday: 0 assistants < max 3
        assert "Partially Covered (0/3 assistants)" in table_html
        
        # Friday: 2 assistants < max 3
        assert "Partially Covered (2/3 assistants)" in table_html

    def test_build_class_table_with_empty_assistants(self):
        """Test that empty assistant fields are handled correctly"""
        email_service = EmailService()
        
        config = {
            "sheet_range": "B7:G11",
            "time": "9:30 - 10:30 AM",
            "max_assistants": 2,
            "notes": "Test class"
        }
        
        mock_sheet_service = MagicMock()
        mock_sheet_service.get_schedule_range.return_value = [
            ["", "Monday", "Tuesday"],
            ["Teacher", "John Doe", "Jane Smith"],
            ["Head TA", "Head TA 1", "Head TA 2"],
            ["Assistants", "", "   "]  # Empty and whitespace-only assistants
        ]
        
        mock_db = MagicMock()
        
        result = email_service.build_class_table("Test Class", config, mock_sheet_service, mock_db)
        
        # Both days should show 0 assistants
        table_html = result["table_html"]
        assert "Partially Covered (0/2 assistants)" in table_html

    def test_build_class_table_with_default_max_assistants(self):
        """Test that default max_assistants (3) is used when not specified in config"""
        email_service = EmailService()
        
        # Config without max_assistants
        config = {
            "sheet_range": "B7:G11",
            "time": "9:30 - 10:30 AM",
            "notes": "Test class"
        }
        
        mock_sheet_service = MagicMock()
        mock_sheet_service.get_schedule_range.return_value = [
            ["", "Monday"],
            ["Teacher", "John Doe"],
            ["Head TA", "Head TA 1"],
            ["Assistants", "TA1, TA2, TA3, TA4"]  # 4 assistants > default max 3
        ]
        
        mock_db = MagicMock()
        
        result = email_service.build_class_table("Test Class", config, mock_sheet_service, mock_db)
        
        # Should use default max of 3
        table_html = result["table_html"]
        assert "Max 3 Assistants" in table_html
        assert "Fully Covered (4/3 assistants)" in table_html

    def test_build_class_table_missing_teacher_status(self):
        """Test status when teacher is missing (need volunteers)"""
        email_service = EmailService()
        
        config = {
            "sheet_range": "B7:G11",
            "time": "9:30 - 10:30 AM",
            "max_assistants": 3,
            "notes": "Test class"
        }
        
        mock_sheet_service = MagicMock()
        mock_sheet_service.get_schedule_range.return_value = [
            ["", "Monday", "Tuesday"],
            ["Teacher", "Need Volunteers", "John Doe"],
            ["Head TA", "Head TA 1", "Head TA 2"],
            ["Assistants", "TA1, TA2", "TA1"]
        ]
        
        mock_db = MagicMock()
        
        result = email_service.build_class_table("Test Class", config, mock_sheet_service, mock_db)
        
        table_html = result["table_html"]
        # Monday should show missing teacher status
        assert "❌ Missing Teacher" in table_html
        # Tuesday should show normal status
        assert "Partially Covered (1/3 assistants)" in table_html

    def test_build_class_table_missing_head_ta_status(self):
        """Test status when head TA is missing"""
        email_service = EmailService()
        
        config = {
            "sheet_range": "B7:G11",
            "time": "9:30 - 10:30 AM",
            "max_assistants": 3,
            "notes": "Test class"
        }
        
        mock_sheet_service = MagicMock()
        mock_sheet_service.get_schedule_range.return_value = [
            ["", "Monday", "Tuesday"],
            ["Teacher", "John Doe", "Jane Smith"],
            ["Head TA", "", "Need Volunteers"],  # Empty and need volunteers
            ["Assistants", "TA1, TA2", "TA1"]
        ]
        
        mock_db = MagicMock()
        
        result = email_service.build_class_table("Test Class", config, mock_sheet_service, mock_db)
        
        table_html = result["table_html"]
        # Both days should show missing head TA status
        assert "❌ Missing Head TA" in table_html
        assert table_html.count("❌ Missing Head TA") == 2

    def test_build_class_table_missing_assistants_status(self):
        """Test status when assistants are missing"""
        email_service = EmailService()
        
        config = {
            "sheet_range": "B7:G11",
            "time": "9:30 - 10:30 AM",
            "max_assistants": 3,
            "notes": "Test class"
        }
        
        mock_sheet_service = MagicMock()
        mock_sheet_service.get_schedule_range.return_value = [
            ["", "Monday", "Tuesday"],
            ["Teacher", "John Doe", "Jane Smith"],
            ["Head TA", "Head TA 1", "Head TA 2"],
            ["Assistants", "Need Volunteers", "TA1"]  # Need volunteers for assistants
        ]
        
        mock_db = MagicMock()
        
        result = email_service.build_class_table("Test Class", config, mock_sheet_service, mock_db)
        
        table_html = result["table_html"]
        # Monday should show missing assistants status
        assert "❌ Missing TA's" in table_html
        # Tuesday should show normal status
        assert "Partially Covered (1/3 assistants)" in table_html

    def test_build_class_table_optional_day_status(self):
        """Test status for optional days"""
        email_service = EmailService()
        
        config = {
            "sheet_range": "B7:G11",
            "time": "9:30 - 10:30 AM",
            "max_assistants": 3,
            "notes": "Test class"
        }
        
        mock_sheet_service = MagicMock()
        mock_sheet_service.get_schedule_range.return_value = [
            ["", "Monday", "Tuesday"],
            ["Teacher", "Optional Day", "John Doe"],
            ["Head TA", "Head TA 1", "Head TA 2"],
            ["Assistants", "TA1", "TA1, TA2"]
        ]
        
        mock_db = MagicMock()
        
        result = email_service.build_class_table("Test Class", config, mock_sheet_service, mock_db)
        
        table_html = result["table_html"]
        # Monday should show optional day status
        assert "optional day, volunteers welcome to support existing classes" in table_html
        # Tuesday should show normal status
        assert "Partially Covered (2/3 assistants)" in table_html

    def test_build_class_table_no_class_status(self):
        """Test status for no class days"""
        email_service = EmailService()
        
        config = {
            "sheet_range": "B7:G11",
            "time": "9:30 - 10:30 AM",
            "max_assistants": 3,
            "notes": "Test class"
        }
        
        mock_sheet_service = MagicMock()
        mock_sheet_service.get_schedule_range.return_value = [
            ["", "Monday", "Tuesday"],
            ["Teacher", "No Class", "No Class - Holiday"],
            ["Head TA", "Head TA 1", "Head TA 2"],
            ["Assistants", "TA1", "TA1"]
        ]
        
        mock_db = MagicMock()
        
        result = email_service.build_class_table("Test Class", config, mock_sheet_service, mock_db)
        
        table_html = result["table_html"]
        # Monday should show no class status
        assert "No class" in table_html
        # Tuesday should show holiday status
        assert "No class: holiday" in table_html

    def test_build_class_table_no_limit_assistants(self):
        """Test status when max_assistants is None (no limit)"""
        email_service = EmailService()
        
        config = {
            "sheet_range": "B7:G11",
            "time": "9:30 - 10:30 AM",
            "max_assistants": None,  # No limit
            "notes": "Test class"
        }
        
        mock_sheet_service = MagicMock()
        mock_sheet_service.get_schedule_range.return_value = [
            ["", "Monday", "Tuesday"],
            ["Teacher", "John Doe", "Jane Smith"],
            ["Head TA", "Head TA 1", "Head TA 2"],
            ["Assistants", "TA1, TA2, TA3", "TA1"]
        ]
        
        mock_db = MagicMock()
        
        result = email_service.build_class_table("Test Class", config, mock_sheet_service, mock_db)
        
        table_html = result["table_html"]
        # Both days should show no limit status
        assert "3 assistant(s) signed up - No limit, TA's welcome to join" in table_html
        assert "1 assistant(s) signed up - No limit, TA's welcome to join" in table_html

    def test_build_class_table_status_priority_order(self):
        """Test that status conditions are checked in the correct priority order"""
        email_service = EmailService()
        
        config = {
            "sheet_range": "B7:G11",
            "time": "9:30 - 10:30 AM",
            "max_assistants": 3,
            "notes": "Test class"
        }
        
        # Test case where teacher says "need volunteers" but head TA and assistants are also missing
        # Should prioritize missing teacher over other issues
        mock_sheet_service = MagicMock()
        mock_sheet_service.get_schedule_range.return_value = [
            ["", "Monday"],
            ["Teacher", "Need Volunteers"],
            ["Head TA", "Need Volunteers"],  # Also missing
            ["Assistants", "Need Volunteers"]  # Also missing
        ]
        
        mock_db = MagicMock()
        
        result = email_service.build_class_table("Test Class", config, mock_sheet_service, mock_db)
        
        table_html = result["table_html"]
        # Should show missing teacher, not missing head TA or assistants
        assert "❌ Missing Teacher" in table_html
        assert "❌ Missing Head TA" not in table_html
        assert "❌ Missing TA's" not in table_html

    def test_build_class_table_case_insensitive_status_detection(self):
        """Test that status detection is case insensitive"""
        email_service = EmailService()
        
        config = {
            "sheet_range": "B7:G11",
            "time": "9:30 - 10:30 AM",
            "max_assistants": 3,
            "notes": "Test class"
        }
        
        mock_sheet_service = MagicMock()
        mock_sheet_service.get_schedule_range.return_value = [
            ["", "Monday", "Tuesday", "Wednesday"],
            ["Teacher", "NEED VOLUNTEERS", "optional day", "NO CLASS"],
            ["Head TA", "Head TA 1", "Head TA 2", "Head TA 3"],
            ["Assistants", "TA1", "TA1", "TA1"]
        ]
        
        mock_db = MagicMock()
        
        result = email_service.build_class_table("Test Class", config, mock_sheet_service, mock_db)
        
        table_html = result["table_html"]
        # Should detect all statuses regardless of case
        assert "❌ Missing Teacher" in table_html
        assert "optional day, volunteers welcome to support existing classes" in table_html
        assert "No class" in table_html 