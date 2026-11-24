"""What the inbox bot does today, pinned end to end so its internals can change.

Every scenario goes in through the same doors production uses -
``factory.build_pipeline`` and ``EmailBotPipeline.run`` in an anyio worker
thread - with every external system replaced at its edge and nothing inside
the bot replaced at all: a fake Gmail transport, a fake vendor client for each
classifier (the decider and the shadow), a fake vector search and Gemini
client behind a real ``BotService``, a fake SMTP service behind the real
forward, and a fake HTTP client behind the real Discord post.

What comes out is everything a person or the dashboard could observe: the
labels on each mail, every draft and send as a mail client would read it, the
forwards, the Discord posts and alerts, the run summary and run row, the
settings the bot changed, every conversation and message row with its stored
text decrypted, and what the models were asked. That is compared with
``fixtures/email_bot_characterisation.json``.

The golden file was recorded from the code before the inbox core moved into
the ``inbox-core`` package, so a difference is a change in behaviour. When a
change is intended, re-record with ``UPDATE_CHARACTERISATION=1`` and review
the diff of the golden file line by line; every changed line is a behaviour
change to justify.

Nothing here is real: every address is on example.com and every message was
written for the fixture.
"""

from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email import message_from_bytes
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import pytest

from app.models import Conversation, EmailBotRun, Message
from app.services.bot_service import BotService
from app.services.channels.gmail_transport import GmailAuthRevoked, RawMail
from app.services.conversation_store import SqlConversationStore
from app.services.email_bot import factory
from app.services.email_bot.settings import (
    SETTING_LAST_ERROR,
    SETTING_MODE,
    EmailBotSettings,
)
from app.services.settings_service import get_setting, set_setting
from tests.fixtures import gmail_payloads as payloads
from tests.fixtures.email_bot import (
    FakeTransport,
    SendingFakeTransport,
    load_gmail,
)

GOLDEN = Path(__file__).parent / "fixtures" / "email_bot_characterisation.json"
UPDATE = os.environ.get("UPDATE_CHARACTERISATION") == "1"

OWNER = payloads.TEST_OWNER
ADMIN = "admin@example.com"
FORM = "https://forms.example.com/volunteer-signup"
DISCORD = "https://discord.example.com/api/webhooks/1/characterisation"

# ------------------------------------------------------------------ scripts
#
# What each model says, keyed by the subject the classifier is shown. One
# table drives both vendor fakes, so the decider and the shadow agree unless a
# row says otherwise.


@dataclass(frozen=True)
class Verdict:
    category: str
    language: str = "en"
    confidence: float = 0.9
    asks_for_human: bool = False
    money: bool = False


@dataclass(frozen=True)
class Knowledge:
    """What the vector search returns for a question, and what Gemini answers."""

    similarities: tuple[float, ...] = (0.82, 0.71, 0.4)
    answer: str = "Classes run on weekday mornings and no certificate is needed."
    search_fails: bool = False
    generation_fails: bool = False


VERDICTS: dict[str, Verdict | str] = {
    "Volunteering with Vietnam Hearts": Verdict("signup", confidence=0.94),
    "Đăng ký làm tình nguyện viên": Verdict("signup", language="vi", confidence=0.89),
    "Question about the classes": Verdict("faq", confidence=0.86),
    "Re: Question about the classes": Verdict("faq", confidence=0.88),
    "Interested in helping": Verdict("faq", confidence=0.9),
    "(no subject)": Verdict("faq", confidence=0.7),
    "Lo ngại về một lớp học": Verdict(
        "safeguarding_legal", language="vi", confidence=0.91
    ),
    "Re: Lo ngại về một lớp học": Verdict("faq", language="vi", confidence=0.93),
    "Corporate sponsorship proposal": Verdict(
        "sponsorship", confidence=0.82, money=True
    ),
    "Class times, and a donation": Verdict("donation", confidence=0.58, money=True),
    "Please call me": Verdict("faq", confidence=0.92, asks_for_human=True),
    "Hola": Verdict("faq", language="other", confidence=0.9),
    "Maybe a volunteer question": Verdict("signup", confidence=0.41),
    "My Thursday shift": Verdict("volunteer_ops", confidence=0.9),
    "Your receipt": Verdict("automated", confidence=0.95),
    "Where are you based?": Verdict("faq", confidence=0.9),
    "Do you pay volunteers?": Verdict("faq", confidence=0.9),
    "Teaching on weekends": Verdict("signup", confidence=0.93),
    "Teaching in the evening": Verdict("signup", confidence=0.93),
    "Teaching in the summer": Verdict("signup", confidence=0.93),
    # The decider fails for these; the shadow is never asked.
    "Classifier outage": "unavailable",
    "Classifier outage 2": "unavailable",
    "Classifier outage 3": "unavailable",
    "Classifier outage 4": "unavailable",
}

