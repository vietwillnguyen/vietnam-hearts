"""
Database connection and session management

This file handles:
1. Creating the database connection
2. Managing database sessions (transactions)
3. Creating tables when the app starts
4. Initializing default settings
"""

from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from .models import Base
from .utils.logging_config import get_database_logger
from .services.settings_service import initialize_default_settings
from app.config import DATABASE_URL
# Initialize logger
logger = get_database_logger()

# Create database engine
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

"""
Core Concept: Database Sessions
- Session = a conversation with the database
- autocommit=False = we control when changes are saved (transactions)
- autoflush=False = we control when changes are sent to database
- bind=engine = which database to use
"""


def create_tables():
    """
    Create all tables defined in models.py
    This runs when the application starts
    """
    try:
        db = SessionLocal()
        initialize_default_settings(db)
        db.close()
        logger.info("Default settings initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize default settings: {str(e)}", exc_info=True)
        raise


def initialize_settings():
    """
    Initialize default settings in the database
    This runs after tables are created
    """
    try:
        db = SessionLocal()
        initialize_default_settings(db)
        db.close()
        logger.info("Default settings initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize default settings: {str(e)}", exc_info=True)
        raise


def get_db():
    """
    Dependency function for FastAPI endpoints.
    Provides a database session to each API request.
    Automatically closes the session when done.
    """
    db = SessionLocal()
    try:
        logger.debug("Database session created (FastAPI)")
        yield db
    except Exception as e:
        # If you have a _handle_session_error, call it here, otherwise just log and raise
        logger.error(f"Database session error (FastAPI): {str(e)}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()
        logger.debug("Database session closed (FastAPI)")

from contextlib import contextmanager

@contextmanager
def get_db_session():
    """
    Context manager for manual database session management.
    Use this when you need manual control over database sessions (e.g., batch operations).
    """
    db = SessionLocal()
    try:
        logger.debug("Database session created (manual)")
        yield db
    except Exception as e:
        logger.error(f"Database session error (manual): {str(e)}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()
        logger.debug("Database session closed (manual)")

def test_connection():
    """Test database connectivity"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database connection test successful")
        return True
    except Exception as e:
        logger.error(f"Failed to create database tables: {str(e)}", exc_info=True)
        raise


"""
Core Concept: HTTP Status Codes
- 200: Success
- 404: Not Found (HTTPException with 404)
- 422: Validation Error (FastAPI handles this automatically)
- 500: Server Error (unhandled exceptions)

FastAPI automatically returns appropriate status codes
"""

