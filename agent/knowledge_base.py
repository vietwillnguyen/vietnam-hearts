"""
Knowledge base for Vietnam Hearts Agent.
Provides specific information about classes, locations, and schedules.
"""

import logging
from typing import Dict, List, Optional
from pathlib import Path
import sys

# Add the project root to Python path to import app modules
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

logger = logging.getLogger(__name__)


class VietnamHeartsKnowledgeBase:
    """Knowledge base for Vietnam Hearts specific information"""
    
    def __init__(self):
        """Initialize the knowledge base"""
        self.class_config = self._load_class_config()
        self.general_info = self._load_general_info()
        
    def _load_class_config(self) -> Dict:
        """Load class configuration from the app"""
        try:
            from app.services.classes_config import CLASS_CONFIG, SPECIAL_KEYS
            return {
                "classes": CLASS_CONFIG,
                "special_keys": SPECIAL_KEYS
            }
        except ImportError as e:
            logger.warning(f"Could not load class config: {e}")
            # Fallback configuration
            return {
                "classes": {
                    "Grade 1": {"sheet_range": "B7:G10", "time": "9:30 - 10:30 AM"},
                    "Grade 4": {"sheet_range": "B12:G15", "time": "9:30 - 10:30 AM"},
                },
                "special_keys": {
                    "optional": "Optional Day",
                    "Need Volunteers": "❌ Missing Teacher and/or TA's",
                }
            }
    
    def _load_general_info(self) -> Dict:
        """Load general information about Vietnam Hearts"""
        return {
            "organization": {
                "name": "Vietnam Hearts",
                "mission": "Help underprivileged children in Vietnam through education and support",
                "focus": "Teaching English and providing educational support to children in Vietnam"
            },
            "locations": {
                "primary": "Vietnam (Binh Thanh, Ho Chi Minh City)",
                "details": "Due to safety reasons, we do not disclose the exact location of our classes until you signed up and joined our community. We do our best to work with other communities when opportunities come."
            },
            "volunteer_roles": {
                "teachers": "Lead English classes for children",
                "teaching_assistants": "Support teachers in the classroom",
                "non_teaching": "Administrative support, fundraising, and other roles"
            },
            "requirements": {
                "teaching": "No formal teaching certificate required, but experience helpful",
                "language": "Basic English proficiency required, Basic Vietnamese proficiency helpful",
                "commitment": "Flexible scheduling available",
                "training": "Experience is best learned in person, if you are new just step in as a Teaching Assistant and we will help you learn."
            }
        }
    
    def get_class_schedule(self) -> str:
        """Get formatted class schedule information"""
        classes = self.class_config.get("classes", {})
        
        if not classes:
            return "Class schedules are being updated. Please contact our team for current information."
        
        schedule_text = "📚 **Current Class Schedule:**\n\n"
        
        for grade, config in classes.items():
            time = config.get("time", "TBD")
            schedule_text += f"• **{grade}**: {time}\n"
        
        schedule_text += "\n*Schedules may vary by location and availability.*"
        return schedule_text
    
    def get_location_info(self) -> str:
        """Get location information"""
        locations = self.general_info["locations"]
        
        location_text = f"📍 **Where We Work:**\n\n"
        location_text += f"• **Primary Location**: {locations['primary']}\n"
        location_text += f"• **Details**: {locations['details']}\n\n"
        location_text += "We partner with schools and communities across Vietnam to provide educational support."
        
        return location_text
    
    def get_hours_info(self) -> str:
        """Get information about volunteer hours and scheduling"""
        classes = self.class_config.get("classes", {})
        
        hours_text = "🕐 **Main Volunteer Hours & Scheduling:**\n\n"
        
        if classes:
            hours_text += "**Current Class Times:**\n"
            for grade, config in classes.items():
                time = config.get("time", "TBD")
                hours_text += f"• {grade}: {time}\n"
            hours_text += "\n"
        
        hours_text += "**Flexible Scheduling:**\n"
        hours_text += "• We primarily teach in person at our location in Binh Thanh, Ho Chi Minh City, but we also do other volunteering events when we can.\n"
        hours_text += "• You can choose your preferred time slots\n"
        hours_text += "• No minimum commitment required - volunteer when you can\n\n"
        
        return hours_text
    
    def get_volunteer_info(self) -> str:
        """Get comprehensive volunteer information"""
        roles = self.general_info["volunteer_roles"]
        requirements = self.general_info["requirements"]
        
        volunteer_text = "🙋‍♀️ **Volunteer Opportunities:**\n\n"
        volunteer_text += "**Available Roles:**\n"
        volunteer_text += f"• **Teachers**: {roles['teachers']}\n"
        volunteer_text += f"• **Teaching Assistants**: {roles['teaching_assistants']}\n"
        volunteer_text += f"• **Non-Teaching Roles**: {roles['non_teaching']}\n\n"
        
        volunteer_text += "**Requirements:**\n"
        volunteer_text += f"• **Teaching**: {requirements['teaching']}\n"
        volunteer_text += f"• **Language**: {requirements['language']}\n"
        volunteer_text += f"• **Commitment**: {requirements['commitment']}\n"
        volunteer_text += f"• **Training**: {requirements['training']}\n\n"
        
        volunteer_text += "**Benefits:**\n"
        volunteer_text += "• Make a real difference in children's lives\n"
        volunteer_text += "• Gain teaching experience\n"
        volunteer_text += "• Join a supportive community\n"
        volunteer_text += "• Flexible scheduling\n"
        
        return volunteer_text
    
    def get_organization_info(self) -> str:
        """Get general organization information"""
        org = self.general_info["organization"]
        
        org_text = f"💙 **About {org['name']}:**\n\n"
        org_text += f"**Mission**: {org['mission']}\n\n"
        org_text += f"**Focus**: {org['focus']}\n\n"
        org_text += "**What We Do:**\n"
        org_text += "• Provide education and support to underprivileged children\n"
        org_text += "• Support local schools and communities\n"
        org_text += "• Create opportunities for local and international volunteers\n"
        org_text += "• Build bridges between cultures\n"
        org_text += "• Provide a safe and supportive environment for children\n\n"
        org_text += "We believe every child deserves access to quality education!"
        
        return org_text
    
    def search_knowledge(self, query: str) -> Optional[Dict]:
        """
        Search knowledge base for relevant information
        
        Args:
            query: User's question
            
        Returns:
            Dictionary with information and source, or None if not found
        """
        query_lower = query.lower()
        
        # Location-related queries
        if any(word in query_lower for word in ["location", "where", "place", "address"]):
            return {
                "content": self.get_location_info(),
                "source": "Vietnam Hearts Knowledge Base - Location Information",
                "confidence": 0.95,
                "type": "location"
            }
        
        # Hours/schedule-related queries
        if any(word in query_lower for word in ["hours", "time", "schedule", "when", "class time"]):
            return {
                "content": self.get_hours_info(),
                "source": "Vietnam Hearts Knowledge Base - Class Schedule & Hours",
                "confidence": 0.95,
                "type": "schedule"
            }
        
        # Volunteer-related queries
        if any(word in query_lower for word in ["volunteer", "help", "teach", "role", "position"]):
            return {
                "content": self.get_volunteer_info(),
                "source": "Vietnam Hearts Knowledge Base - Volunteer Opportunities",
                "confidence": 0.95,
                "type": "volunteer"
            }
        
        # Organization-related queries
        if any(word in query_lower for word in ["what is", "about", "organization", "mission"]):
            return {
                "content": self.get_organization_info(),
                "source": "Vietnam Hearts Knowledge Base - Organization Information",
                "confidence": 0.95,
                "type": "organization"
            }
        
        # Class schedule queries
        if any(word in query_lower for word in ["class", "grade", "schedule"]):
            return {
                "content": self.get_class_schedule(),
                "source": "Vietnam Hearts Knowledge Base - Class Schedule",
                "confidence": 0.95,
                "type": "schedule"
            }
        
        return None
    
    def get_specific_answer(self, question: str) -> str:
        """
        Get a specific answer for a question
        
        Args:
            question: User's question
            
        Returns:
            Specific answer or fallback response
        """
        # Try to find specific information
        specific_info = self.search_knowledge(question)
        
        if specific_info:
            return specific_info["content"]
        
        # Fallback for general questions
        return "I don't have specific information about that, but I'd be happy to connect you with our team who can help! You can also check our FAQ or contact us directly." 