# Where the shadow disagrees with the decider, so its record is distinguishable.
SHADOW_OVERRIDES: dict[str, Verdict] = {
    "Question about the classes": Verdict("signup", confidence=0.7),
}

KNOWLEDGE: dict[str, Knowledge] = {
    "Question about the classes": Knowledge(),
    "Re: Question about the classes": Knowledge(),
    "Re: Lo ngại về một lớp học": Knowledge(),
    "Interested in helping": Knowledge(similarities=(0.42, 0.3)),
    "(no subject)": Knowledge(similarities=()),
    "Where are you based?": Knowledge(answer="INSUFFICIENT_CONTEXT"),
    "Do you pay volunteers?": Knowledge(generation_fails=True),
}


def _verdict_for(subject: str, *, shadow: bool) -> Verdict | str:
    if shadow and subject in SHADOW_OVERRIDES:
        return SHADOW_OVERRIDES[subject]
    return VERDICTS.get(subject, Verdict("human_other", confidence=0.5))


# ------------------------------------------------------------- vendor fakes


class ModelLog:
    """Everything the models were asked, in order."""

    def __init__(self) -> None:
        self.triage_system_prompts: list[str] = []
        self.jev_questions: list[Any] = []
        self.classified: list[tuple[str, str]] = []
        self.retrieval_queries: list[str] = []
        self.generation_prompts: list[str] = []
        self.summary_prompts: list[str] = []


def _jev_client_class(log: ModelLog) -> type:
    class FakeJevClient:
        def __init__(self, api_key: str, model: str) -> None:
            self.model = model

        def system_one(self, *, state, questions, model):
            subject = state.get("subject", "")
            log.jev_questions.append(questions)
            log.classified.append(("jev", subject))
            verdict = _verdict_for(subject, shadow=False)
            if verdict == "unavailable":
                raise ConnectionError("jev is down")
            assert isinstance(verdict, Verdict)
            return SimpleNamespace(
                choices={
                    "category": SimpleNamespace(
                        choice=verdict.category, confidence=verdict.confidence
                    ),
                    "language": SimpleNamespace(
                        choice=verdict.language, confidence=0.99
                    ),
                },
                nouls={
                    "asks_for_human": SimpleNamespace(
                        noul=0.9 if verdict.asks_for_human else 0.1
                    ),
                    "mentions_money_or_commitment": SimpleNamespace(
                        noul=0.9 if verdict.money else 0.1
                    ),
                },
            )

    return FakeJevClient


def _litellm_completion(log: ModelLog, *, deciding: bool) -> Callable[..., Any]:
    def completion(**kwargs):
        messages = kwargs["messages"]
        log.triage_system_prompts.append(messages[0]["content"])
        state = json.loads(messages[1]["content"])
        subject = state.get("subject", "")
        log.classified.append(("litellm", subject))
        verdict = _verdict_for(subject, shadow=not deciding)
        if verdict == "unavailable":
            raise ConnectionError("litellm is down")
        assert isinstance(verdict, Verdict)
        content = json.dumps(
            {
                "category": verdict.category,
                "language": verdict.language,
                "confidence": verdict.confidence,
                "asks_for_human": verdict.asks_for_human,
                "mentions_money_or_commitment": verdict.money,
            }
        )
        return {"choices": [{"message": {"content": content}}]}

    return completion


class FakeGemini:
    """Gemini's ``models.generate_content``, for both answers and summaries."""

    def __init__(self, log: ModelLog, knowledge_for: Callable[[str], Knowledge]):
        self._log = log
        self._knowledge_for = knowledge_for
        self.models = self

    def generate_content(self, *, model: str, contents: str):
        if contents.startswith("Summarise this email"):
            self._log.summary_prompts.append(contents)
            return SimpleNamespace(text="Someone wrote about volunteering.")
        self._log.generation_prompts.append(contents)
        question = contents.rsplit("Question: ", 1)[-1]
        knowledge = self._knowledge_for(question)
        if knowledge.generation_fails:
            raise RuntimeError("gemini is over quota")
        chunks = contents.count("Context ")
        return SimpleNamespace(text=f"{knowledge.answer} [{chunks} context chunks]")


