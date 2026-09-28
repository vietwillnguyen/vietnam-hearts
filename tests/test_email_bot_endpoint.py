"""The admin endpoints.

The first assertion in this file is the important one: with
``EMAIL_BOT_ENABLED`` false, nothing at all is constructed. A deployment that
never turns the bot on must not be breakable by a Gmail key it does not hold.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import Conversation, EmailBotRun
from app.services.email_bot.pipeline import ALREADY_RUNNING, STALE_RUN_AFTER, RunSummary
from app.services.settings_service import set_setting

POLL = "/admin/email-bot/poll"
SYNC = "/admin/email-bot/sync-knowledge-base"


@pytest.fixture
def admin_client(client):
    from app.dependencies.auth import get_current_admin_user
    from app.main import app

    app.dependency_overrides[get_current_admin_user] = lambda: {
        "id": "test-admin",
        "email": "admin@vietnamhearts.org",
    }
    yield client
    app.dependency_overrides.pop(get_current_admin_user, None)


@pytest.fixture
def enabled():
    with patch("app.routers.admin.email_bot.EMAIL_BOT_ENABLED", True):
        yield


def in_draft_mode(db):
    set_setting(db, "EMAIL_BOT_MODE", "draft")
    set_setting(db, "ESCALATION_OWNER_EMAIL", "owner@example.com")


class TestTheOperatorStop:
    def test_enabled_false_returns_disabled(self, admin_client, test_db):
        in_draft_mode(test_db)
        with patch("app.routers.admin.email_bot.EMAIL_BOT_ENABLED", False):
            response = admin_client.post(POLL)

        assert response.status_code == 200
        assert response.json()["status"] == "disabled"

    def test_enabled_false_constructs_nothing(self, admin_client, test_db):
        in_draft_mode(test_db)
        with (
            patch("app.routers.admin.email_bot.EMAIL_BOT_ENABLED", False),
            patch("app.routers.admin.email_bot.build_pipeline") as build,
        ):
            admin_client.post(POLL)

        # No Gmail client, no classifier, no credentials read.
        build.assert_not_called()

    def test_enabled_false_writes_no_run_row(self, admin_client, test_db):
        in_draft_mode(test_db)
        with patch("app.routers.admin.email_bot.EMAIL_BOT_ENABLED", False):
            admin_client.post(POLL)
        assert test_db.query(EmailBotRun).count() == 0


class TestTheKillSwitch:
    def test_mode_off_returns_off(self, admin_client, test_db, enabled):
        set_setting(test_db, "EMAIL_BOT_MODE", "off")
        response = admin_client.post(POLL)

        assert response.status_code == 200
        assert response.json()["status"] == "off"

    def test_mode_off_constructs_nothing(self, admin_client, test_db, enabled):
        set_setting(test_db, "EMAIL_BOT_MODE", "off")
        with patch("app.routers.admin.email_bot.build_pipeline") as build:
            admin_client.post(POLL)
        build.assert_not_called()

    @pytest.mark.parametrize("value", ["aut", "true", "", "on"])
    def test_an_unrecognised_mode_reads_as_off(
        self, admin_client, test_db, enabled, value
    ):
        # The whole point of fail-closed parsing: a typo stops the bot.
        set_setting(test_db, "EMAIL_BOT_MODE", value)
        with patch("app.routers.admin.email_bot.build_pipeline") as build:
            response = admin_client.post(POLL)

        assert response.json()["status"] == "off"
        build.assert_not_called()

    def test_the_mode_is_read_fresh_on_every_call(self, admin_client, test_db, enabled):
        # No deploy, no restart: flipping the setting stops the next poll.
        set_setting(test_db, "EMAIL_BOT_MODE", "off")
        assert admin_client.post(POLL).json()["status"] == "off"

        in_draft_mode(test_db)
        with patch("app.routers.admin.email_bot.build_pipeline") as build:
            build.return_value = MagicMock(run=lambda: RunSummary())
            admin_client.post(POLL)
        build.assert_called_once()


class TestPolling:
    def test_a_successful_run_reports_its_counters(
        self, admin_client, test_db, enabled
    ):
        in_draft_mode(test_db)
        summary = RunSummary(listed=3, processed=3, drafted=2, forwarded=1)
        with patch("app.routers.admin.email_bot.build_pipeline") as build:
            build.return_value = MagicMock(run=lambda: summary)
            response = admin_client.post(POLL)

        body = response.json()
        assert body["status"] == "success"
        assert body["run"]["drafted"] == 2
        assert body["run"]["forwarded"] == 1

    def test_an_aborted_run_says_so(self, admin_client, test_db, enabled):
        in_draft_mode(test_db)
        summary = RunSummary(aborted_reason="3 consecutive infrastructure failures")
        with patch("app.routers.admin.email_bot.build_pipeline") as build:
            build.return_value = MagicMock(run=lambda: summary)
            response = admin_client.post(POLL)

        assert response.json()["status"] == "aborted"
        assert "consecutive" in response.json()["run"]["aborted_reason"]

    def test_missing_credentials_are_a_client_visible_misconfiguration(
        self, admin_client, test_db, enabled
    ):
        from app.services.email_bot.factory import EmailBotNotConfigured

        in_draft_mode(test_db)
        with patch("app.routers.admin.email_bot.build_pipeline") as build:
            build.side_effect = EmailBotNotConfigured(
                "Missing Gmail credentials: GMAIL_OAUTH_REFRESH_TOKEN"
            )
            response = admin_client.post(POLL)

        # Not a 500: somebody can fix this.
        assert response.status_code == 400
        assert "GMAIL_OAUTH_REFRESH_TOKEN" in response.json()["detail"]


class TestTheRunLock:
    """Review Focus item 5, at the endpoint."""

    def test_a_concurrent_scheduler_retry_gets_200_and_does_nothing(
        self, admin_client, test_db, enabled
    ):
        in_draft_mode(test_db)
        test_db.add(
            EmailBotRun(
                started_at=datetime.now(UTC),
                mode="draft",
                listed=0,
                processed=0,
                drafted=0,
                sent=0,
                forwarded=0,
                skipped=0,
                errors=0,
            )
        )
        test_db.commit()

        with patch("app.routers.admin.email_bot.build_pipeline") as build:
            from app.services.email_bot.pipeline import EmailBotPipeline

            real_lock = EmailBotPipeline._acquire_run
            pipeline = MagicMock()
            pipeline.run.return_value = RunSummary(aborted_reason=ALREADY_RUNNING)
            build.return_value = pipeline
            response = admin_client.post(POLL)
            assert real_lock is EmailBotPipeline._acquire_run

        # 200, not 409: a non-2xx would make Cloud Scheduler retry again on a
        # growing backoff for a condition that is already correct.
        assert response.status_code == 200
        assert response.json()["status"] == ALREADY_RUNNING

    def test_a_run_older_than_thirty_minutes_is_treated_as_dead(self, test_db):
        # The pipeline owns this rule; asserted here so the endpoint's contract
        # and the window stay documented together.
        assert STALE_RUN_AFTER == timedelta(minutes=30)


class TestKnowledgeBaseSync:
    def test_it_awaits_sync_documents_with_the_configured_doc_id(
        self, admin_client, test_db
    ):
        set_setting(test_db, "KNOWLEDGE_BASE_DOC_ID", "doc-123")
        bot = MagicMock()
        bot.sync_documents = AsyncMock(
            return_value={"status": "success", "chunks": 12, "embeddings": 12}
        )

        from app.dependencies.services import get_bot_service
        from app.main import app

        app.dependency_overrides[get_bot_service] = lambda: bot
        try:
            response = admin_client.post(SYNC)
        finally:
            app.dependency_overrides.pop(get_bot_service, None)

        assert response.status_code == 200
        bot.sync_documents.assert_awaited_once()
        assert bot.sync_documents.await_args.args[0] == "doc-123"

    def test_the_route_is_an_async_def(self):
        # It only awaits sync_documents, so paying for a threadpool hop would
        # be waste. The poll route is the opposite case and is a plain def.
        import inspect

        from app.routers.admin.email_bot import poll_inbox, sync_knowledge_base

        assert inspect.iscoroutinefunction(sync_knowledge_base)
        assert not inspect.iscoroutinefunction(poll_inbox)

    def test_an_unset_doc_id_is_a_400(self, admin_client, test_db):
        set_setting(test_db, "KNOWLEDGE_BASE_DOC_ID", "")
        assert admin_client.post(SYNC).status_code == 400

    def test_a_failed_sync_is_reported_not_swallowed(self, admin_client, test_db):
        # A silent failure leaves the bot answering from a knowledge base
        # nobody realises is stale.
        set_setting(test_db, "KNOWLEDGE_BASE_DOC_ID", "doc-123")
        bot = MagicMock()
        bot.sync_documents = AsyncMock(
            return_value={"status": "error", "message": "Document not accessible"}
        )

        from app.dependencies.services import get_bot_service
        from app.main import app

        app.dependency_overrides[get_bot_service] = lambda: bot
        try:
            response = admin_client.post(SYNC)
        finally:
            app.dependency_overrides.pop(get_bot_service, None)

        assert response.status_code == 502
        assert "not accessible" in str(response.json()["detail"])


class TestRunHistory:
    def test_runs_are_listed_newest_first(self, admin_client, test_db):
        for index in range(3):
            test_db.add(
                EmailBotRun(
                    started_at=datetime.now(UTC) - timedelta(hours=index),
                    finished_at=datetime.now(UTC),
                    mode="draft",
                    listed=index,
                    processed=index,
                    drafted=index,
                    sent=0,
                    forwarded=0,
                    skipped=0,
                    errors=0,
                )
            )
        test_db.commit()

        runs = admin_client.get("/admin/email-bot/runs").json()["runs"]
        assert [run["listed"] for run in runs] == [0, 1, 2]

    def test_timestamps_carry_an_explicit_utc_offset(self, admin_client, test_db):
        started = datetime(2026, 9, 28, 21, 25, 5)
        test_db.add(
            EmailBotRun(
                started_at=started,
                finished_at=started + timedelta(minutes=1),
                mode="draft",
                listed=0,
                processed=0,
                drafted=0,
                sent=0,
                forwarded=0,
                skipped=0,
                errors=0,
            )
        )
        test_db.commit()

        run = admin_client.get("/admin/email-bot/runs").json()["runs"][0]
        assert datetime.fromisoformat(run["started_at"]) == started.replace(tzinfo=UTC)
        assert datetime.fromisoformat(run["finished_at"]).utcoffset() == timedelta(0)

    def test_the_limit_is_clamped(self, admin_client, test_db):
        assert admin_client.get("/admin/email-bot/runs?limit=9999").status_code == 200
        assert admin_client.get("/admin/email-bot/runs?limit=0").status_code == 200

    def test_no_runs_is_an_empty_list(self, admin_client, test_db):
        assert admin_client.get("/admin/email-bot/runs").json()["runs"] == []


class TestEscalationList:
    def _paused(self, db, thread_key: str, status: str) -> Conversation:
        conversation = Conversation(
            channel="email",
            thread_key=thread_key,
            sender_key="hash",
            status=status,
            bot_reply_count=1,
            last_category="donation",
            last_tier="needs_executive",
            pause_reason="needs_executive: executive category donation",
            updated_at=datetime.now(UTC),
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        return conversation

    def test_paused_threads_are_listed_with_their_thread_key(
        self, admin_client, test_db
    ):
        self._paused(test_db, "thread-1", "paused_handoff")
        listed = admin_client.get("/admin/email-bot/escalations").json()["escalations"]

        assert len(listed) == 1
        assert listed[0]["thread_key"] == "thread-1"
        assert listed[0]["category"] == "donation"

    def test_the_listing_never_exposes_a_sender(self, admin_client, test_db):
        # The conversation row holds only a hash of the address, by design, and
        # the endpoint must not leak even that.
        self._paused(test_db, "thread-1", "paused_handoff")
        body = admin_client.get("/admin/email-bot/escalations").text
        assert "sender_key" not in body
        assert "@" not in body

    def test_an_active_thread_is_not_listed(self, admin_client, test_db):
        self._paused(test_db, "thread-1", "bot")
        assert (
            admin_client.get("/admin/email-bot/escalations").json()["escalations"] == []
        )


class TestResume:
    def test_resuming_hands_the_thread_back_to_the_bot(self, admin_client, test_db):
        conversation = Conversation(
            channel="email",
            thread_key="thread-1",
            sender_key="hash",
            status="paused_manual",
            bot_reply_count=1,
            pause_reason="a human replied in this thread",
        )
        test_db.add(conversation)
        test_db.commit()
        test_db.refresh(conversation)

        response = admin_client.post(
            f"/admin/email-bot/conversations/{conversation.id}/resume"
        )

        assert response.status_code == 200
        test_db.refresh(conversation)
        assert conversation.status == "bot"
        assert conversation.pause_reason is None

    def test_resuming_does_not_reset_the_reply_counter(self, admin_client, test_db):
        conversation = Conversation(
            channel="email",
            thread_key="thread-1",
            sender_key="hash",
            status="paused_manual",
            bot_reply_count=1,
        )
        test_db.add(conversation)
        test_db.commit()
        test_db.refresh(conversation)

        admin_client.post(f"/admin/email-bot/conversations/{conversation.id}/resume")

        test_db.refresh(conversation)
        assert conversation.bot_reply_count == 1

    def test_an_unknown_conversation_is_a_404(self, admin_client, test_db):
        assert (
            admin_client.post("/admin/email-bot/conversations/99999/resume").status_code
            == 404
        )


ENDPOINTS = [
    ("post", POLL),
    ("post", SYNC),
    ("get", "/admin/email-bot/runs"),
    ("get", "/admin/email-bot/escalations"),
    ("post", "/admin/email-bot/conversations/{conversation_id}/resume"),
]


class TestAuthentication:
    """Every one of these endpoints is gated, structurally.

    Asserted on the route's own dependency list rather than on the status code
    an unauthenticated call happens to produce. The mocked auth service in
    ``conftest.py`` makes that code an artifact of test ordering - the existing
    ``/admin/logs`` auth test shows the same variation - whereas "this route
    carries the admin dependency" is the property that actually matters and
    cannot drift.
    """

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_every_endpoint_carries_the_admin_dependency(self, method, path):
        from app.dependencies.auth import get_current_admin_user
        from app.main import app

        matching = [
            route
            for route in app.routes
            if getattr(route, "path", None) == path
            and method.upper() in getattr(route, "methods", set())
        ]
        assert matching, f"no {method.upper()} route mounted at {path}"

        guards = {dependency.call for dependency in matching[0].dependant.dependencies}
        assert get_current_admin_user in guards

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_no_endpoint_answers_an_unauthenticated_call(self, client, method, path):
        response = getattr(client, method)(path.replace("{conversation_id}", "1"))
        assert response.status_code != 200


class TestMetricsEndpoint:
    """The two numbers the evaluation gate is argued from.

    Both are grouped and reported the way the design states the gate, so the
    card and the design cannot drift into saying different things.
    """

    def _drafted(self, db, kind: str, outcome: str | None, index: int):
        from app.models import Conversation, Message

        conversation = Conversation(
            channel="email",
            thread_key=f"thread-{kind}-{index}",
            sender_key="hash",
            status="bot",
            bot_reply_count=1,
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        db.add(
            Message(
                conversation_id=conversation.id,
                direction="outbound",
                action="drafted",
                language="en",
                text="a reply",
                category=kind,
                gmail_draft_id=f"draft-{kind}-{index}",
                draft_outcome=outcome,
            )
        )
        db.commit()

    def test_acceptance_is_grouped_by_the_answer_path(self, admin_client, test_db):
        # The gate is stated per kind, so one aggregate would not answer it.
        for index in range(9):
            self._drafted(test_db, "signup", "sent_unchanged", index)
        self._drafted(test_db, "signup", "sent_edited", 99)
        self._drafted(test_db, "faq", "sent_unchanged", 0)

        body = admin_client.get("/admin/email-bot/metrics").json()
        acceptance = body["draft_acceptance"]

        assert acceptance["signup"]["rate"] == pytest.approx(0.9)
        assert acceptance["signup"]["resolved"] == 10
        assert acceptance["faq"]["rate"] == 1.0

    def test_each_kind_carries_its_gate_from_the_table(self, admin_client, test_db):
        from app.services.email_bot.gates import ACCEPTANCE_GATES

        self._drafted(test_db, "signup", "sent_unchanged", 0)
        self._drafted(test_db, "donation", "deleted", 0)

        body = admin_client.get("/admin/email-bot/metrics").json()

        assert body["acceptance_gates"] == dict(ACCEPTANCE_GATES)
        assert body["draft_acceptance"]["signup"]["gate"] == ACCEPTANCE_GATES["signup"]
        # No gate is stated for this kind, and the card must not invent one.
        assert body["draft_acceptance"]["donation"]["gate"] is None

    def test_an_unresolved_kind_reports_no_rate_rather_than_zero(
        self, admin_client, test_db
    ):
        # A rate of 0 and "not measured yet" must not look the same: the gate
        # needs two weeks of the latter before it means anything.
        self._drafted(test_db, "faq", "pending", 0)

        body = admin_client.get("/admin/email-bot/metrics").json()
        assert body["draft_acceptance"]["faq"]["rate"] is None

    def test_pending_drafts_are_not_counted_as_resolved(self, admin_client, test_db):
        self._drafted(test_db, "signup", "sent_unchanged", 0)
        self._drafted(test_db, "signup", "pending", 1)

        body = admin_client.get("/admin/email-bot/metrics").json()
        assert body["draft_acceptance"]["signup"]["resolved"] == 1
        assert body["draft_acceptance"]["signup"]["rate"] == 1.0

    def test_no_drafts_is_an_empty_acceptance_summary(self, admin_client, test_db):
        body = admin_client.get("/admin/email-bot/metrics").json()
        assert body["draft_acceptance"] == {}

    def test_shadow_agreement_is_reported_per_field(self, admin_client, test_db):
        from app.models import Conversation, Message

        conversation = Conversation(
            channel="email",
            thread_key="t-1",
            sender_key="hash",
            status="bot",
            bot_reply_count=0,
        )
        test_db.add(conversation)
        test_db.commit()
        test_db.refresh(conversation)

        for index, (category, shadow_category) in enumerate(
            [("faq", "faq"), ("donation", "faq")]
        ):
            test_db.add(
                Message(
                    conversation_id=conversation.id,
                    direction="inbound",
                    provider_message_id=f"m-{index}",
                    action="labelled",
                    category=category,
                    language="en",
                    classifier="jev",
                    triage_shadow={
                        "category": shadow_category,
                        "language": "en",
                        "classifier": "litellm:gemini",
                    },
                )
            )
        test_db.commit()

        agreement = admin_client.get("/admin/email-bot/metrics").json()[
            "shadow_agreement"
        ]
        assert agreement["compared"] == 2
        assert agreement["category_agreement"] == pytest.approx(0.5)
        assert agreement["language_agreement"] == 1.0

    def test_confidence_is_not_compared(self, admin_client, test_db):
        # Jev's probabilities are calibrated and a general model's self-report
        # is not, so comparing the two numbers would manufacture disagreements.
        body = admin_client.get("/admin/email-bot/metrics").json()
        assert "confidence_agreement" not in body["shadow_agreement"]

    def test_nothing_shadowed_reports_no_rate(self, admin_client, test_db):
        agreement = admin_client.get("/admin/email-bot/metrics").json()[
            "shadow_agreement"
        ]
        assert agreement["compared"] == 0
        assert agreement["category_agreement"] is None

    def test_the_response_carries_no_sender_or_mail_content(
        self, admin_client, test_db
    ):
        self._drafted(test_db, "signup", "sent_unchanged", 0)
        body = admin_client.get("/admin/email-bot/metrics").text
        assert "@" not in body
        assert "a reply" not in body

    def test_the_endpoint_carries_the_admin_dependency(self):
        from app.dependencies.auth import get_current_admin_user
        from app.main import app

        route = next(
            r
            for r in app.routes
            if getattr(r, "path", None) == "/admin/email-bot/metrics"
        )
        assert get_current_admin_user in {
            dependency.call for dependency in route.dependant.dependencies
        }

    def test_the_run_summary_reports_reconciled_drafts(self, admin_client, test_db):
        from datetime import UTC, datetime

        from app.models import EmailBotRun

        test_db.add(
            EmailBotRun(
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                mode="draft",
                listed=0,
                processed=0,
                drafted=0,
                sent=0,
                forwarded=0,
                skipped=0,
                reconciled=4,
                errors=0,
            )
        )
        test_db.commit()

        runs = admin_client.get("/admin/email-bot/runs").json()["runs"]
        assert runs[0]["reconciled"] == 4


class TestCapsOnTheMetricsEndpoint:
    def test_the_caps_are_reported(self, admin_client, test_db):
        body = admin_client.get("/admin/email-bot/metrics").json()
        caps = body["caps"]

        assert caps["daily_cap"] == 30
        assert caps["per_sender_cap"] == 2
        assert caps["sent_today"] == 0
        assert caps["remaining_today"] == 30

    def test_a_send_today_is_counted(self, admin_client, test_db):
        from datetime import UTC, datetime

        from app.models import Conversation, Message

        conversation = Conversation(
            channel="email",
            thread_key="t-1",
            sender_key="hash-a",
            status="bot",
            bot_reply_count=1,
        )
        test_db.add(conversation)
        test_db.commit()
        test_db.refresh(conversation)
        test_db.add(
            Message(
                conversation_id=conversation.id,
                direction="outbound",
                action="sent",
                language="en",
                text="a reply",
                gmail_message_id_out="sent-1",
                created_at=datetime.now(UTC),
            )
        )
        test_db.commit()

        caps = admin_client.get("/admin/email-bot/metrics").json()["caps"]
        assert caps["sent_today"] == 1
        assert caps["remaining_today"] == 29

    def test_a_configured_cap_is_reflected(self, admin_client, test_db):
        from app.services.settings_service import set_setting

        # The canary week runs at 10, so the card has to show that rather than
        # the design default.
        set_setting(test_db, "EMAIL_BOT_DAILY_SEND_CAP", "10")
        caps = admin_client.get("/admin/email-bot/metrics").json()["caps"]
        assert caps["daily_cap"] == 10

    def test_the_caps_carry_no_sender(self, admin_client, test_db):
        body = admin_client.get("/admin/email-bot/metrics").text
        assert "sent_today_by_sender" not in body
        assert "@" not in body
