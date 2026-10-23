"""
Running migrations at startup must not silence the app's loggers.

init_db() runs `alembic upgrade head` on Postgres, and alembic/env.py hands
alembic.ini to logging.config.fileConfig. fileConfig disables every logger
that already exists unless told otherwise, and by then every module-level app
logger has been created - so production logged its startup banner and then
nothing at all, including the traceback of a failed schedule rotation.
"""

import logging

import pytest

from alembic import command
from alembic.config import Config
from app.config import PROJECT_ROOT
from app.utils.logging_config import get_logger


@pytest.fixture
def restore_logging():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    loggers = {
        name: (lg.disabled, lg.level)
        for name, lg in logging.Logger.manager.loggerDict.items()
        if isinstance(lg, logging.Logger)
    }
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    for name, (disabled, lvl) in loggers.items():
        lg = logging.getLogger(name)
        lg.disabled = disabled
        lg.setLevel(lvl)


def test_app_loggers_survive_alembic_upgrade(restore_logging, capsys):
    app_logger = get_logger("startup_logging_probe")
    assert not app_logger.disabled

    # Offline (sql=True) still runs env.py, and so fileConfig, without
    # needing a database.
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head", sql=True)
    capsys.readouterr()

    assert not app_logger.disabled
    assert not get_logger("api").disabled
    assert not get_logger("database").disabled
