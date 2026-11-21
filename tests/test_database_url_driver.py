import pytest
from sqlalchemy.engine import make_url

from app.config import with_explicit_postgres_driver


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@db.example.com:5432/vh",
        "postgres://u:p@db.example.com:5432/vh",
    ],
)
def test_a_driverless_postgres_url_is_pinned_to_the_installed_psycopg2(url):
    pinned = with_explicit_postgres_driver(url)

    assert make_url(pinned).drivername == "postgresql+psycopg2"
    assert make_url(pinned).host == "db.example.com"
    assert make_url(pinned).password == "p"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg2://u:p@db.example.com/vh",
        "postgresql+psycopg://u:p@db.example.com/vh",
        "sqlite:///./dev.db",
    ],
)
def test_a_url_that_names_its_driver_or_is_not_postgres_is_left_alone(url):
    assert with_explicit_postgres_driver(url) == url


def test_the_pinned_driver_is_importable():
    """The app must import the driver the URL names, or startup dies at create_engine."""
    dialect = make_url(
        with_explicit_postgres_driver("postgresql://u:p@h/vh")
    ).get_dialect()

    dialect.import_dbapi()