class FakeKnowledgeService:
    """The vector search ``BotService`` reads, answering by the question asked."""

    def __init__(self, log: ModelLog, questions: Mapping[str, str]) -> None:
        self._log = log
        # Body text to subject, so a retrieval query can be traced to its mail.
        self._questions = dict(questions)
        self.gemini_client = FakeGemini(log, self.knowledge_for)

    def knowledge_for(self, query: str) -> Knowledge:
        for text, subject in self._questions.items():
            if text.strip() and text.strip() in query:
                return KNOWLEDGE.get(subject, Knowledge())
        return KNOWLEDGE.get(query.strip(), Knowledge(similarities=()))

    async def similarity_search(self, query: str, limit: int = 3, threshold=0.3):
        from app.services.knowledge_service import EmbeddingsUnavailable

        self._log.retrieval_queries.append(query)
        knowledge = self.knowledge_for(query)
        if knowledge.search_fails:
            raise EmbeddingsUnavailable("vector search failed")
        return [
            {
                "content": f"Knowledge chunk {index + 1}.",
                "similarity": similarity,
                "source_document_id": "kb-doc",
            }
            for index, similarity in enumerate(knowledge.similarities[:limit])
        ]


class FakeEmailService:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_custom_email(self, **kwargs) -> bool:
        kwargs.pop("db", None)
        self.sent.append(kwargs)
        return True


class FakeHttpClient:
    posts: list[dict[str, Any]] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url: str, json: Any = None):
        FakeHttpClient.posts.append({"url": url, "json": json})


# ---------------------------------------------------------------- the world


@dataclass
class World:
    db: Any
    monkeypatch: Any
    log: ModelLog = field(default_factory=ModelLog)
    email: FakeEmailService = field(default_factory=FakeEmailService)
    transport: FakeTransport | None = None
    summaries: list[dict[str, Any]] = field(default_factory=list)

    def bot_service(self, mails: list[Mapping[str, Any]]) -> BotService:
        questions = {}
        for mail in mails:
            parsed = _parse(mail)
            questions[parsed["text"]] = parsed["subject"]
        service = BotService.__new__(BotService)
        service.knowledge_service = FakeKnowledgeService(self.log, questions)
        service.document_service = None
        service.supabase = None
        return service

    def poll(
        self,
        mails: list[Mapping[str, Any]],
        *,
        settings: EmailBotSettings,
        threads: Mapping[str, list[Mapping[str, Any]]] | None = None,
        decider: str = "jev",
        sending: bool = False,
        revoked: bool = False,
        mutate: Callable[[FakeTransport], None] | None = None,
    ) -> dict[str, Any]:
        """One poll through the factory, against a transport that remembers drafts."""
        transport_class = SendingFakeTransport if sending else FakeTransport
        drafts = dict(self.transport.drafts) if self.transport else None
        transport = transport_class(mails=mails, threads=threads, drafts=drafts)
        if self.transport is not None:
            transport.deleted_drafts = list(self.transport.deleted_drafts)
            transport._draft_threads.update(self.transport._draft_threads)
            transport._next_draft = self.transport._next_draft
        if mutate is not None:
            mutate(transport)
        if revoked:

            def refuse(**_kwargs):
                raise GmailAuthRevoked("invalid_grant: Token has been revoked.")

            transport.list_unprocessed = refuse
        self.transport = transport

        monkeypatch = self.monkeypatch
        monkeypatch.setattr(
            factory.GmailTransport,
            "from_credentials",
            classmethod(lambda cls, *a, **k: transport),
        )
        import app.services.email_service as email_module

        monkeypatch.setattr(email_module, "email_service", self.email)
        import httpx

        monkeypatch.setattr(httpx, "Client", FakeHttpClient)
        import typesafe_sdk

        monkeypatch.setattr(typesafe_sdk, "TypeSafeClient", _jev_client_class(self.log))
        import litellm

        monkeypatch.setattr(
            litellm,
            "completion",
            _litellm_completion(self.log, deciding=decider == "litellm"),
        )
        monkeypatch.setattr(factory.config, "ADMIN_EMAILS", [ADMIN])

        credentials = factory.Credentials(
            gmail_client_id="client-id",
            gmail_client_secret="client-secret",
            gmail_refresh_token="refresh-token",
            typesafe_api_key="typesafe-key" if decider == "jev" else "",
            discord_webhook_url=DISCORD,
        )
        pipeline = factory.build_pipeline(
            self.db,
            settings,
            credentials=credentials,
            bot_service=self.bot_service(mails),
        )

        async def drive():
            return await anyio.to_thread.run_sync(pipeline.run)

        summary = anyio.run(drive).as_dict()
        self.summaries.append(summary)
        return summary


