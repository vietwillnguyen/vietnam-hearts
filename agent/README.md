# Vietnam Hearts Agent 🤖

An intelligent AI agent for handling Facebook Messenger and Instagram messages for Vietnam Hearts, powered by Google Gemini.

## Features

### 🧠 Intelligent Intent Detection
- **Hybrid Detection**: Combines keyword-based and AI-powered intent detection
- **Multiple Intents**: Recognizes volunteer interest, FAQ questions, and unknown queries
- **Confidence Scoring**: Provides confidence levels for each detected intent
- **Context Awareness**: Maintains conversation context for better responses

### 💬 Smart Response Generation
- **Intent-Based Responses**: Generates appropriate responses based on detected intent
- **Knowledge Base Integration**: Provides specific information about Vietnam Hearts classes, locations, and schedules
- **AI-Powered FAQ**: Uses Gemini AI to answer general questions about Vietnam Hearts
- **Quick Reply Buttons**: Provides interactive quick reply options
- **Template System**: Configurable response templates for consistency

### 📊 Conversation Management
- **Conversation Tracking**: Maintains conversation history and context
- **User Management**: Tracks users across different platforms
- **Message Logging**: Logs all interactions for analysis and improvement
- **Escalation Handling**: Automatically escalates complex queries to human team

### 🔌 Platform Integration
- **Facebook Messenger**: Full webhook support for Messenger
- **Instagram**: Support for Instagram Business messaging
- **REST API**: Clean API endpoints for custom integrations
- **Webhook Processing**: Handles webhook events from social platforms

## Architecture

```
📥 Incoming Message
    ↓
🔍 Intent Detection (Keywords + AI)
    ↓
📚 Knowledge Base Check
    ↓
🧠 Response Generation (Specific Info or AI)
    ↓
📤 Send Response + Quick Replies
    ↓
📊 Log Conversation
```

## Quick Start

### 1. Install Dependencies

```bash
# Install Google Gemini
poetry add google-generativeai

# Or with pip
pip install google-generativeai
```

### 2. Set Environment Variables

Add to your `.env` file:

```env
# Required
GEMINI_API_KEY=your_gemini_api_key_here
NEW_USER_SIGNUP_LINK=https://your-signup-form.com

# Optional
GEMINI_MODEL=gemini-1.5-flash
FACEBOOK_MESSENGER_LINK=https://facebook.com/vietnamhearts
INSTAGRAM_LINK=https://instagram.com/vietnamhearts
FACEBOOK_PAGE_LINK=https://facebook.com/vietnamhearts
```

### 3. Test the Agent

```bash
# Run the test script
python agent/test_agent.py
```

### 4. Start the API Server

```bash
# The agent is automatically included in your main FastAPI app
uvicorn app.main:app --reload
```

## API Endpoints

### Health Check
```
GET /agent/health
```

### Process Message
```
POST /agent/message
{
  "user_id": "12345",
  "platform": "messenger",
  "message_text": "I want to volunteer",
  "user_name": "John Doe"
}
```

### Process Quick Reply
```
POST /agent/quick-reply?user_id=12345&platform=messenger&payload=SIGNUP
```

### Webhook Handler
```
POST /agent/webhook
```
Handles Facebook Messenger/Instagram webhooks automatically.

### Test Endpoint
```
POST /agent/test
```
Runs a comprehensive test of the agent with sample messages.

## Hybrid Workflow Implementation

The agent now follows a sophisticated hybrid approach that combines knowledge base lookups with AI-powered responses:

### Workflow Decision Tree

```
1. Check Knowledge Base for exact/close matches
   ↓ (if found with high confidence ≥0.9)
2. Return KB answer with source citation
   ↓ (if not found or low confidence)
3. Send to Gemini with document context
   ↓
4. Return Gemini response with document citations
```

### What's Included in Knowledge Base

- **Class Schedules**: Real-time class schedules from `classes_config.py`
  - Grade 1: 9:30 - 10:30 AM
  - Grade 4: 9:30 - 10:30 AM
- **Location Information**: Specific details about Vietnam Hearts locations
- **Volunteer Opportunities**: Comprehensive role descriptions and requirements
- **Organization Information**: Mission, focus, and program details

### How It Works

1. **High Confidence KB Queries** (≥0.9 confidence):
   - Location, hours, volunteer, and organization queries
   - Returns specific information with source citations
   - Example: "Where are you located?" → KB response with source

