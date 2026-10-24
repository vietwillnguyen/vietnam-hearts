"""
Personal data must not reach any log sink.

Every app logger fans out to stdout (Cloud Logging), a rotating file and the
system_logs table. These tests log through a real app logger and then read
back what each of those sinks actually holds, so a sink that formats the
record on its own (a JSON formatter, a traceback, the DB handler's message)
cannot slip past the redaction.
"""

import json
import logging
import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import SystemLog
from app.utils import logging_config
from app.utils.db_log_handler import DatabaseLogHandler
from app.utils.logging_config import redact_credentials, setup_logger

EMAIL = "nguyen.van.an@example.com"
MASKED_EMAIL = "n***@example.com"

# Every value below is personal data or a credential that must be masked.
SENSITIVE = {
    "email": EMAIL,
    "vn_mobile": "0912 345 678",
    "vn_mobile_compact": "0912345678",
    "international": "+84 912 345 678",
    "nanp": "(555) 123-4567",
    # Credential-shaped values are assembled at runtime from obviously fake
    # parts so secret scanners do not flag the test source itself.
    "jwt": ".".join(["eyJ" + "fakeheader", "fakepayload0", "fakesignature0"]),
    "google_api_key": "AI" + "za" + "-fake-placeholder-not-a-real-key-0",
    "supabase_key": "sb_" + "secret_" + "fakePlaceholderValue0",
    "api_key_pair": "api_key=placeholder-not-a-real-key",
}

# Look-alikes that carry no personal data and must survive untouched.
HARMLESS = [
    "sheet 1775524467",
    "spreadsheet 1woqe6FLYrW08YoeTx3V0xZcSMrKxgMV391ZrOlzTsPo",
    "at 2026-10-23T10:00:06.411945Z",
    "on 2026-10-23 10:00:06",
    "Schedule 26/10/2026",
    "from 34.116.43.105",
    "1188 total rows, 191 valid",
    "took 32.554756651s",
    "request ID: 85ff154c",
]


@pytest.fixture
def sinks(test_engine, test_db, tmp_path, monkeypatch, capsys):
    """A fresh app logger wired to all three production sinks."""
    monkeypatch.setenv("PERSIST_LOGS_TO_DB", "true")
    monkeypatch.setattr(logging_config, "LOGS_DIR", tmp_path)
    db_handler = DatabaseLogHandler(
        session_factory=sessionmaker(bind=test_engine), buffer_size=10_000
    )
    monkeypatch.setattr(logging_config, "_db_log_handler", db_handler)

    def make(json_console: bool):
        monkeypatch.setattr(logging_config, "IS_CLOUD_RUN", json_console)
        name = f"pii-probe-{uuid.uuid4().hex[:8]}"
        logger = setup_logger(name, "probe.log")

        def read():
            for handler in logger.handlers:
                handler.flush()
            stdout = capsys.readouterr().err
            file_text = (tmp_path / "probe.log").read_text()
            rows = [
                r.message
                for r in test_db.query(SystemLog)
                .filter(SystemLog.logger_name == name)
                .all()
            ]
            return {"stdout": stdout, "file": file_text, "system_logs": "\n".join(rows)}

        return logger, read

    yield make
    db_handler.close()


@pytest.mark.parametrize("json_console", [False, True], ids=["text", "cloud-run-json"])
@pytest.mark.parametrize("kind", sorted(SENSITIVE))
def test_sensitive_values_are_masked_in_every_sink(sinks, json_console, kind):
    logger, read = sinks(json_console)
    value = SENSITIVE[kind]

    logger.info(f"inline {value} end")
    logger.info("lazy %s end", value)
    logger.info("dict %s end", {"contact": value})

    for sink, text in read().items():
        assert text.count(" end") >= 3, f"{sink} did not receive the records"
        assert value not in text, f"{kind} leaked into {sink}"


@pytest.mark.parametrize("json_console", [False, True], ids=["text", "cloud-run-json"])
def test_emails_keep_their_first_letter_and_domain(sinks, json_console):
    logger, read = sinks(json_console)

    logger.info("Confirmation email sent to %s", EMAIL)

    for sink, text in read().items():
        assert MASKED_EMAIL in text, sink


@pytest.mark.parametrize("json_console", [False, True], ids=["text", "cloud-run-json"])
def test_tracebacks_are_masked_in_every_sink(sinks, json_console):
    logger, read = sinks(json_console)

    try:
        raise ValueError(f"SMTP rejected {EMAIL} / 0912 345 678")
    except ValueError:
        logger.error("send failed", exc_info=True)

    out = read()
    for sink in ("stdout", "file"):
        assert "ValueError: SMTP rejected" in out[sink], sink
        assert EMAIL not in out[sink], sink
        assert "0912 345 678" not in out[sink], sink
    assert EMAIL not in out["system_logs"]


@pytest.mark.parametrize("text", HARMLESS)
def test_look_alikes_are_left_alone(text):
    assert redact_credentials(text) == text


def test_masking_is_idempotent():
    once = redact_credentials(" ".join(SENSITIVE.values()))
    assert redact_credentials(once) == once


def test_the_filter_is_on_the_root_logger():
    assert logging_config._credential_redacting_filter in logging.getLogger().filters


def test_json_payload_stays_parseable_after_masking(sinks):
    logger, read = sinks(True)
    logger.info("to %s", EMAIL)
    line = [ln for ln in read()["stdout"].splitlines() if ln.startswith("{")][-1]
    assert json.loads(line)["message"] == f"to {MASKED_EMAIL}"


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def test_sheet_reads_log_counts_not_cell_values():
    from unittest.mock import MagicMock, patch

    from app.services.google_sheets import GoogleSheetsService

    svc = GoogleSheetsService.__new__(GoogleSheetsService)
    svc._sheet = MagicMock()
    svc._initialized = True
    svc._sheet.values().get().execute.return_value = {
        "values": [["Teacher", "Trần Thị Mỹ Hạnh", "Annie (Thu Hằng)"], ["TA"]]
    }
    collect = _Collect()
    api_logger = logging.getLogger("api")
    api_logger.addHandler(collect)
    try:
        with patch(
            "app.services.google_sheets.ConfigHelper.get_google_sheets_max_retries",
            return_value=1,
        ):
            svc.get_range_from_sheet(
                MagicMock(), "sheet-id", "'Schedule 26/10/2026'!B1:G100"
            )
    finally:
        api_logger.removeHandler(collect)

    text = "\n".join(collect.lines)
    assert "rows=2 cols=3" in text
    assert "Mỹ Hạnh" not in text and "Thu Hằng" not in text
