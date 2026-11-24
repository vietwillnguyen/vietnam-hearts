"""Metric arithmetic and exit codes. Runs in CI, makes no API call.

The runners themselves cost money, so the part that can be wrong for free -
the percentages, the gate logic, and the non-zero exit on a missed escalation -
is tested here against synthetic result sets. A recall calculation that is
quietly wrong would otherwise only ever be exercised by a run somebody then
believes.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

from unittest.mock import MagicMock

import pytest

from evals.metrics import (
    AgreementMetrics,
    GroundednessMetrics,
    TriageMetrics,
)


def record(
    metrics: TriageMetrics,
    case_id: str,
    expected_category: str,
    actual_category: str,
    expected_tier: str,
    actual_tier: str,
    language: str = "en",
    actual_language: str | None = None,
) -> None:
    metrics.record(
        case_id=case_id,
        expected_category=expected_category,
        actual_category=actual_category,
        expected_tier=expected_tier,
        actual_tier=actual_tier,
        expected_language=language,
        actual_language=actual_language or language,
    )


class TestTheRunnerExitCode:
    def _fake_classifier(self, answers):
        from inbox_core import TriageSignals

        class Fake:
            name = "fake"

            def classify(self, message):
                case_id = message.provider_message_id.removeprefix("golden-")
                category = answers.get(case_id, "human_other")
                return TriageSignals(
                    category=category,
                    language="en",
                    confidence=0.95,
                    asks_for_human=False,
                    mentions_money_or_commitment=category
                    in ("donation", "sponsorship"),
                    classifier="fake",
                )

        return Fake()

    def test_a_missed_executive_case_exits_non_zero(self, monkeypatch):
        from evals import run_triage_eval
        from evals.loader import GoldenCase

        cases = (
            GoldenCase(
                id="safeguarding-en-01",
                language="en",
                subject="Concern",
                text="I have a safeguarding concern.",
                expected_category="safeguarding_legal",
                expected_tier="needs_executive",
            ),
        )
        monkeypatch.setattr(run_triage_eval, "load_golden_set", lambda: cases)
        monkeypatch.setattr(
            run_triage_eval,
            "build_classifier",
            lambda name, model: self._fake_classifier({"safeguarding-en-01": "faq"}),
        )

        assert run_triage_eval.main([]) == 1

    def test_a_clean_run_exits_zero(self, monkeypatch, capsys):
        from evals import run_triage_eval
        from evals.loader import GoldenCase

        cases = (
            GoldenCase(
                id="safeguarding-en-01",
                language="en",
                subject="Concern",
                text="I have a safeguarding concern.",
                expected_category="safeguarding_legal",
                expected_tier="needs_executive",
            ),
        )
        monkeypatch.setattr(run_triage_eval, "load_golden_set", lambda: cases)
        monkeypatch.setattr(
            run_triage_eval,
            "build_classifier",
            lambda name, model: self._fake_classifier(
                {"safeguarding-en-01": "safeguarding_legal"}
            ),
        )

        assert run_triage_eval.main([]) == 0
        assert "Executive recall gate: PASS" in capsys.readouterr().out

    def test_the_report_names_the_gate_number(self, monkeypatch, capsys):
        from evals import run_triage_eval
        from evals.loader import GoldenCase

        cases = (
            GoldenCase(
                id="donation-en-01",
                language="en",
                subject="Donating",
                text="I would like to donate.",
                expected_category="donation",
                expected_tier="needs_executive",
                expected_mentions_money=True,
            ),
        )
        monkeypatch.setattr(run_triage_eval, "load_golden_set", lambda: cases)
        monkeypatch.setattr(
            run_triage_eval,
            "build_classifier",
            lambda name, model: self._fake_classifier({"donation-en-01": "donation"}),
        )
        run_triage_eval.main([])

        out = capsys.readouterr().out
        assert "Executive recall" in out
        assert "100.0%" in out


class TestTheJudge:
    def test_a_clean_verdict_parses(self):
        from evals.judge import parse_verdict

        verdict = parse_verdict(
            '{"mentions": {"class time": true}, "grounded": true, '
            '"violations": [], "reasoning": "fine"}'
        )
        assert verdict.passes
        assert verdict.mentions_everything

    def test_markdown_fences_are_stripped(self):
        from evals.judge import parse_verdict

        verdict = parse_verdict(
            '```json\n{"mentions": {}, "grounded": true, "violations": []}\n```'
        )
        assert verdict.grounded

    def test_an_ungrounded_verdict_does_not_pass(self):
        from evals.judge import parse_verdict

        verdict = parse_verdict(
            '{"mentions": {"class time": true}, "grounded": false, "violations": []}'
        )
        assert not verdict.passes
        assert "ungrounded" in verdict.why_not()

    def test_a_missing_fact_does_not_pass(self):
        from evals.judge import parse_verdict

        verdict = parse_verdict(
            '{"mentions": {"signup form link": false}, "grounded": true, '
            '"violations": []}'
        )
        assert not verdict.passes
        assert "signup form link" in verdict.why_not()

    def test_a_violation_does_not_pass_however_grounded(self):
        # A grounded reply that leaks an acceptance decision is not a good
        # answer.
        from evals.judge import parse_verdict

        verdict = parse_verdict(
            '{"mentions": {"class time": true}, "grounded": true, '
            '"violations": ["acceptance"]}'
        )
        assert not verdict.passes
        assert "acceptance" in verdict.why_not()

    @pytest.mark.parametrize(
        "raw",
        [
            "not json",
            '{"grounded": "yes", "mentions": {}, "violations": []}',
            '{"grounded": true, "mentions": "none", "violations": []}',
            '{"grounded": true, "mentions": {}, "violations": "none"}',
            "[1, 2, 3]",
        ],
    )
    def test_an_unreadable_verdict_raises_rather_than_passing(self, raw):
        # A judge whose output could not be read has said nothing, and counting
        # that as grounded is how a bad number becomes a good one.
        from evals.judge import parse_verdict

        with pytest.raises((ValueError, TypeError)):
            parse_verdict(raw)

    def test_the_prompt_carries_the_facts_and_the_prohibitions(self):
        from evals.judge import judge_answer

        client = MagicMock()
        client.models.generate_content.return_value = MagicMock(
            text=(
                '{"mentions": {"signup form link": true}, "grounded": true, '
                '"violations": []}'
            )
        )
        judge_answer(
            client,
            question="How do I sign up?",
            answer="Fill in the form.",
            context="The form is at example.com/form",
            must_mention=("signup form link",),
            must_not_answer=("acceptance",),
        )

        prompt = client.models.generate_content.call_args.kwargs["contents"]
        assert "signup form link" in prompt
        assert "acceptance" in prompt
        assert "How do I sign up?" in prompt
        assert "Fill in the form." in prompt

    @pytest.mark.parametrize(
        "raw",
        [
            '{"grounded": true, "violations": []}',
            '{"mentions": {}, "grounded": true, "violations": []}',
            '{"mentions": {"class time": true}, "grounded": true, "violations": []}',
        ],
    )
    def test_silence_on_a_required_fact_raises_rather_than_passing(self, raw):
        from evals.judge import parse_verdict

        with pytest.raises(ValueError):
            parse_verdict(raw, required=("class time", "signup form link"))

    def test_a_non_boolean_mention_raises(self):
        from evals.judge import parse_verdict

        with pytest.raises(ValueError):
            parse_verdict(
                '{"mentions": {"class time": "false"}, "grounded": true, '
                '"violations": []}',
                required=("class time",),
            )

    def test_judge_answer_holds_the_verdict_to_the_cases_facts(self):
        from evals.judge import judge_answer

        client = MagicMock()
        client.models.generate_content.return_value = MagicMock(
            text='{"grounded": true, "violations": []}'
        )
        with pytest.raises(ValueError):
            judge_answer(
                client,
                question="When are classes?",
                answer="Tuesdays.",
                context="Classes are on Tuesdays and Thursdays.",
                must_mention=("class days", "class time"),
                must_not_answer=(),
            )


class TestThresholdSweeps:
    def _answered(self):
        from inbox_core import TriageSignals

        from evals.loader import GoldenCase

        def signals(category, confidence):
            return TriageSignals(
                category=category,
                language="en",
                confidence=confidence,
                asks_for_human=False,
                mentions_money_or_commitment=category == "donation",
                classifier="fake",
            )

        return [
            (
                GoldenCase(
                    id="faq-confident",
                    language="en",
                    subject="x",
                    text="x",
                    expected_category="faq",
                    expected_tier="auto_answer",
                    must_mention=("a fact",),
                ),
                signals("faq", 0.9),
            ),
            (
                GoldenCase(
                    id="faq-hesitant",
                    language="en",
                    subject="x",
                    text="x",
                    expected_category="faq",
                    expected_tier="auto_answer",
                    must_mention=("a fact",),
                ),
                signals("faq", 0.5),
            ),
            (
                GoldenCase(
                    id="donation",
                    language="en",
                    subject="x",
                    text="x",
                    expected_category="donation",
                    expected_tier="needs_executive",
                    expected_mentions_money=True,
                ),
                signals("donation", 0.7),
            ),
        ]

    def test_the_sweep_has_a_row_per_threshold(self):
        from evals.calibrate_thresholds import sweep_triage_threshold

        rows = sweep_triage_threshold(self._answered(), thresholds=(0.4, 0.6, 0.8))
        assert [row["threshold"] for row in rows] == [0.4, 0.6, 0.8]

    def test_raising_the_threshold_answers_less(self):
        # The trade-off the sweep exists to make visible.
        from evals.calibrate_thresholds import sweep_triage_threshold

        rows = sweep_triage_threshold(self._answered(), thresholds=(0.4, 0.8))
        assert rows[0]["answered_share"] > rows[1]["answered_share"]

    def test_executive_recall_is_unaffected_by_the_threshold(self):
        # Because the category gate runs first. If a sweep ever showed
        # otherwise, the rule order would have been broken.
        from evals.calibrate_thresholds import sweep_triage_threshold

        rows = sweep_triage_threshold(
            self._answered(), thresholds=(0.01, 0.5, 0.9, 1.0)
        )
        assert {row["executive_recall"] for row in rows} == {1.0}

    def test_the_answer_threshold_sweep_reports_precision_and_reach(self):
        from evals.calibrate_thresholds import sweep_answer_threshold

        # (case_id, top_similarity, was_correct)
        similarities = [
            ("a", 0.9, True),
            ("b", 0.6, True),
            ("c", 0.4, False),
            ("d", 0.2, False),
        ]
        rows = sweep_answer_threshold(similarities, thresholds=(0.0, 0.5, 0.8))

        assert rows[0]["answered"] == 4
        assert rows[0]["wrong_answers"] == 2
        assert rows[1]["answered"] == 2
        assert rows[1]["wrong_answers"] == 0
        assert rows[1]["precision"] == 1.0
        assert rows[2]["answered"] == 1

    def test_the_groundedness_run_feeds_the_answer_sweep(self, tmp_path):
        # The sweep needs per-answer similarity and verdict; the counts alone
        # cannot produce it.
        import json

        from evals.calibrate_thresholds import (
            load_answer_similarities,
            sweep_answer_threshold,
        )
        from evals.run_groundedness_eval import answers_payload

        metrics = GroundednessMetrics()
        metrics.record("a", True, similarity=0.9)
        metrics.record("b", False, "ungrounded", similarity=0.4)
        path = tmp_path / "answers.json"
        path.write_text(json.dumps(answers_payload(metrics)))

        similarities = load_answer_similarities(json.loads(path.read_text()))
        assert similarities == [("a", 0.9, True), ("b", 0.4, False)]

        rows = sweep_answer_threshold(similarities, thresholds=(0.0, 0.5))
        assert rows[0]["wrong_answers"] == 1
        assert rows[1]["wrong_answers"] == 0

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"answers": [{"id": "a", "passed": True}]},
            {"answers": [{"id": "a", "similarity": 0.9, "passed": "yes"}]},
        ],
    )
    def test_a_malformed_answers_file_raises(self, payload):
        from evals.calibrate_thresholds import load_answer_similarities

        with pytest.raises(ValueError):
            load_answer_similarities(payload)


class TestAgreementMetrics:
    def test_full_agreement_is_one(self):
        metrics = AgreementMetrics()
        for index in range(5):
            metrics.record(
                f"m-{index}",
                {"category": True, "language": True},
            )
        assert metrics.overall == 1.0
        assert metrics.rate("category") == 1.0

    def test_a_disagreement_is_listed_by_field(self):
        metrics = AgreementMetrics()
        metrics.record("m-1", {"category": False, "language": True}, "faq vs donation")

        assert metrics.rate("category") == 0.0
        assert metrics.rate("language") == 1.0
        assert "category" in metrics.disagreements[0]
        assert "faq vs donation" in metrics.disagreements[0]

    def test_the_overall_rate_counts_whole_agreements(self):
        metrics = AgreementMetrics()
        metrics.record("m-1", {"category": True, "language": True})
        metrics.record("m-2", {"category": True, "language": False})

        assert metrics.overall == 0.5

    def test_an_absent_shadow_is_not_counted(self):
        metrics = AgreementMetrics()
        metrics.record("m-1", {})
        assert metrics.compared == 0
        assert metrics.overall == 1.0


class TestShadowReportReadsOnlyWhatTheRowsHold:
    def test_it_compares_the_decided_row_against_the_shadow_json(self, test_db):
        from app.models import Conversation, Message
        from evals.shadow_report import compare

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

        test_db.add(
            Message(
                conversation_id=conversation.id,
                direction="inbound",
                provider_message_id="m-1",
                action="labelled",
                category="donation",
                language="en",
                classifier="jev",
                triage_shadow={
                    "category": "faq",
                    "language": "en",
                    "classifier": "litellm:gemini/gemini-3.5-flash-lite",
                },
            )
        )
        test_db.commit()

        from evals.shadow_report import load_rows

        metrics = compare(load_rows(test_db, None, None))

        assert metrics.compared == 1
        assert metrics.rate("category") == 0.0
        assert metrics.rate("language") == 1.0

    def test_rows_without_a_shadow_are_skipped(self, test_db):
        from app.models import Conversation, Message
        from evals.shadow_report import compare, load_rows

        conversation = Conversation(
            channel="email",
            thread_key="t-2",
            sender_key="hash",
            status="bot",
            bot_reply_count=0,
        )
        test_db.add(conversation)
        test_db.commit()
        test_db.refresh(conversation)

        test_db.add(
            Message(
                conversation_id=conversation.id,
                direction="inbound",
                provider_message_id="m-2",
                action="labelled",
                category="faq",
                language="en",
                classifier="jev",
                triage_shadow=None,
            )
        )
        test_db.commit()

        assert compare(load_rows(test_db, None, None)).compared == 0

    def test_it_writes_nothing(self, test_db):
        from app.models import Message
        from evals.shadow_report import compare, load_rows

        before = test_db.query(Message).count()
        compare(load_rows(test_db, None, None))
        assert test_db.query(Message).count() == before


class TestWeeklyLiveSampling:
    """Judges what was actually sent, and stores nothing.

    The golden set says what the bot does on cases somebody thought of. This
    says what it did to real questions, which is the only measurement that can
    notice a question nobody anticipated being answered badly.
    """

    def _conversation(self, db, thread="t-1"):
        from app.models import Conversation

        conversation = Conversation(
            channel="email",
            thread_key=thread,
            sender_key="hash",
            status="bot",
            bot_reply_count=1,
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        return conversation

    def _row(self, db, conversation, **kwargs):
        from datetime import UTC, datetime

        from app.models import Message

        values = {
            "conversation_id": conversation.id,
            "direction": "outbound",
            "action": "sent",
            "category": "faq",
            "language": "en",
            "text": "No certificate is needed.",
            "created_at": datetime.now(UTC),
        }
        values.update(kwargs)
        text = values.pop("text")
        if text is not None:
            from app.utils.field_crypto import cipher_from_env

            values["body_encrypted"] = cipher_from_env().encrypt(
                text, "messages.body_encrypted"
            )
        row = Message(**values)
        db.add(row)
        db.commit()
        return row

    def test_only_sent_faq_rows_in_the_window_are_picked(self, test_db):
        from datetime import UTC, datetime, timedelta

        from evals.sample_live_answers import sent_faq_rows

        conversation = self._conversation(test_db)
        wanted = self._row(test_db, conversation)
        # A draft is measured by the acceptance rate instead: a draft the
        # captain corrected before sending is not a customer-visible answer.
        self._row(test_db, conversation, action="drafted", gmail_draft_id="d-1")
        # The sign-up template and the holding message are fixed strings and
        # cannot be ungrounded.
        self._row(test_db, conversation, category="signup")
        self._row(test_db, conversation, category="holding")
        # Outside the window.
        self._row(
            test_db,
            conversation,
            created_at=datetime.now(UTC) - timedelta(days=30),
        )

        until = datetime.now(UTC) + timedelta(minutes=1)
        since = until - timedelta(days=7)
        picked = sent_faq_rows(test_db, since, until)

        assert [row.id for row in picked] == [wanted.id]

    def test_an_empty_window_picks_nothing(self, test_db):
        from datetime import UTC, datetime, timedelta

        from evals.sample_live_answers import sent_faq_rows

        conversation = self._conversation(test_db)
        self._row(test_db, conversation)

        until = datetime.now(UTC) - timedelta(days=10)
        assert sent_faq_rows(test_db, until - timedelta(days=7), until) == []

    def test_the_prohibitions_it_judges_against_are_the_designs(self):
        from evals.sample_live_answers import LIVE_PROHIBITIONS

        joined = " ".join(LIVE_PROHIBITIONS)
        for harm in ("acceptance", "money", "phone", "when someone will reply"):
            assert harm in joined

    def test_it_judges_without_writing_anything(self, test_db):
        import asyncio
        from datetime import UTC, datetime, timedelta
        from unittest.mock import MagicMock

        from evals.sample_live_answers import evaluate, sent_faq_rows

        conversation = self._conversation(test_db)
        self._row(
            test_db,
            conversation,
            direction="inbound",
            action="labelled",
            provider_message_id="msg-1",
            text=None,
            created_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        self._row(test_db, conversation)

        from app.models import Message

        before = test_db.query(Message).count()

        import base64

        question = "Do I need a teaching certificate?"
        adapter = MagicMock()
        adapter.get_message.return_value = MagicMock(
            payload={
                "payload": {
                    "mimeType": "text/plain",
                    "body": {
                        "data": base64.urlsafe_b64encode(question.encode()).decode()
                    },
                }
            }
        )
        bot = MagicMock()

        async def search(question, limit=3):
            return [{"content": "context", "similarity": 0.8}]

        bot.knowledge_service.similarity_search = search
        bot._build_context = lambda chunks: "context"

        judge = MagicMock()
        judge.models.generate_content.return_value = MagicMock(
            text='{"mentions": {}, "grounded": true, "violations": []}'
        )

        until = datetime.now(UTC) + timedelta(minutes=1)
        rows = sent_faq_rows(test_db, until - timedelta(days=7), until)
        metrics = asyncio.run(evaluate(test_db, adapter, bot, judge, rows))

        assert metrics.judged == 1
        assert metrics.refused == 0
        judge.models.generate_content.assert_called_once()
        assert question in str(judge.models.generate_content.call_args)

        # The whole point: the inbound body was fetched from Gmail for this run
        # and discarded. Nothing new is in the database.
        assert test_db.query(Message).count() == before
        assert all(
            row.body_encrypted is None
            for row in test_db.query(Message).filter(Message.direction == "inbound")
        )

    @pytest.mark.parametrize("failing", ["fetch", "retrieval"])
    def test_one_failing_row_does_not_lose_the_report(self, test_db, failing):
        import asyncio
        from datetime import UTC, datetime, timedelta

        from evals.sample_live_answers import evaluate, sent_faq_rows

        conversation = self._conversation(test_db)
        self._row(
            test_db,
            conversation,
            direction="inbound",
            action="labelled",
            provider_message_id="msg-1",
            text=None,
            created_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        self._row(test_db, conversation)

        adapter = MagicMock()
        bot = MagicMock()
        if failing == "fetch":
            adapter.get_message.side_effect = ConnectionError("down")
        else:
            adapter.get_message.return_value = MagicMock(
                payload={
                    "payload": {
                        "mimeType": "text/plain",
                        "body": {"data": "SGVsbG8"},
                    }
                }
            )

            async def search(question, limit=3):
                raise ConnectionError("down")

            bot.knowledge_service.similarity_search = search

        until = datetime.now(UTC) + timedelta(minutes=1)
        rows = sent_faq_rows(test_db, until - timedelta(days=7), until)
        metrics = asyncio.run(evaluate(test_db, adapter, bot, MagicMock(), rows))

        assert metrics.refused == 1
        assert metrics.judged == 0
        assert any(
            f"{failing} failed: ConnectionError" in item for item in metrics.failures
        )

    def test_an_empty_week_does_not_report_a_groundedness_figure(self):
        # In draft mode nothing is sent, and a report that says 100 percent
        # would vouch for answers nobody judged.
        from evals.metrics import GroundednessMetrics
        from evals.sample_live_answers import report

        text = report(GroundednessMetrics(), days=7)

        assert "Groundedness: n/a (nothing sent in the window)" in text
        assert "100" not in text
        assert "fetched from Gmail" not in text

    def test_a_judged_week_reports_the_figure(self):
        from evals.metrics import GroundednessMetrics
        from evals.sample_live_answers import report

        metrics = GroundednessMetrics()
        metrics.record("msg-1", True)
        metrics.record("msg-2", False, "invented a date")

        text = report(metrics, days=7)

        assert "Groundedness: 50.0%" in text
        assert "fetched from Gmail for this run and discarded" in text
