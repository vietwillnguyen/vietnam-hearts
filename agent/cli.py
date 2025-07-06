#!/usr/bin/env python3
"""
Command Line Interface for the Vietnam Hearts Agent.
Allows interactive testing of the chatbot functionality.
"""

import sys
import logging
import argparse
from pathlib import Path
from typing import Optional

# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agent.agent import VietnamHeartsAgent
from agent.models import MessageRequest
from agent.config import AGENT_NAME, AGENT_VERSION


def setup_logging(verbose: bool = False) -> None:
    """Set up logging for the CLI"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )


def print_banner() -> None:
    """Print the CLI banner"""
    print("=" * 60)
    print(f"🤖 {AGENT_NAME} v{AGENT_VERSION}")
    print("Command Line Interface")
    print("=" * 60)
    print()


def print_help() -> None:
    """Print help information"""
    print("Available commands:")
    print("  message <text>     - Send a message to the chatbot")
    print("  quick <payload>    - Test a quick reply (e.g., 'quick SIGNUP')")
    print("  help               - Show this help message")
    print("  quit/exit          - Exit the CLI")
    print()
    print("Quick reply payloads:")
    print("  - SIGNUP")
    print("  - LEARN MORE") 
    print("  - CONTACT US")
    print("  - LOCATION")
    print("  - SCHEDULE")
    print()


def process_message(agent: VietnamHeartsAgent, message: str, user_id: str = "cli_user") -> None:
    """Process a message through the agent"""
    print(f"📤 You: {message}")
    print("-" * 40)
    
    try:
        request = MessageRequest(
            user_id=user_id,
            platform="cli",
            message_text=message
        )
        
        response = agent.process_message(request)
        
        print(f"🤖 Bot: {response.response_text}")
        print(f"📊 Intent: {response.intent} (confidence: {response.confidence:.2f})")
        print(f"🚨 Escalate: {response.should_escalate}")
        
        if response.quick_replies:
            print("🔘 Quick Replies:")
            for i, qr in enumerate(response.quick_replies, 1):
                print(f"  {i}. {qr['text']} ({qr['payload']})")
        
        print("-" * 40)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        print("-" * 40)


def process_quick_reply(agent: VietnamHeartsAgent, payload: str, user_id: str = "cli_user") -> None:
    """Process a quick reply through the agent"""
    print(f"🔘 Quick Reply: {payload}")
    print("-" * 40)
    
    try:
        response = agent.process_quick_reply(user_id, "cli", payload)
        
        print(f"🤖 Bot: {response.response_text}")
        print(f"📊 Intent: {response.intent} (confidence: {response.confidence:.2f})")
        print(f"🚨 Escalate: {response.should_escalate}")
        
        if response.quick_replies:
            print("🔘 Quick Replies:")
            for i, qr in enumerate(response.quick_replies, 1):
                print(f"  {i}. {qr['text']} ({qr['payload']})")
        
        print("-" * 40)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        print("-" * 40)


def interactive_mode(agent: VietnamHeartsAgent) -> None:
    """Run the CLI in interactive mode"""
    print("💬 Interactive mode started. Type 'help' for commands, 'quit' to exit.")
    print()
    
    while True:
        try:
            user_input = input("> ").strip()
            
            if not user_input:
                continue
                
            if user_input.lower() in ['quit', 'exit', 'q']:
                print("👋 Goodbye!")
                break
                
            elif user_input.lower() == 'help':
                print_help()
                
            elif user_input.lower().startswith('quick '):
                payload = user_input[6:].strip().upper()
                process_quick_reply(agent, payload)
                
            elif user_input.lower().startswith('message '):
                message = user_input[8:].strip()
                process_message(agent, message)
                
            else:
                # Treat as a regular message
                process_message(agent, user_input)
                
        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            break
        except EOFError:
            print("\n👋 Goodbye!")
            break


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description=f"{AGENT_NAME} CLI - Interactive chatbot testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                    # Start interactive mode
  %(prog)s -m "I want to volunteer"           # Send a single message
  %(prog)s -q SIGNUP                          # Test quick reply
  %(prog)s -v                                 # Verbose logging
        """
    )
    
    parser.add_argument(
        '-m', '--message',
        help='Send a single message and exit'
    )
    
    parser.add_argument(
        '-q', '--quick-reply',
        help='Test a quick reply payload and exit'
    )
    
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    
    parser.add_argument(
        '--user-id',
        default='cli_user',
        help='User ID for the session (default: cli_user)'
    )
    
    args = parser.parse_args()
    
    # Set up logging
    setup_logging(args.verbose)
    
    # Print banner
    print_banner()
    
    try:
        # Initialize agent
        print("🚀 Initializing agent...")
        agent = VietnamHeartsAgent()
        print("✅ Agent initialized successfully!")
        print()
        
        # Handle different modes
        if args.message:
            process_message(agent, args.message, args.user_id)
        elif args.quick_reply:
            process_quick_reply(agent, args.quick_reply.upper(), args.user_id)
        else:
            # Interactive mode
            interactive_mode(agent)
            
    except Exception as e:
        print(f"❌ Failed to initialize agent: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main() 