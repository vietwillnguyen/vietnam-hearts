"""End-to-end conversation scenarios on the email channel.

Rewritten and un-skipped. The previous contents drove the Messenger webhook
through a bot that answered from keyword-matched canned strings, and asserted
that a reply about volunteering contained the word "volunteer" - so it was
skipped wholesale when that canned-answer path was removed, and would have
passed against a knowledge base that was completely unreachable.

What replaces it asks the question the old file was reaching for - "does the
right thing happen to a real mail?" - of the path that now exists, with fixture
mails through fakes and the outcome asserted as an outcome: what was drafted,
what was escalated, what was skipped, in both languages.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import anyio
import pytest

from app.models import Message
from app.services.channels.gmail import GmailAdapter
from app.services.channels.gmail_transport import (
    LABEL_DRAFTED,
    LABEL_ESCALATED,
    LABEL_SEEN,
    LABEL_SKIPPED,
)
from app.services.email_bot.pipeline import EmailBotPipeline
from app.services.email_bot.settings import EmailBotSettings
from tests.fixtures.email_bot import (
    FakeBotService,
    FakeClassifier,
    FakeTransport,
    RecordingNotifier,
    load_gmail,
    signals,
)

OWNER = "owner@example.com"
FORM = "https://docs.google.com/forms/d/e/1FAIpQLSexample/viewform"

GROUNDED_ANSWER = {
    "response": "Volunteers do not need a teaching certificate.",
    "context_used": 2,
    "confidence": 0.81,
    "sources": ["kb-doc"],
}


def settings(**overrides) -> EmailBotSettings:
    values = {
        "mode": "draft",
        "escalation_owner_email": OWNER,
        "signup_form_link": FORM,
        "per_run_cap": 20,
    }
    values.update(overrides)
    return EmailBotSettings(**values)


class Scenario:
    """One poll over one mail, with everything the assertions need to hand."""

    def __init__(self, db, fixture: str, triage, answer=None):
        self.resource = load_gmail(fixture)
        self.transport = FakeTransport(mails=[self.resource])
        self.notifier = RecordingNotifier()
        self.bot = FakeBotService(answer or GROUNDED_ANSWER)
        adapter = GmailAdapter(self.transport)
        self.pipeline = EmailBotPipeline(
            db=db,
            adapter=adapter,
            classifier=FakeClassifier(default=triage),
            notifier=self.notifier,
            settings=settings(),
            bot_service=self.bot,
        )
        self.db = db

        async def drive():
            return await anyio.to_thread.run_sync(self.pipeline.run)

        self.summary = anyio.run(drive)

    @property
    def labels(self) -> list[str]:
        return self.transport.labels_for(self.resource["id"])

    @property
    def drafted_text(self) -> str:
        rows = (
            self.db.query(Message)
            .filter(Message.direction == "outbound", Message.action == "drafted")
            .all()
        )
        assert len(rows) == 1, f"expected one draft, found {len(rows)}"
        return rows[0].text


class TestSignupIsAnsweredFromTheTemplate:
    @pytest.mark.parametrize(
        "fixture,language,phrase",
        [
            ("signup_en.json", "en", "interest in volunteering with Vietnam Hearts"),
            ("signup_vi.json", "vi", "quan tâm đến việc làm tình nguyện viên"),
        ],
    )
    def test_a_signup_mail_gets_the_template_in_its_own_language(
        self, test_db, fixture, language, phrase
    ):
        scenario = Scenario(
            test_db,
            fixture,
            signals(category="signup", language=language, confidence=0.9),
        )

        assert scenario.summary.drafted == 1
        assert scenario.summary.forwarded == 0
        assert phrase in scenario.drafted_text
        assert FORM in scenario.drafted_text

    def test_the_template_answer_never_touches_the_generator(self, test_db):
        scenario = Scenario(
            test_db, "signup_en.json", signals(category="signup", confidence=0.9)
        )
        assert scenario.bot.calls == []

    def test_the_outcome_labels_say_drafted(self, test_db):
        scenario = Scenario(
            test_db, "signup_en.json", signals(category="signup", confidence=0.9)
        )
        assert LABEL_DRAFTED in scenario.labels
        assert "VH-Bot/signup" in scenario.labels
        assert scenario.labels[-1] == LABEL_SEEN


class TestFaqIsAnsweredFromTheKnowledgeBase:
    def test_a_grounded_answer_is_drafted(self, test_db):
        scenario = Scenario(
            test_db, "faq_en.json", signals(category="faq", confidence=0.86)
        )

        assert scenario.summary.drafted == 1
        assert "do not need a teaching certificate" in scenario.drafted_text

    def test_the_email_prompt_and_language_reach_the_generator(self, test_db):
        scenario = Scenario(
            test_db, "faq_en.json", signals(category="faq", confidence=0.86)
        )
        assert scenario.bot.calls[0]["channel"] == "email"
        assert scenario.bot.calls[0]["language"] == "en"

    def test_a_vietnamese_faq_asks_for_a_vietnamese_answer(self, test_db):
        scenario = Scenario(
            test_db,
            "signup_vi.json",
            signals(category="faq", language="vi", confidence=0.86),
        )
        assert scenario.bot.calls[0]["language"] == "vi"

    def test_an_ungrounded_answer_is_never_offered(self, test_db):
        # The historical failure this whole path exists to prevent: an
        # unreachable knowledge base still producing a confident claim.
        scenario = Scenario(
            test_db,
            "faq_en.json",
            signals(category="faq", confidence=0.86),
            answer={"response": "You definitely do not need one.", "confidence": 0.12},
        )

        assert scenario.summary.forwarded == 1
        assert "definitely" not in scenario.drafted_text
        assert "better answered by a person" in scenario.drafted_text


class TestExecutiveMailIsEscalated:
    @pytest.mark.parametrize(
        "fixture,category,language",
        [
            ("sponsorship_en.json", "sponsorship", "en"),
            ("safeguarding_vi.json", "safeguarding_legal", "vi"),
            ("multi_topic_en.json", "donation", "en"),
        ],
    )
    def test_it_is_forwarded_and_the_sender_gets_the_holding_message(
        self, test_db, fixture, category, language
    ):
        scenario = Scenario(
            test_db,
            fixture,
            signals(category=category, language=language, confidence=0.85, money=True),
        )

        assert scenario.summary.forwarded == 1
        assert scenario.notifier.categories == [category]
        assert scenario.notifier.events[0].decision.tier == "needs_executive"
        assert LABEL_ESCALATED in scenario.labels
        assert f"VH-Bot/{category}" in scenario.labels

    def test_the_holding_message_is_in_the_senders_language(self, test_db):
        scenario = Scenario(
            test_db,
            "safeguarding_vi.json",
            signals(category="safeguarding_legal", language="vi", confidence=0.91),
        )
        assert "trợ lý tự động" in scenario.drafted_text

    def test_a_multi_topic_mail_escalates_on_its_most_serious_part(self, test_db):
        # Class times *and* a donation: answered as neither, escalated as the
        # donation, which is the parent spec's rule applied end to end.
        scenario = Scenario(
            test_db,
            "multi_topic_en.json",
            signals(category="donation", confidence=0.58, money=True),
        )
        assert scenario.summary.forwarded == 1
        assert scenario.bot.calls == []


class TestAutomatedMailIsSkipped:
    @pytest.mark.parametrize(
        "fixture", ["newsletter.json", "vacation_autoreply.json", "bounce.json"]
    )
    def test_it_is_labelled_and_never_replied_to(self, test_db, fixture):
        scenario = Scenario(
            test_db, fixture, signals(category="automated", confidence=0.97)
        )

        assert scenario.summary.skipped == 1
        assert scenario.summary.drafted == 0
        assert scenario.summary.forwarded == 0
        assert scenario.transport.drafts == {}
        assert LABEL_SKIPPED in scenario.labels

    def test_a_vacation_responder_never_starts_a_loop(self, test_db):
        # The specific case: an auto-reply that gets a holding message back is
        # how a two-machine mail loop begins.
        scenario = Scenario(
            test_db, "vacation_autoreply.json", signals(category="faq", confidence=0.9)
        )
        assert scenario.transport.drafts == {}
        assert scenario.notifier.events == []


class TestOddShapedMailDoesNotBreakARun:
    def test_an_html_only_mail_is_answered_from_its_stripped_text(self, test_db):
        scenario = Scenario(
            test_db, "html_only.json", signals(category="signup", confidence=0.9)
        )
        assert scenario.summary.drafted == 1

    def test_an_empty_mail_escalates_rather_than_crashing(self, test_db):
        from app.services.bot_service import NoRelevantContext

        resource = load_gmail("empty_body.json")
        transport = FakeTransport(mails=[resource])
        notifier = RecordingNotifier()
        adapter = GmailAdapter(transport)
        pipeline = EmailBotPipeline(
            db=test_db,
            adapter=adapter,
            classifier=FakeClassifier(default=signals(category="faq", confidence=0.8)),
            notifier=notifier,
            settings=settings(),
            bot_service=FakeBotService(error=NoRelevantContext("nothing to answer")),
        )

        async def drive():
            return await anyio.to_thread.run_sync(pipeline.run)

        summary = anyio.run(drive)

        assert summary.forwarded == 1
        assert summary.errors == 0
        assert summary.aborted_reason is None
