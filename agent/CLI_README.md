# Vietnam Hearts Agent CLI

A command-line interface for testing and interacting with the Vietnam Hearts chatbot.

## Features

- **Interactive Mode**: Chat with the bot in real-time
- **Single Message Testing**: Send individual messages for testing
- **Quick Reply Testing**: Test quick reply button functionality
- **Verbose Logging**: Debug mode with detailed logs
- **Intent Detection**: See how the bot interprets messages
- **Response Analysis**: View confidence scores and escalation flags

## Installation

Make sure you have the required environment variables set in your `.env` file:

```bash
GEMINI_API_KEY=your_gemini_api_key
NEW_USER_SIGNUP_LINK=your_signup_link
```

## Usage

### Interactive Mode (Default)

Start an interactive chat session:

```bash
poetry run python agent/cli.py
```

### Single Message Testing

Send a single message and exit:

```bash
poetry run python agent/cli.py -m "I want to volunteer"
poetry run python agent/cli.py -m "Where are you located?"
poetry run python agent/cli.py -m "What's the weather like?"
```

### Quick Reply Testing

Test quick reply button functionality:

```bash
poetry run python agent/cli.py -q SIGNUP
poetry run python agent/cli.py -q "LEARN MORE"
poetry run python agent/cli.py -q "CONTACT US"
poetry run python agent/cli.py -q LOCATION
poetry run python agent/cli.py -q SCHEDULE
```

### Verbose Logging

Enable detailed logging for debugging:

```bash
poetry run python agent/cli.py -v
poetry run python agent/cli.py -m "I want to volunteer" -v
```

### Custom User ID

Set a custom user ID for the session:

```bash
poetry run python agent/cli.py --user-id test_user_123
```

## Interactive Mode Commands

When in interactive mode, you can use these commands:

- `help` - Show available commands
- `message <text>` - Send a message with explicit command
- `quick <payload>` - Test a quick reply (e.g., `quick SIGNUP`)
- `quit` or `exit` - Exit the CLI

## Available Quick Reply Payloads

- `SIGNUP` - Volunteer signup
- `LEARN MORE` - General information about Vietnam Hearts
- `CONTACT US` - Contact the team
- `LOCATION` - Location information
- `SCHEDULE` - Class schedule information

## Example Session

```
============================================================
🤖 Vietnam Hearts Assistant v1.0.0
Command Line Interface
============================================================

🚀 Initializing agent...
✅ Agent initialized successfully!

💬 Interactive mode started. Type 'help' for commands, 'quit' to exit.

> I want to volunteer
📤 You: I want to volunteer
----------------------------------------
🤖 Bot: Thank you for your interest in volunteering with Vietnam Hearts! We are always looking for volunteer teachers and assistants 🙌

You can sign up here: https://forms.gle/yBFA8GqP1TbwUJt17

We'd love to have you join our community of volunteers making a difference in Vietnam!
📊 Intent: volunteer (confidence: 1.00)
🚨 Escalate: False
🔘 Quick Replies:
  1. Sign Up Now (SIGNUP)
  2. Learn More (LEARN MORE)
  3. Contact Us (CONTACT US)
----------------------------------------

> quick LEARN MORE
🔘 Quick Reply: LEARN MORE
----------------------------------------
🤖 Bot: 💙 **About Vietnam Hearts:**

**Mission**: Help underprivileged children in Vietnam through education and support
...
📊 Intent: faq (confidence: 0.90)
🚨 Escalate: False
----------------------------------------

> quit
👋 Goodbye!
```

## Troubleshooting

### Common Issues

1. **Missing Environment Variables**: Make sure your `.env` file is properly configured
2. **API Key Issues**: Verify your Gemini API key is valid and has sufficient quota
3. **Import Errors**: Ensure you're running from the project root directory

### Debug Mode

Use the `-v` flag to see detailed logs:

```bash
poetry run python agent/cli.py -v -m "test message"
```

This will show:
- Intent detection process
- AI reasoning
- Confidence scores
- Error details

## Development

The CLI is designed for testing and development. It provides:

- **Real-time feedback** on bot responses
- **Intent detection analysis** 
- **Quick reply testing** without UI
- **Error handling** with detailed logging

Use this tool to:
- Test new features
- Debug intent detection issues
- Verify response quality
- Test quick reply flows
- Validate configuration changes 