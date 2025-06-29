"""
Database connection and session management

This file handles:
1. Creating the database connection
2. Managing database sessions (transactions)
3. Creating tables when the app starts
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .models import Base
from .utils.logging_config import get_database_logger

# Initialize logger
logger = get_database_logger()

# Get database URL from environment variables
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./scheduler.db")
logger.info(f"Using database URL: {DATABASE_URL}")

"""
Core Concept: Database URLs
- sqlite:///./scheduler.db = SQLite file in current directory
- postgresql://user:pass@localhost/db = PostgreSQL connection
- The /// in SQLite means it's a relative path
"""

# Create database engine with appropriate settings
if DATABASE_URL.startswith("sqlite"):
    # SQLite-specific settings
    engine = create_engine(
        DATABASE_URL,
        connect_args={
            "check_same_thread": False
        },  # Allow multiple threads (needed for FastAPI)
    )
    logger.info("Using SQLite database")
else:
    # PostgreSQL settings (for both Cloud SQL and Supabase)
    engine = create_engine(
        DATABASE_URL,
        pool_size=5,  # Number of connections to keep open
        max_overflow=10,  # Maximum number of connections that can be created beyond pool_size
        pool_timeout=30,  # Seconds to wait before giving up on getting a connection from the pool
        pool_recycle=1800,  # Recycle connections after 30 minutes
        # Supabase-specific settings
        connect_args={"sslmode": "require"},  # Required for Supabase
    )
    logger.info("Using PostgreSQL database")

"""
Core Concept: Database Engine
- Engine = the interface to your database
- Handles connection pooling (reusing database connections)
- Translates SQLAlchemy operations to actual SQL
"""

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
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.error(f"Failed to create database tables: {str(e)}", exc_info=True)
        raise


def get_db():
    """
    Dependency function for FastAPI
    Provides a database session to each API request
    Automatically closes the session when done

    This is a Python generator - yields a session, then cleans up
    """
    db = SessionLocal()
    try:
        logger.debug("Database session created")
        yield db  # This is where the API endpoint uses the database
    except Exception as e:
        logger.error(f"Database session error: {str(e)}", exc_info=True)
        raise
    finally:
        db.close()  # Always close the session, even if an error occurs
        logger.debug("Database session closed")


"""
Core Concept: Dependency Injection
FastAPI will automatically call get_db() for any endpoint that needs it:

@app.get("/classes")
def get_classes(db: Session = Depends(get_db)):
    # db is automatically provided by FastAPI
    return db.query(ScheduledClass).all()
"""


# Database utility functions
def init_database():
    """Initialize database schema and tables"""
    try:
        create_tables()
        logger.info("Database URL: %s", DATABASE_URL)
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
