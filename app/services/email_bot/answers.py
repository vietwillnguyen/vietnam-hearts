"""How the bot answers: the rules an emailed answer is written under, and the handlers.

Vietnam Hearts' own, and kept here rather than taken from inbox-core's
``render_answer_instructions``, because two of them have no counterpart there:
an answer must never say or imply that an application was accepted or
rejected, and must never state an amount of money. Both are things the captain
alone decides, and the inbox is where a stranger would most like to be told
them. Until the core lets a client add rules of its own, this prompt is the
one the generator is given.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from inbox_core import GroundedAnswer, Handler, HandlerRegistry, HandOff
from inbox_core.handlers import GenerationRequest
from inbox_core.knowledge import KnowledgeEntry, Passage
from inbox_core.prompts import REFUSAL_SENTINEL

from app.services.email_bot.bridge import run_coroutine
from app.services.email_bot.settings import EmailBotSettings

EMAIL_ANSWER_PROHIBITIONS = (
    "You are replying to an email sent to the volunteer inbox. Follow every "
    "one of these rules.\n"
    "- Use only the facts in the context above. If the context does not "
    f"answer the question, reply with exactly {REFUSAL_SENTINEL} and nothing "
    "else.\n"
    "- Never state a date, time, deadline, or commitment that is not in the "
    "context.\n"
    "- Never say or imply that an application has been accepted or rejected, "
    "and never comment on anyone's suitability.\n"
    "- Never state an amount of money, a fee, a salary, or a donation figure.\n"
    "- Never include anyone's name, email address, phone number, or any other "
    "personal detail about a third party.\n"
    "- Never promise when someone will reply or how long anything will take.\n"
    "- If the email asks several things and the context answers only some of "
    "them, answer those and say that a member of the team will follow up on "
    "the rest.\n"
    "- Write the reply in the same language the sender wrote in: "
    "{language_instruction}\n"
    "- Write as the Vietnam Hearts team, warmly and briefly, as plain text "
    "with no subject line and no signature."
)

LANGUAGE_NAMES = {
    "en": "English",
    "vi": "Vietnamese",
    "other": "English",
}


def language_instruction(language: str) -> str:
    return f"reply in {LANGUAGE_NAMES.get(language, 'English')}."


# ------------------------------------------------------------------ handlers
#
# The FAQ answer is inbox-core's GroundedAnswer: it retrieves, refuses when no
# passage clears ANSWER_THRESHOLD, generates from the passages that did, and
# refuses again on the sentinel. Vietnam Hearts supplies the two halves it
# plugs in - the vector search over the coordinators' Google Doc, and Gemini
# under the rules above - and nothing else.


# How many passages the FAQ answer is grounded in at most.
ANSWER_PASSAGES = 3


@dataclass
class AnswerOutages:
    """Whether answering failed for a reason a person did not choose.

    The core turns a broken retrieval or generation into a hand-off, which is
    right for the sender. The run also needs to know it was an outage rather
    than a question the knowledge base does not cover, because outages count
    toward the circuit breaker and a quiet knowledge gap does not.
    """

    last: str | None = None

    def record(self, exc: BaseException) -> None:
        self.last = type(exc).__name__

    def clear(self) -> None:
        self.last = None


class KnowledgeBaseRetriever:
    """The vector search over the knowledge base, as inbox-core's ``Retriever``.

    A chunk with no text is dropped rather than passed on: a passage that says
    nothing is no grounding, and an answer built on it would be invented from
    the question alone.
    """

    def __init__(self, knowledge_service: Any, *, outages: AnswerOutages) -> None:
        self._knowledge = knowledge_service
        self._outages = outages

    def search(self, query: str, *, limit: int) -> list[Passage]:
        try:
            chunks = run_coroutine(
                lambda: self._knowledge.similarity_search(query, limit=limit)
            )
        except Exception as exc:
            self._outages.record(exc)
            raise
        passages = []
        for chunk in chunks or []:
            text = (chunk.get("content") or "").strip()
            if not text:
                continue
            entry = KnowledgeEntry(
                id=str(chunk.get("source_document_id") or "knowledge-base"),
                title="knowledge base",
                text=text,
            )
            passages.append(Passage(entry, float(chunk.get("similarity") or 0.0)))
        return passages


class KnowledgeBaseGenerator:
    """Gemini under ``EMAIL_ANSWER_PROHIBITIONS``, as inbox-core's ``Generator``.

    It writes the prompt ``BotService`` has always written for the email
    channel, from the passages the core selected, and ignores the core's own
    instructions for the reason the module docstring gives.
    """

    def __init__(self, bot_service: Any, *, outages: AnswerOutages) -> None:
        self._bot = bot_service
        self._outages = outages

    @property
    def name(self) -> str:
        from app.services.knowledge_service import CHAT_MODEL

        return f"gemini:{CHAT_MODEL}"

    def generate(self, request: GenerationRequest) -> str:
        from app.services.channels.gmail import EMAIL_CHANNEL

        context = self._bot._build_context(
            [{"content": passage.entry.text} for passage in request.passages]
        )
        try:
            return run_coroutine(
                lambda: self._bot._generate_contextual_response(
                    request.question,
                    context,
                    channel=EMAIL_CHANNEL,
                    language=request.language,
                )
            )
        except Exception as exc:
            self._outages.record(exc)
            raise


def build_handlers(
    settings: EmailBotSettings, bot_service: Any, outages: AnswerOutages
) -> HandlerRegistry:
    """Which handler answers which auto-answerable category.

    ``signup`` is the captain's fixed copy and ``faq`` the grounded answer.
    Without an answer service the FAQ is handed to a person, and so is
    anything the table could ever let through without a handler.
    """
    from app.services.email_bot.replies import SignupFacts, SignupReply

    handlers: dict[str, Handler] = {
        "signup": SignupReply(
            SignupFacts(
                teaching_days=settings.teaching_days,
                class_start=settings.class_start_time,
                class_end=settings.class_end_time,
                signup_form_link=settings.signup_form_link,
            )
        )
    }
    if bot_service is not None:
        handlers["faq"] = GroundedAnswer(
            KnowledgeBaseRetriever(bot_service.knowledge_service, outages=outages),
            KnowledgeBaseGenerator(bot_service, outages=outages),
            min_score=settings.answer_threshold,
            limit=ANSWER_PASSAGES,
            name="faq",
        )
    return HandlerRegistry(
        handlers, default=HandOff(reason="no answer service is configured")
    )