2. **AI with Context Queries** (<0.9 confidence):
   - General questions not in knowledge base
   - Sends question to Gemini with full KB context
   - Returns AI response with document citations
   - Example: "What should I bring to volunteer?" → AI response with KB context

### Source Citations

- **Knowledge Base Responses**: Include `*Source: Vietnam Hearts Knowledge Base - [Type]*`
- **AI Responses**: Include `Source: Vietnam Hearts Documentation` when using KB context

### Example Queries and Responses

**High Confidence KB:**
- "Where are you located?" → KB response with location source
- "What are your hours?" → KB response with schedule source
- "How can I volunteer?" → KB response with volunteer source

**AI with Context:**
- "What should I bring to volunteer?" → AI response using KB context
- "How do I get to your location?" → AI response using location context
- "Are there age restrictions?" → AI response using volunteer context

## Configuration

Configure keywords in `agent/config.py`:

```python
VOLUNTEER_KEYWORDS = [
    "volunteer", "volunteering", "help", "teach", "teaching", 
    "assist", "join", "sign up", "participate"
]

FAQ_KEYWORDS = [
    "location", "where", "when", "time", "schedule", 
    "hours", "address", "contact", "info"
]
```

### Response Templates

Customize responses in `agent/config.py`:

```python
RESPONSE_TEMPLATES = {
    "volunteer_interest": {
        "message": "Thank you for your interest in volunteering! 🙌\n\nYou can sign up here: {signup_link}",
        "quick_replies": [
            {"text": "Sign Up Now", "payload": "SIGNUP"},
            {"text": "Learn More", "payload": "LEARN_MORE"}
        ]
    }
}
```

## Usage Examples

### Basic Message Processing

```python
from agent.agent import VietnamHeartsAgent
from agent.models import MessageRequest

# Initialize agent
agent = VietnamHeartsAgent()

# Process a message
request = MessageRequest(
    user_id="12345",
    platform="messenger",
    message_text="I want to volunteer"
)

response = agent.process_message(request)
print(f"Intent: {response.intent}")
print(f"Response: {response.response_text}")
```

### Quick Reply Processing

```python
# Process a quick reply button click
response = agent.process_quick_reply("12345", "messenger", "SIGNUP")
print(f"Response: {response.response_text}")
```

## Integration with Facebook Messenger

### 1. Set Up Webhook

Configure your Facebook app webhook to point to:
```
https://your-domain.com/agent/webhook
```

### 2. Verify Webhook

Facebook will send a verification request. Handle it in your webhook endpoint.

### 3. Send Responses

The webhook handler processes incoming messages and generates responses. You'll need to implement the actual sending logic using the Facebook Messenger API.

## Integration with Instagram

Similar to Facebook Messenger, but configure the webhook for Instagram Business messaging.

## Development

### Running Tests

```bash
# Test with real Gemini API
GEMINI_API_KEY=your_key python agent/test_agent.py

# Test with keyword detection only
python agent/test_agent.py
```

### Adding New Intents

1. Add keywords to `VOLUNTEER_KEYWORDS` or `FAQ_KEYWORDS` in `config.py`
2. Update the intent detection logic in `intent_detector.py`
3. Add response templates in `config.py`
4. Update the response generator in `response_generator.py`

### Adding New Response Types

1. Add template to `RESPONSE_TEMPLATES` in `config.py`
2. Add generation method to `ResponseGenerator` class
3. Update the main `generate_response` method

## Monitoring and Analytics

The agent logs all interactions to the database (if configured):

- **Conversations**: Track user conversations across sessions
- **Messages**: Log all incoming and outgoing messages
- **Intents**: Record detected intents and confidence scores
- **Escalations**: Track when messages are escalated to humans

## Troubleshooting

### Common Issues

1. **Gemini API Key Missing**
   - Set `GEMINI_API_KEY` environment variable
   - Agent will fall back to keyword detection only

2. **Import Errors**
   - Ensure you're running from the project root
   - Check that all dependencies are installed

3. **Webhook Not Working**
   - Verify webhook URL is accessible
   - Check Facebook app configuration
   - Review webhook verification process

### Debug Mode

Enable debug logging:

```python
import logging
logging.getLogger('agent').setLevel(logging.DEBUG)
```

## Contributing

1. Follow the existing code structure
2. Add tests for new features
3. Update documentation
4. Test with both keyword and AI detection

## License

This agent is part of the Vietnam Hearts project and follows the same licensing terms. 