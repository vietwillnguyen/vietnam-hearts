#!/usr/bin/env python3
"""
Test script to verify logging configuration works for both console and file output.
"""

from app.utils.logging_config import get_logger, print_log_paths

def test_logs_appear_console_and_file():
    """Test that logs appear in both console and file."""
    print("=== Testing Logging Configuration ===")
    
    # Print log paths
    print_log_paths()
    
    # Get logger
    logger = get_logger("test")
    
    # Test different log levels
    print("\n=== Testing Log Messages ===")
    logger.debug("This is a DEBUG message")
    logger.info("This is an INFO message")
    logger.warning("This is a WARNING message")
    logger.error("This is an ERROR message")
    
    # Test with context
    logger.info("Testing application startup...")
    logger.info("Database connection established")
    logger.info("API server started successfully")
    
    print("\n=== Test Complete ===")
    print("Check both console output above and the log file at ./app/logs/app.log")

if __name__ == "__main__":
    test_logs_appear_console_and_file() 