def bot_settings(**overrides: Any) -> EmailBotSettings:
    values: dict[str, Any] = {
        "mode": "draft",
        "auto_languages": frozenset({"en"}),
        "escalation_owner_email": OWNER,
        "signup_form_link": FORM,
        "triage_classifier": "jev",
        "triage_fallback_model": "gemini/gemini-3.5-flash-lite",
        "triage_confidence_threshold": 0.6,
        "answer_threshold": 0.5,
        "per_run_cap": 40,
        "daily_send_cap": 30,
        "per_sender_daily_cap": 2,
        "class_start_time": "09:30",
        "class_end_time": "10:30",
        "teaching_days": "Tuesday, Thursday",
        "timezone": "Asia/Ho_Chi_Minh",
    }
    values.update(overrides)
    return EmailBotSettings(**values)


def _parse(mail: Mapping[str, Any]) -> dict[str, str]:
    from app.services.channels.mail_builder import extract_text

    raw = RawMail.from_resource(dict(mail))
    headers = raw.headers()
    return {
        "subject": (headers.get("subject") or "").strip(),
        "text": extract_text(raw.payload),
    }


# ------------------------------------------------------------ observation


def _mime(raw: bytes) -> dict[str, Any]:
    parsed = message_from_bytes(raw)
    body = (parsed.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
    return {
        "to": parsed["To"],
        "subject": str(parsed["Subject"]),
        "in_reply_to": parsed["In-Reply-To"],
        "references": parsed["References"],
        "auto_submitted": parsed["Auto-Submitted"],
        "body": body,
    }


def _thread_of(transport: FakeTransport, draft_id: str) -> str:
    return transport._draft_threads.get(draft_id, "")


def observe(world: World) -> dict[str, Any]:
    db = world.db
    db.expire_all()
    transport = world.transport
    assert transport is not None
    store = SqlConversationStore(db)

    conversations = []
    for conversation in db.query(Conversation).order_by(Conversation.id).all():
        conversations.append(
            {
                "id": conversation.id,
                "channel": conversation.channel,
                "thread_key": conversation.thread_key,
                "status": conversation.status,
                "pause_reason": conversation.pause_reason,
                "bot_reply_count": conversation.bot_reply_count,
                "last_category": conversation.last_category,
                "last_tier": conversation.last_tier,
                "sender_key_is_hash": len(conversation.sender_key or "") == 64,
            }
        )

    content = {}
    for conversation in db.query(Conversation).all():
        for stored in store.messages(conversation.id):
            content[stored.id] = {
                "subject": stored.subject,
                "body": stored.body,
                "sender_address": stored.sender_address,
            }

    messages = []
    for row in db.query(Message).order_by(Message.id).all():
        messages.append(
            {
                "id": row.id,
                "conversation_id": row.conversation_id,
                "direction": row.direction,
                "provider_message_id": row.provider_message_id,
                "action": row.action,
                "category": row.category,
                "tier": row.tier,
                "language": row.language,
                "confidence": row.triage_confidence,
                "classifier": row.classifier,
                "shadow": row.triage_shadow,
                "sources": row.sources,
                "handled": row.handled_at is not None,
                "draft_outcome": row.draft_outcome,
                "gmail_draft_id": row.gmail_draft_id,
                "gmail_message_id_out": row.gmail_message_id_out,
                "gmail_thread_id": row.gmail_thread_id,
                "rfc_message_id": row.rfc_message_id,
                "sender_key_is_hash": (
                    None if row.sender_key is None else len(row.sender_key) == 64
                ),
                "content": content.get(row.id),
                "content_erased": row.content_erased_at is not None,
            }
        )

    runs = [
        {
            "mode": run.mode,
            "finished": run.finished_at is not None,
            "listed": run.listed,
            "processed": run.processed,
            "drafted": run.drafted,
            "sent": run.sent,
            "forwarded": run.forwarded,
            "skipped": run.skipped,
            "reconciled": run.reconciled,
            "capped": run.capped,
            "errors": run.errors,
            "aborted_reason": run.aborted_reason,
        }
        for run in db.query(EmailBotRun).order_by(EmailBotRun.id).all()
    ]

    labels: dict[str, list[list[str]]] = {}
    for message_id, names in transport.applied:
        labels.setdefault(message_id, []).append(list(names))

    drafts = {
        draft_id: {"thread": _thread_of(transport, draft_id), **_mime(raw)}
        for draft_id, raw in sorted(transport.drafts.items())
    }
    sent = (
        [_mime(raw) for raw in transport.sent]
        if isinstance(transport, SendingFakeTransport)
        else []
    )

    return {
        "summaries": world.summaries,
        "runs": runs,
        "settings": {
            "EMAIL_BOT_MODE": get_setting(db, SETTING_MODE, None),
            "EMAIL_BOT_LAST_ERROR": get_setting(db, SETTING_LAST_ERROR, None),
        },
        "labels": labels,
        "label_names_ensured": sorted(transport.labels),
        "drafts": drafts,
        "deleted_drafts": transport.deleted_drafts,
        "sent": sent,
        "forwards": world.email.sent,
        "discord": [post["json"] for post in FakeHttpClient.posts],
        "conversations": conversations,
        "messages": messages,
        "models": {
            "classified": world.log.classified,
            "retrieval_queries": world.log.retrieval_queries,
            "generation_prompts": world.log.generation_prompts,
            "summary_prompts": world.log.summary_prompts,
        },
    }


# --------------------------------------------------------------- the corpus


def mail(
    message_id: str,
    subject: str,
    text: str = "A question written for this fixture.",
    *,
    thread_id: str | None = None,
    from_address: str = payloads.TEST_SENDER,
    **kwargs: Any,
) -> dict[str, Any]:
    return payloads.message(
        message_id=message_id,
        thread_id=thread_id or f"thread-{message_id}",
        subject=subject,
        from_address=from_address,
        text=text,
        rfc_message_id=f"<{message_id}@mail.example.com>",
        **kwargs,
    )


RECORDED = [
    "signup_en.json",
    "signup_vi.json",
    "faq_en.json",
    "html_only.json",
    "empty_body.json",
    "newsletter.json",
    "vacation_autoreply.json",
    "bounce.json",
    "safeguarding_vi.json",
    "sponsorship_en.json",
    "multi_topic_en.json",
]


def corpus() -> list[dict[str, Any]]:
    """Every path one poll can take, one mail each, from distinct senders where it matters."""
    mails = [load_gmail(name) for name in RECORDED]
    mails += [
        mail("syn-call", "Please call me", "Could someone phone me about this?"),
        mail("syn-hola", "Hola", "Hola, quiero ser voluntario."),
        mail("syn-unsure", "Maybe a volunteer question", "I might want to help?"),
        mail("syn-ops", "My Thursday shift", "I cannot make Thursday this week."),
        mail("syn-receipt", "Your receipt", "Thanks for your order."),
        mail("syn-outage", "Classifier outage", "Some ordinary question."),
        mail("syn-refusal", "Where are you based?", "Where is the classroom?"),
        mail(
            "syn-gen-down",
            "Do you pay volunteers?",
            "Is the volunteering paid?",
            from_address="payer@example.com",
        ),
        mail(
            "syn-owner",
            "Forwarded note",
            "Internal note.",
            from_address=f"Owner <{OWNER}>",
        ),
        mail(
            "syn-admin",
            "Admin note",
            "Admin note.",
            from_address=f"Admin <{ADMIN}>",
        ),
        mail(
            "syn-self",
            "Note to self",
            "Inbox writing to itself.",
            from_address=payloads.TEST_INBOX,
        ),
        mail(
            "syn-nosender",
            "No sender",
            "Nobody to reply to.",
            from_address="not an address",
        ),
    ]
    return mails


# -------------------------------------------------------------- scenarios


def scenario_draft_corpus(world: World) -> None:
    world.poll(corpus(), settings=bot_settings(mode="draft"))


def scenario_auto_corpus(world: World) -> None:
    extra = [
        mail(
            "syn-weekend",
            "Teaching on weekends",
            "Can I teach on weekends?",
            from_address="busy@example.com",
        ),
        mail(
            "syn-evening",
            "Teaching in the evening",
            "Evenings?",
            from_address="busy@example.com",
        ),
        mail(
            "syn-summer",
            "Teaching in the summer",
            "Summer?",
            from_address="busy@example.com",
        ),
    ]
    world.poll(corpus() + extra, settings=bot_settings(mode="auto"), sending=True)


def scenario_auto_daily_cap(world: World) -> None:
    mails = [
        mail(
            f"syn-cap-{index}",
            "Teaching on weekends",
            "Weekends?",
            from_address=f"person{index}@example.com",
        )
        for index in range(3)
    ]
    world.poll(
        mails, settings=bot_settings(mode="auto", daily_send_cap=2), sending=True
    )


def scenario_off(world: World) -> None:
    world.poll(corpus(), settings=bot_settings(mode="off"))


def scenario_litellm_decides(world: World) -> None:
    mails = [
        load_gmail("signup_en.json"),
        load_gmail("faq_en.json"),
        load_gmail("safeguarding_vi.json"),
    ]
    world.poll(
        mails, settings=bot_settings(triage_classifier="litellm"), decider="litellm"
    )


def scenario_no_form_link(world: World) -> None:
    world.poll(
        [load_gmail("signup_en.json")], settings=bot_settings(signup_form_link="")
    )


def scenario_follow_ups(world: World) -> None:
    """An answered thread and an escalated thread each get a second mail."""
    first = [load_gmail("faq_en.json"), load_gmail("safeguarding_vi.json")]
    world.poll(first, settings=bot_settings(mode="auto"), sending=True)
    follow_up = mail(
        "syn-safeguarding-2",
        "Re: Lo ngại về một lớp học",
        "Cảm ơn, tôi có thêm một câu hỏi.",
        thread_id="18f2a1b4c5d6e878",
    )
    world.poll(
        [load_gmail("second_inbound.json"), follow_up],
        settings=bot_settings(mode="auto"),
        sending=True,
    )


def scenario_drafts_resolved(world: World) -> None:
    """Drafts sent unchanged, deleted, left alone, and a thread a person took over."""
    signups = [
        mail(
            "syn-a", "Teaching on weekends", "Weekends?", from_address="a@example.com"
        ),
        mail(
            "syn-b",
            "Teaching in the evening",
            "Evenings?",
            from_address="b@example.com",
        ),
        mail(
            "syn-c", "Teaching in the summer", "Summer?", from_address="c@example.com"
        ),
        mail(
            "syn-d",
            "Volunteering with Vietnam Hearts",
            "Sign me up",
            from_address="d@example.com",
        ),
    ]
    world.poll(signups, settings=bot_settings(mode="draft"))
    transport = world.transport
    assert transport is not None
    draft_a, draft_b, _draft_c, _draft_d = sorted(transport.drafts)
    sent_a = _mime(transport.drafts[draft_a])["body"]

    def resolve(next_transport: FakeTransport) -> None:
        # A sent unchanged by the captain, B deleted, C untouched; D's thread
        # gets a reply the captain typed and then a new mail from the sender.
        next_transport.drafts.pop(draft_a)
        next_transport.drafts.pop(draft_b)
        next_transport._threads["thread-syn-a"] = [
            RawMail.from_resource(
                payloads.message(
                    message_id="syn-a",
                    thread_id="thread-syn-a",
                    subject="Teaching on weekends",
                    from_address="a@example.com",
                    text="Weekends?",
                )
            ),
            RawMail.from_resource(
                payloads.message(
                    message_id="captain-sent-a",
                    thread_id="thread-syn-a",
                    subject="Re: Teaching on weekends",
                    from_address=f"Vietnam Hearts <{payloads.TEST_INBOX}>",
                    to_address="a@example.com",
                    text=sent_a,
                    label_ids=("SENT",),
                )
            ),
        ]
        next_transport._threads["thread-syn-d"] = [
            RawMail.from_resource(
                payloads.message(
                    message_id="syn-d",
                    thread_id="thread-syn-d",
                    subject="Volunteering with Vietnam Hearts",
                    from_address="d@example.com",
                    text="Sign me up",
                )
            ),
            RawMail.from_resource(
                payloads.message(
                    message_id="captain-typed-d",
                    thread_id="thread-syn-d",
                    subject="Re: Volunteering with Vietnam Hearts",
                    from_address=f"Vietnam Hearts <{payloads.TEST_INBOX}>",
                    to_address="d@example.com",
                    text="Hi, I will call you this afternoon.",
                    label_ids=("SENT",),
                )
            ),
            RawMail.from_resource(
                payloads.message(
                    message_id="syn-d-2",
                    thread_id="thread-syn-d",
                    subject="Re: Volunteering with Vietnam Hearts",
                    from_address="d@example.com",
                    text="Thanks, talk soon",
                )
            ),
        ]

    followup_d = payloads.message(
        message_id="syn-d-2",
        thread_id="thread-syn-d",
        subject="Re: Volunteering with Vietnam Hearts",
        from_address="d@example.com",
        text="Thanks, talk soon",
        rfc_message_id="<syn-d-2@mail.example.com>",
    )
    world.poll([followup_d], settings=bot_settings(mode="draft"), mutate=resolve)


def scenario_circuit_breaker(world: World) -> None:
    mails = [
        mail(
            f"syn-down-{index}",
            f"Classifier outage{suffix}",
            "Question?",
            from_address=f"p{index}@example.com",
        )
        for index, suffix in enumerate(["", " 2", " 3", " 4"])
    ]
    mails.insert(0, load_gmail("signup_en.json"))
    world.poll(mails, settings=bot_settings(mode="draft"))


def scenario_revoked_grant(world: World) -> None:
    set_setting(world.db, SETTING_MODE, "draft")
    world.poll(
        [load_gmail("signup_en.json")],
        settings=bot_settings(mode="draft"),
        revoked=True,
    )


def scenario_no_owner(world: World) -> None:
    world.poll(
        [load_gmail("signup_en.json")], settings=bot_settings(escalation_owner_email="")
    )


def scenario_dedupe(world: World) -> None:
    mails = [load_gmail("signup_en.json"), load_gmail("sponsorship_en.json")]
    world.poll(mails, settings=bot_settings(mode="draft"))
    world.poll(mails, settings=bot_settings(mode="draft"))


SCENARIOS: dict[str, Callable[[World], None]] = {
    "draft_corpus": scenario_draft_corpus,
    "auto_corpus": scenario_auto_corpus,
    "auto_daily_cap": scenario_auto_daily_cap,
    "off": scenario_off,
    "litellm_decides": scenario_litellm_decides,
    "no_form_link": scenario_no_form_link,
    "follow_ups": scenario_follow_ups,
    "drafts_resolved": scenario_drafts_resolved,
    "circuit_breaker": scenario_circuit_breaker,
    "revoked_grant": scenario_revoked_grant,
    "no_owner": scenario_no_owner,
    "dedupe": scenario_dedupe,
}


def _golden() -> dict[str, Any]:
    if not GOLDEN.exists():
        return {}
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _normalise(value: Any) -> Any:
    """JSON round trip, so tuples and lists compare the way the file stores them."""
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


@pytest.fixture(autouse=True)
def _fresh_http_log():
    FakeHttpClient.posts = []
    yield
    FakeHttpClient.posts = []


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_bot_behaves_as_recorded(name, test_db, monkeypatch):
    world = World(db=test_db, monkeypatch=monkeypatch)
    SCENARIOS[name](world)
    observed = _normalise(observe(world))

    if UPDATE:
        golden = _golden()
        golden[name] = observed
        GOLDEN.write_text(
            json.dumps(golden, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return

    expected = _golden().get(name)
    assert expected is not None, (
        f"no recording for {name}; run with UPDATE_CHARACTERISATION=1"
    )
    for key in expected:
        assert observed.get(key) == expected[key], f"{name}: {key} changed"
    assert sorted(observed) == sorted(expected)


def test_every_scenario_has_a_recording():
    """A scenario added without a recording would otherwise pass by recording itself."""
    if UPDATE:
        pytest.skip("recording")
    assert sorted(_golden()) == sorted(SCENARIOS)


def test_the_corpus_uses_only_reserved_example_addresses():
    """The fixture is public; nothing in it may be a real address."""
    import re

    text = GOLDEN.read_text(encoding="utf-8") if GOLDEN.exists() else ""
    for address in set(re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text)):
        domain = address.split("@", 1)[1]
        # Gmail's own message-id fallback is an id, not an address.
        assert domain.endswith(("example.com", ".example", "mail.gmail.com")), address
