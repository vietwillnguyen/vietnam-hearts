import os
os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared"
import secrets
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from unittest.mock import patch

from app.models import Base, Volunteer as VolunteerModel

import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@pytest.fixture(scope="session", autouse=True)
def set_test_env():
    os.environ["TESTING"] = "true"
    yield
    os.environ.pop("TESTING", None)


@pytest.fixture
def test_engine():
    engine = create_engine(
        "sqlite:///file::memory:?cache=shared",
        connect_args={"check_same_thread": False, "uri": True}
    )
    return engine


@pytest.fixture
def test_db(test_engine):
    # Create tables before test
    Base.metadata.create_all(bind=test_engine)
    Session = sessionmaker(bind=test_engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def client(test_engine, test_db):
    import app.database as app_db
    app_db.engine = test_engine
    app_db.SessionLocal = sessionmaker(bind=test_engine)

    def override_get_db():
        yield test_db

    with patch("app.config.validate_config"):
        from app.main import app
        app.dependency_overrides[app_db.get_db] = override_get_db

        # ✅ Use TestClient as a context manager to trigger `lifespan`
        with TestClient(app) as test_client:
            yield test_client

        app.dependency_overrides.clear()

@pytest.fixture(autouse=True, scope="session")
def enable_logging_for_tests():
    logging.basicConfig(
        level=logging.DEBUG,  # or INFO
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Make sure custom loggers are included
    for logger_name in ["app", "api", "database", "scheduler"]:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)
        logger.propagate = True

@pytest.fixture
def mock_volunteer(test_db):
    volunteer = VolunteerModel(
        name="Test Volunteer",
        email="test@example.com",
        positions=["Teacher"],
        teaching_experience="Some experience",
        email_unsubscribe_token=secrets.token_urlsafe(32),
        weekly_reminders_subscribed=True,
        all_emails_subscribed=True,
        is_active=True,
    )
    test_db.add(volunteer)
    test_db.commit()
    test_db.refresh(volunteer)
    return volunteer

@pytest.fixture
def mock_inactive_volunteer(test_db):
    volunteer = VolunteerModel(
        name="Test Inactive",
        email="inactive@example.com",
        email_unsubscribe_token="resub-token-123",
        all_emails_subscribed=False,
        weekly_reminders_subscribed=False,
        is_active=False,
    )
    test_db.add(volunteer)
    test_db.commit()
    test_db.refresh(volunteer)
    return volunteer