"""
Database connection and session management

This file handles:
1. Creating the database connection
2. Managing database sessions (transactions)
3. Creating tables when the app starts
"""

import os
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from .models import Base
from .utils.logging_config import get_database_logger
from .config import DATABASE_URL

# Initialize logger
logger = get_database_logger()

# Create database engine
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def _handle_session_error(db, error, context=""):
    """Common error handling for database sessions"""
    logger.error(f"Database session error{' ' + context if context else ''}: {str(error)}", exc_info=True)
    db.rollback()
    raise

def init_database():
    """Initialize database"""
    Base.metadata.create_all(bind=engine)

@contextmanager
def get_db_session():
    """
    Context manager for manual database session management.
    Use this when you need manual control over database sessions.
    """
    db = SessionLocal()
    try:
        logger.debug("Database session created (manual)")
        yield db
    except Exception as e:
        _handle_session_error(db, e, "(manual)")
    finally:
        db.close()
        logger.debug("Database session closed (manual)")

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
        _handle_session_error(db, e, "(FastAPI)")
    finally:
        db.close()
        logger.debug("Database session closed (FastAPI)")

def test_connection():
    """Test database connectivity"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database connection test successful")
        return True
    except Exception as e:
        logger.error(f"Database connection test failed: {str(e)}")
        return False