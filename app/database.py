"""
Database connection and session management

This file handles:
1. Creating the database connection
2. Managing database sessions (transactions)
3. Creating tables when the app starts
4. Initializing default settings
"""

from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from app.config import DATABASE_URL, PROJECT_ROOT

from .services.settings_service import initialize_default_settings
from .utils.logging_config import get_database_logger

# Initialize logger
logger = get_database_logger()

# Create database engine
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
    if DATABASE_URL.startswith("sqlite")
    else {},
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


def _stamp_baseline_if_pre_existing_schema(alembic_cfg: Config) -> None:
    """
    Reconcile a database that was created by the old create_all() path
    (tables exist already) but has never been touched by Alembic (no
    alembic_version table/row), so that `alembic upgrade head` doesn't try
    to re-create tables that are already there.

    Only stamps when the un-migrated database already has our tables; a
    genuinely empty database is left alone so `upgrade head` builds it from
    the baseline migration as usual.
    """
    with engine.connect() as connection:
        current_rev = MigrationContext.configure(connection).get_current_revision()
        if current_rev is not None:
            return

        existing_tables = set(inspect(engine).get_table_names())

    from app.models import Base

    app_tables = set(Base.metadata.tables.keys())
    if existing_tables & app_tables:
        script = ScriptDirectory.from_config(alembic_cfg)
        command.stamp(alembic_cfg, script.get_current_head())


def init_db():
    """
    Initialize the database on application startup.
    Ensures default settings are present in the database.
    """
    try:
        if DATABASE_URL.startswith("sqlite"):
            # sqlite is only used for local dev/tests, which start from an
            # empty file/in-memory db each time, so a straight create_all
            # (no migration history) is sufficient and keeps that path fast.
            from app.models import Base

            Base.metadata.create_all(bind=engine)
        else:
            # Postgres/Supabase is schema-managed by Alembic so that column
            # changes on existing tables (which create_all() can't express)
            # ship as reviewable, versioned migrations. See issue #9.
            alembic_cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
            _stamp_baseline_if_pre_existing_schema(alembic_cfg)
            command.upgrade(alembic_cfg, "head")

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
