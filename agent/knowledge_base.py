"""
Knowledge base for Vietnam Hearts Agent.
Provides specific information about classes, locations, and schedules.
"""

import logging
from re import S
from typing import Dict, List, Optional
from pathlib import Path
import sys
from .config import KB_CONFIDENCE_THRESHOLD, VOLUNTEER_KEYWORDS, FAQ_KEYWORDS

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
        from .config import NEW_USER_SIGNUP_LINK
        
        return {
            "organization": {
                "name": "Vietnam Hearts",
                "description": "Vietnam Hearts is a collective of volunteers that provides education and support to underprivileged children in Vietnam. We primarily focus in Saigon area but we also work with other communities when opportunities come.",
                "mission": "Serve the community and help underprivileged children in Vietnam through education and support",
            },
            "locations": {
                "primary": "Vietnam (Binh Thanh, Ho Chi Minh City)",
                "details": "Due to safety reasons, we do not disclose the exact location of our classes until you signed up and joined our community. We do our best to work with other communities in Saigon and nearby areas when we can."
            },
            "volunteer_roles": {
                "teachers": "Lead English classes for children",
                "teaching_assistants": "Support teachers in the classroom",
                "non_teaching": "Administrative support, social media,fundraising, and other roles",
                "accomodations": "we provide accomodations for lead teachers up to 75K VND for their expenses like food, transportation, etc. the day they teach! Other accomodations like visa, flights, etc. are not covered."
            },
            "requirements": {
                "teaching": "No formal teaching certificate required, but experience helpful",
                "language": "Basic English proficiency required, Basic Vietnamese proficiency helpful",
                "commitment": "No minimum commitment required - volunteer when you can",
                "experience": "While teaching experience is helpful, we believe anybody with a big heart and a willingness to learn can be a great help."
            },
            "donations" : {
                "donations_supplies_description": "Donations like food and supplies are accepted, but this will require us to contact a team member to be involved, and we will get back to you shortly.",
                "donations_cash_description": "Donations go to support our community in different ways. GoFundMe is for our international donations supporting directly for the school and students for food or school supplies, BuyMeACoffee is used for supporting our teachers and volunteers. If the donation is with VND person, We accept cash or bank transfer, this will require us to contact a team member to be involved, and we will get back to you shortly.",
                "donation_international_link": "https://www.gofundme.com/f/help-feed-and-provide-education-for-kids-in-saigon-vietnam?fbclid=PAZXh0bgNhZW0CMTEAAaexN53ysVnAbBtULcaQtqtcLDYD49R_eBSaU0pJgA7R8bItk_P_mjxI-b9sRg_aem_P7OLNodGP0DNkCtGLoml-Q",
                "donation_buymeacoffee_link": "https://www.buymeacoffee.com/vietnamhearts"
            }
        }
    
    def get_class_schedule(self) -> str:
        """Get formatted class schedule information"""
        classes = self.class_config.get("classes", {})
        
        if not classes:
            return "Class schedules are being updated. Please contact our team for current information."
        
        schedule_text = "📚 **Primary Class Schedule:**\n\n"
        
        for grade, config in classes.items():
            time = config.get("time", "TBD")
            schedule_text += f"• **{grade}**: {time}\n"
        
        schedule_text += "\n*Schedules can be flexible depending on the needs of the community. Sign up to stay updated about different class availabilities.*"
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
        volunteer_text += f"• **Experience**: {requirements['experience']}\n\n"
        
        volunteer_text += "**Accommodations:**\n"
        volunteer_text += f"• {roles['accomodations']}\n\n"
        
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
        org_text += f"**Description**: {org['description']}\n\n"
        org_text += f"**Mission**: {org['mission']}\n\n"
        org_text += "**What We Do:**\n"
        org_text += "• Provide education and support to underprivileged children\n"
        org_text += "• Support local schools and communities\n"
        org_text += "• Create opportunities for local and international volunteers\n"
        org_text += "• Build bridges between cultures\n"
        org_text += "• Provide a safe and supportive environment for children\n\n"
        org_text += "We believe every child deserves access to education, and we are committed to providing a safe and supportive environment for children."
        
        return org_text
    
    def get_donation_info(self) -> str:
        """Get donation information"""
        donations = self.general_info["donations"]
        
        donation_text = "💝 **Support Vietnam Hearts:**\n\n"
        donation_text += "**How Donations Help:**\n"
        donation_text += f"• **Supplies & Food**: {donations['donations_supplies_description']}\n\n"
        donation_text += f"• **Financial Support**: {donations['donations_cash_description']}\n\n"
        
        donation_text += "**Donation Links:**\n"
        donation_text += f"• **International Donations (GoFundMe)**: {donations['donation_international_link']}\n"
        donation_text += f"• **Support Teachers & Volunteers (Buy Me a Coffee)**: {donations['donation_buymeacoffee_link']}\n\n"
        
        donation_text += "*Note: For VND donations (cash or bank transfer), we will contact a team member to get back to you shortly on this.*"
        
        return donation_text
    
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
        location_keywords = [kw for kw in FAQ_KEYWORDS if kw in ["location", "where", "address", "place", "district"]]
        if any(word in query_lower for word in location_keywords):
            return {
                "content": self.get_location_info(),
                "source": "Vietnam Hearts Knowledge Base - Location Information",
                "confidence": KB_CONFIDENCE_THRESHOLD,
                "type": "location"
            }
        
        # Hours/schedule-related queries
        schedule_keywords = [kw for kw in FAQ_KEYWORDS if kw in ["when", "time", "schedule", "hours", "class time", "what time"]]
        if any(word in query_lower for word in schedule_keywords):
            return {
                "content": self.get_hours_info(),
                "source": "Vietnam Hearts Knowledge Base - Class Schedule & Hours",
                "confidence": KB_CONFIDENCE_THRESHOLD,
                "type": "schedule"
            }
        
        # Donation-related queries
        donation_keywords = ["donate", "donation", "money", "support", "fund", "funding", "give", "contribute", "financial"]
        if any(word in query_lower for word in donation_keywords):
            return {
                "content": self.get_donation_info(),
                "source": "Vietnam Hearts Knowledge Base - Donation Information",
                "confidence": KB_CONFIDENCE_THRESHOLD,
                "type": "donation"
            }
        
        # Volunteer-related queries (using config keywords)
        if any(word in query_lower for word in VOLUNTEER_KEYWORDS):
            return {
                "content": self.get_volunteer_info(),
                "source": "Vietnam Hearts Knowledge Base - Volunteer Opportunities",
                "confidence": KB_CONFIDENCE_THRESHOLD,
                "type": "volunteer"
            }
        
        # FAQ-related queries (using config keywords)
        if any(word in query_lower for word in FAQ_KEYWORDS):
            # Default to organization info for general FAQ queries
            content = self.get_organization_info()
            source = "Vietnam Hearts Knowledge Base - Organization Information"
            kb_type = "organization"
            
            return {
                "content": content,
                "source": source,
                "confidence": KB_CONFIDENCE_THRESHOLD,
                "type": kb_type
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