"""The escalation summary: one call, and a failure that costs nothing."""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

from unittest.mock import MagicMock

import pytest

from app.services.email_bot.summaries import (
    MAX_INPUT_CHARS,
    SUMMARY_PROMPT,
    summarise_for_escalation,
)
from tests.fixtures.logs import attached_caplog

BODY = "I am a parent and I have a concern about the class yesterday."


def client(text: str | None = "A parent raised a safety concern.") -> MagicMock:
    gemini = MagicMock()
    gemini.models.generate_content.return_value = MagicMock(text=text)
    return gemini


class TestSummarising:
    def test_a_summary_is_returned(self):
        assert summarise_for_escalation(BODY, "en", client()) == (
            "A parent raised a safety concern."
        )

    def test_whitespace_is_collapsed_to_one_line(self):
        # The summary goes into a Discord message and a three-line mail header,
        # so a multi-line answer would break both layouts.
        gemini = client("A parent\n  raised   a\nconcern.")
        assert summarise_for_escalation(BODY, "en", gemini) == (
            "A parent raised a concern."
        )

    def test_the_prompt_forbids_names_and_contact_details(self):
        # It is posted to Discord, which has a wider audience than a mailbox.
        assert "Do not include anyone's name" in SUMMARY_PROMPT
        assert "contact detail" in SUMMARY_PROMPT

    def test_the_prompt_asks_for_one_sentence(self):
        assert "one short sentence" in SUMMARY_PROMPT

    def test_a_long_body_is_truncated_before_the_call(self):
        gemini = client()
        summarise_for_escalation("x" * (MAX_INPUT_CHARS * 3), "en", gemini)
        sent = gemini.models.generate_content.call_args.kwargs["contents"]
        assert len(sent) < MAX_INPUT_CHARS * 2

    def test_a_vietnamese_body_is_accepted(self):
        gemini = client("A parent raised a safety concern.")
        assert summarise_for_escalation("Mình có một lo ngại.", "vi", gemini)


class TestFailureCostsNothing:
    def test_no_client_returns_none(self):
        assert summarise_for_escalation(BODY, "en", None) is None

    def test_a_generation_failure_returns_none(self):
        # The design says the forward and the Discord post go out without the
        # summary. An escalation lost to an over-quota summariser would be the
        # worst possible trade.
        gemini = MagicMock()
        gemini.models.generate_content.side_effect = RuntimeError("429 quota")
        assert summarise_for_escalation(BODY, "en", gemini) is None

    @pytest.mark.parametrize("returned", [None, "", "   \n  "])
    def test_an_empty_answer_returns_none(self, returned):
        assert summarise_for_escalation(BODY, "en", client(returned)) is None

    def test_an_empty_body_is_not_sent_to_the_model(self):
        gemini = client()
        assert summarise_for_escalation("", "en", gemini) is None
        gemini.models.generate_content.assert_not_called()

    def test_the_failure_log_carries_no_mail_content(self, caplog):
        gemini = MagicMock()
        gemini.models.generate_content.side_effect = RuntimeError(
            f"provider echoed the prompt back: {BODY}"
        )
        with attached_caplog(caplog, "email_bot_summaries", level="WARNING"):
            summarise_for_escalation(BODY, "en", gemini)

        combined = " ".join(record.getMessage() for record in caplog.records)
        assert BODY not in combined
        assert "RuntimeError" in combined
