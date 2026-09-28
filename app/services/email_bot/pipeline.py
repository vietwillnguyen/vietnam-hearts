"""One poll of the volunteer inbox, start to finish.

Reads as the design's diagram does, and the order of the steps is the safety
model rather than a style choice:

- The deterministic guards run before any model and before any row is written,
  so a mailing list digest costs nothing and is never replied to.
- The dedupe check runs before the classifier, so a re-listed message costs one
  indexed lookup.
- The thread is read from Gmail before the bot acts, because a reply the captain
  typed by hand exists only there.
- The inbound audit row is committed before any side effect, so a run that dies
  mid-message cannot draft twice.
- The category gate runs before the confidence gate, so a low-confidence
  safeguarding guess still reaches the captain.
- ``VH-Bot/Seen`` is applied last, after the audit row is committed, so anything
  unfinished is simply picked up by the next run.

Synchronous, because the route is a plain ``def`` and everything it talks to is a
synchronous client. The single coroutine it needs, ``BotService.chat()``, is
reached through ``bridge.run_coroutine``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models import EmailBotRun
from app.services.bot_service import (
    GenerationUnavailable,
    NoRelevantContext,
)
from app.services.channels.base import IncomingMessage, OutboundReply
from app.services.channels.gmail_transport import (
    LABEL_DRAFTED,
    LABEL_ESCALATED,
    LABEL_PAUSED,
    LABEL_SEEN,
    LABEL_SENT,
    LABEL_SKIPPED,
    OUTCOME_LABELS,
    category_label,
)
from app.services.channels.mail_guards import (
    human_replied,
    is_automated,
    is_internal_sender,
)
from app.services.conversation_service import (
    ACTION_PAUSED,
    ACTION_SKIPPED,
    DRAFT_PENDING,
    ConversationService,
)
from app.services.email_bot.bridge import run_coroutine
from app.services.email_bot.caps import CapDecision
from app.services.email_bot.delivery import (
    DeliveryMode,
    DeliveryResult,
    ReplySink,
    choose_sink,
    parse_mode,
)
from app.services.email_bot.reconciliation import reconcile_draft
from app.services.email_bot.replies import (
    SignupFacts,
    SignupReplyUnavailable,
    render_holding_message,
    render_signup_reply,
    signature,
)
from app.services.email_bot.settings import (
    LISTING_WINDOW_DAYS,
    SETTING_LAST_ERROR,
    EmailBotSettings,
)
from app.services.email_bot.summaries import summarise_for_escalation
from app.services.knowledge_service import EmbeddingsUnavailable
from app.services.notifier import EscalationEvent, Notifier
from app.services.settings_service import set_setting
from app.services.triage.policy import CATEGORY_TIERS, escalation_reason
from app.services.triage.prompts import REFUSAL_SENTINEL
from app.services.triage.protocol import (
    TriageDecision,
    TriageSignals,
    TriageUnavailable,
)
from app.utils.logging_config import get_logger

logger = get_logger("email_bot_pipeline")

# Three in a row, not three in total: a single transient failure among twenty
# healthy messages is normal, while three consecutive ones is an outage, and a
# systemic outage must not turn into twenty forwards.
CIRCUIT_BREAKER_THRESHOLD = 3

# A run that has been "in flight" for longer than this is dead - a Cloud Run
# instance was recycled mid-poll - and must not block every later run forever.
STALE_RUN_AFTER = timedelta(minutes=30)

ALREADY_RUNNING = "already_running"

CLASSIFIER_UNAVAILABLE = "unavailable"


class UnrecordedSendError(Exception):
    """A reply went out but its audit row could not be written."""


@dataclass
class RunSummary:
    """What one poll did. Mirrors the ``email_bot_runs`` row."""

    mode: DeliveryMode = DeliveryMode.OFF
    listed: int = 0
    processed: int = 0
    drafted: int = 0
    sent: int = 0
    forwarded: int = 0
    skipped: int = 0
    reconciled: int = 0
    capped: int = 0
    errors: int = 0
    aborted_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": str(self.mode),
            "listed": self.listed,
            "processed": self.processed,
            "drafted": self.drafted,
            "sent": self.sent,
            "forwarded": self.forwarded,
            "skipped": self.skipped,
            "reconciled": self.reconciled,
            "capped": self.capped,
            "errors": self.errors,
            "aborted_reason": self.aborted_reason,
        }


class _CircuitOpen(RuntimeError):
    """Internal signal that the run must stop now, not that a message failed."""


class EmailBotPipeline:
    """The per-run orchestration. Construct it per poll, never reuse it."""

    def __init__(
        self,
        *,
        db: Session,
        adapter: Any,
        classifier: Any,
        notifier: Notifier,
        settings: EmailBotSettings,
        bot_service: Any = None,
        gemini_client: Any = None,
        admin_emails: tuple[str, ...] = (),
        sinks: dict[str, ReplySink] | None = None,
        caps: Any = None,
    ) -> None:
        self.db = db
        self.adapter = adapter
        self.classifier = classifier
        self.notifier = notifier
        self.settings = settings
        self.bot_service = bot_service
        self.gemini_client = gemini_client
        self.admin_emails = admin_emails
        self.mode = parse_mode(settings.mode)
        self.conversations = ConversationService(db)

        self.caps = caps
        if sinks is None:
            from app.services.email_bot.delivery import DraftSink, SendSink

            sinks = {"draft": DraftSink(adapter)}
            if self.mode is DeliveryMode.AUTO:
                # Only constructed when the mode could actually use it, so a
                # draft-mode run holds no object capable of sending at all.
                sinks["send"] = SendSink(adapter, self._caps())
        self.sinks = sinks

        self._summary = RunSummary(mode=self.mode)
        self._consecutive_failures = 0
        self._failed_this_message = False
        self._inbound_row: Any = None

    def _caps(self):
        """Today's send counts, read once and shared by every send this run.

        "Today" is the organization's local day rather than UTC. Cloud Run sets
        no timezone, so a naive clock would roll the daily cap over at 07:00
        Vietnam time, in the middle of the morning poll - the same class of bug
        the schedule week anchor exists to avoid.
        """
        if self.caps is None:
            from app.services.email_bot.caps import CapsState
            from app.utils.schedule_dates import local_now

            self.caps = CapsState.load(
                self.db, local_now(self.settings.timezone), self.settings
            )
        return self.caps

    # ------------------------------------------------------------------ run

    def run(self) -> RunSummary:
        """Poll once. Never raises for a per-message problem.

        A message that fails is counted, left unlabelled for the next run, and
        does not stop the nineteen behind it. Only the circuit breaker and a
        pre-flight refusal stop a run early, and both record why.
        """
        if self.mode is DeliveryMode.OFF:
            self._summary.aborted_reason = "mode is off"
            return self._summary

        if not self.settings.escalation_owner_email:
            # Without a forward recipient an escalation has nowhere to go, and
            # the design makes the bot inert rather than let it draft replies
            # while silently dropping the handoffs.
            self._summary.aborted_reason = "ESCALATION_OWNER_EMAIL is not set"
            logger.error("Refusing to poll: ESCALATION_OWNER_EMAIL is not set")
            return self._summary

        inbox_refusal = self._inbox_address_refusal()
        if inbox_refusal is not None:
            self._summary.aborted_reason = inbox_refusal
            self._record_last_error(inbox_refusal)
            logger.error("Refusing to poll: %s", inbox_refusal)
            return self._summary

        run_row = self._acquire_run()
        if run_row is None:
            self._summary.aborted_reason = ALREADY_RUNNING
            return self._summary

        try:
            self._poll()
        except _CircuitOpen as stop:
            self._summary.aborted_reason = str(stop)
            self._record_last_error(str(stop))
            logger.error("Run aborted by the circuit breaker: %s", stop)
        except Exception as exc:
            # The run failed as a whole (listing, labels, the database). Loud,
            # and with no partial state to reason about: unlabelled mail waits
            # for the next run.
            self._summary.errors += 1
            self._summary.aborted_reason = f"run failed: {type(exc).__name__}"
            self._record_last_error(self._summary.aborted_reason)
            logger.error("Inbox poll failed: %s", type(exc).__name__, exc_info=True)
        else:
            self._clear_last_error()
        finally:
            self._finish_run(run_row)

        return self._summary

    def _inbox_address_refusal(self) -> str | None:
        """Why this run must not start, if the inbox address is unusable.

        Resolved once, before any mail is touched, because every "is this us?"
        decision below depends on it and none of them has a safe answer without
        it. ``human_replied`` with no inbox address is the case in point:
        answering False lets the bot reply over a human, and answering True
        pauses every thread and - since the inbound row is committed before the
        pause - permanently drops mail the next run dedupes as done. There is
        no correct value, so the guard must never be reached with an empty
        address.

        Recorded as ``EMAIL_BOT_LAST_ERROR`` as well as aborted, unlike the
        other pre-flight refusals: an unresolvable address means the Gmail
        grant itself is broken, which is exactly what the dashboard banner is
        for, whereas an unset setting is visible on the settings page already.
        """
        try:
            address = self.adapter.inbox_address
        except Exception as exc:
            return f"the Gmail grant could not be read: {type(exc).__name__}"
        if not address:
            return "the Gmail grant reports no inbox address"
        return None

    def _poll(self) -> None:
        self.adapter.ensure_labels(self._label_names())

        # Before listing anything new. This is how the draft-acceptance metric
        # is measured, and that metric is what decides whether automatic
        # sending is ever turned on, so it must not be the thing that gets
        # skipped when a run is busy.
        self._reconcile_drafts()

        mails = self.adapter.list_new(
            limit=self.settings.per_run_cap, newer_than_days=LISTING_WINDOW_DAYS
        )
        self._summary.listed = len(mails)

        for raw in mails[: self.settings.per_run_cap]:
            self._process(raw)

    def _reconcile_drafts(self) -> None:
        """Decide what became of every draft still waiting on an answer.

        Answered from what Gmail shows now rather than from anything the bot
        was told: whether the captain sent a draft unchanged is only knowable
        by looking at the thread afterwards.

        Per-draft failures are counted and skipped rather than raised. A draft
        whose thread cannot be read stays ``pending`` and is asked about again
        next run, which is the right outcome - an unknown outcome is not a
        deleted one, and guessing would corrupt the metric the E2 gate reads.
        """
        for row in self.conversations.pending_drafts():
            try:
                self._reconcile_one(row)
            except Exception as exc:
                self._summary.errors += 1
                logger.warning(
                    "Could not reconcile draft %s: %s",
                    row.gmail_draft_id,
                    type(exc).__name__,
                )

    def _reconcile_one(self, row: Any) -> None:
        self.conversations.mark_draft_checked(row)
        still_there = self.adapter.get_draft(row.gmail_draft_id) is not None
        conversation = row.conversation

        result = reconcile_draft(
            draft_still_exists=still_there,
            drafted_text=row.text or "",
            thread_messages=self.adapter.get_thread(row.gmail_thread_id),
            inbox_address=self.adapter.inbox_address,
            bot_message_ids=self.conversations.bot_sent_message_ids(conversation),
        )

        if result.outcome == DRAFT_PENDING:
            return

        self.conversations.set_draft_outcome(
            row, result.outcome, result.sent_message_id
        )
        self._summary.reconciled += 1
        logger.info("Draft %s resolved as %s", row.gmail_draft_id, result.outcome)

    # -------------------------------------------------------------- per mail

    def _process(self, raw: Any) -> None:
        try:
            message = self.adapter.parse(raw)
        except Exception as exc:
            self._note_failure(f"parse failed: {type(exc).__name__}")
            return

        self._summary.processed += 1
        self._failed_this_message = False
        self._inbound_row = None

        try:
            self._handle(message)
        except _CircuitOpen:
            raise
        except Exception as exc:
            self._note_failure(
                f"{message.provider_message_id} failed: {type(exc).__name__}"
            )
            logger.error(
                "Message %s failed: %s",
                message.provider_message_id,
                type(exc).__name__,
                exc_info=True,
            )
            return

        # Only a message that made it through with no infrastructure failure at
        # all resets the streak. A classifier outage that was handled by failing
        # closed to needs_admin still *is* an infrastructure failure: without
        # this, every message would succeed at escalating, the counter would
        # reset each time, and a total outage would quietly produce twenty
        # forwards instead of tripping the breaker.
        if not self._failed_this_message:
            self._consecutive_failures = 0

    def _handle(self, message: IncomingMessage) -> None:
        if self.conversations.is_handled(message.provider_message_id):
            # Finished with on an earlier run. Re-apply the marker label so a
            # run that died before labelling converges, and do nothing else.
            #
            # Deliberately ``is_handled`` and not ``is_duplicate``: a row
            # exists from the moment the mail is triaged, before anything has
            # been answered or forwarded, so skipping on the row alone is how
            # a mail gets written off without a person ever seeing it.
            self._label(message, [LABEL_SEEN])
            return

        guard = self._guard_reason(message)
        if guard:
            conversation = self._conversation(message)
            row = self.conversations.record_action(
                conversation, message, ACTION_SKIPPED
            )
            self._summary.skipped += 1
            logger.info("Skipped %s by guard %s", message.provider_message_id, guard)
            self.conversations.mark_handled(row)
            self._label(message, [LABEL_SKIPPED, LABEL_SEEN])
            return

        conversation = self._conversation(message)

        if self._human_has_replied(conversation, message):
            self._take_over(conversation, message)
            return

        decision, shadow = self._triage(message)

        # The one-reply-per-thread cap, applied before the audit row so the row
        # records what actually happened. A second inbound on a thread the bot
        # has already answered is needs_admin however answerable it looks: it is
        # forwarded and posted like any escalation, and the sender gets nothing
        # further. Chasing an unanswered question twice is a reason to involve a
        # person, not to send the same answer again.
        reason_override: str | None = None
        if decision.tier == "auto_answer" and not self.conversations.can_bot_reply(
            conversation
        ):
            decision = decision.with_tier("needs_admin")
            reason_override = "the bot has already replied in this thread"

        # Committed before any side effect, so a crash cannot draft twice. It
        # carries no ``handled_at`` yet, so a crash also cannot make this mail
        # look dealt with: every terminal branch below marks it, and anything
        # that raises leaves it for the next run.
        self._inbound_row = self.conversations.record_inbound(
            conversation, message, decision, shadow
        )

        if decision.tier == "skip":
            self._summary.skipped += 1
            self._finish(message)
            self._label(
                message,
                [LABEL_SKIPPED, category_label(decision.category), LABEL_SEEN],
            )
            return

        if decision.tier == "auto_answer":
            self._answer(conversation, message, decision)
            return

        self._escalate(conversation, message, decision, reason_override)

    def _guard_reason(self, message: IncomingMessage) -> str | None:
        """Why this mail never reaches a model, if it does not.

        Both rules are here rather than in the classifier because both are
        answerable from headers alone, and a mail from the escalation owner or
        an admin address must never become a conversation with a reply waiting
        in it - it is our own mail coming back.
        """
        if not message.sender_address:
            # An unparseable or absent From. There is nobody to reply to, so
            # this is labelled and dropped rather than triaged: a draft with an
            # empty To is worse than no draft, and mail like this is machine
            # noise in practice.
            return "unparseable-sender"

        automated = is_automated(message.headers, message.label_ids)
        if automated:
            return automated
        if is_internal_sender(
            message.sender_address,
            self.adapter.inbox_address,
            self.settings.escalation_owner_email,
            self.admin_emails,
        ):
            return "internal-sender"
        return None

    def _human_has_replied(self, conversation: Any, message: IncomingMessage) -> bool:
        thread = self.adapter.get_thread(message.thread_key)
        return human_replied(
            thread,
            self.adapter.inbox_address,
            self.conversations.bot_sent_message_ids(conversation),
        )

    def _take_over(self, conversation: Any, message: IncomingMessage) -> None:
        """A human owns this thread now: label it, and take the bot's draft away.

        Deleting the outstanding draft is the part that matters. A stale draft
        under a conversation the captain has taken over is an answer that is no
        longer true, one click away from being sent.
        """
        outstanding = self.conversations.outstanding_draft(conversation)
        if outstanding and outstanding.gmail_draft_id:
            try:
                self.adapter.delete_draft(outstanding.gmail_draft_id)
            except Exception as exc:
                logger.warning(
                    "Could not delete draft %s: %s",
                    outstanding.gmail_draft_id,
                    type(exc).__name__,
                )
            else:
                outstanding.draft_outcome = "deleted"
                self.db.commit()

        self.conversations.pause_manual(conversation)
        row = self.conversations.record_action(conversation, message, ACTION_PAUSED)
        self.conversations.mark_handled(row)
        self._label(message, [LABEL_PAUSED, LABEL_SEEN])

    # --------------------------------------------------------------- triage

    def _triage(
        self, message: IncomingMessage
    ) -> tuple[TriageDecision, TriageSignals | None]:
        """Classify, or fail closed to ``needs_admin``.

        A classifier outage is an infrastructure failure: it counts toward the
        circuit breaker and it produces a decision that sends the mail to a
        person. It never produces a guess, because a guessed category is how a
        safeguarding mail ends up answered from the FAQ.
        """
        from app.services.triage.policy import decide

        try:
            signals = self.classifier.classify(message)
        except TriageUnavailable as exc:
            self._note_failure(f"classifier unavailable: {exc}")
            return self._fail_closed_decision(), None

        shadow = getattr(self.classifier, "last_shadow", None)
        return decide(signals, self.settings.triage_confidence_threshold), shadow

    def _fail_closed_decision(self) -> TriageDecision:
        return TriageDecision(
            category="human_other",
            language="en",
            confidence=0.0,
            asks_for_human=False,
            mentions_money_or_commitment=False,
            classifier=CLASSIFIER_UNAVAILABLE,
            tier="needs_admin",
        )

    # --------------------------------------------------------------- answer

    def _answer(
        self, conversation: Any, message: IncomingMessage, decision: TriageDecision
    ) -> None:
        if decision.category == "signup":
            try:
                text = render_signup_reply(decision.language, self._signup_facts())
            except SignupReplyUnavailable as exc:
                # The facts are not configured, so there is no honest template
                # to send. A person answers it instead.
                logger.error("Sign-up reply unavailable: %s", exc)
                self._escalate(
                    conversation,
                    message,
                    decision.with_tier("needs_admin"),
                    reason_override="the sign-up template is not configured",
                )
                return
            self._reply_or_escalate(
                conversation, message, decision, text=text, kind="signup"
            )
            return

        self._answer_faq(conversation, message, decision)

    def _answer_faq(
        self, conversation: Any, message: IncomingMessage, decision: TriageDecision
    ) -> None:
        """Retrieve, generate, then gate on the retrieval similarity.

        Three distinct ways this declines to answer, all ending in the same
        place: no relevant context, a refusal sentinel in the generated text, or
        a top similarity below ``ANSWER_THRESHOLD``. Everything else - a
        retrieval or generation outage - is an infrastructure failure that also
        escalates but additionally counts toward the circuit breaker.
        """
        if self.bot_service is None:
            self._escalate(
                conversation,
                message,
                decision.with_tier("needs_admin"),
                reason_override="no answer service is configured",
            )
            return

        try:
            answer = run_coroutine(
                lambda: self.bot_service.chat(
                    message.text,
                    channel="email",
                    language=decision.language,
                )
            )
        except NoRelevantContext:
            self._escalate(
                conversation,
                message,
                decision.with_tier("needs_admin"),
                reason_override="the knowledge base does not cover this question",
            )
            return
        except (EmbeddingsUnavailable, GenerationUnavailable) as exc:
            self._escalate(
                conversation,
                message,
                decision.with_tier("needs_admin"),
                reason_override="the answer service is unavailable",
            )
            self._note_failure(f"answering unavailable: {type(exc).__name__}")
            return

        confidence = float(answer.get("confidence") or 0.0)
        text = (answer.get("response") or "").strip()

        if REFUSAL_SENTINEL in text:
            self._escalate(
                conversation,
                message,
                decision.with_tier("needs_admin"),
                reason_override="the generator reported insufficient context",
            )
            return

        if confidence < self.settings.answer_threshold:
            self._escalate(
                conversation,
                message,
                decision.with_tier("needs_admin"),
                reason_override=(
                    f"retrieval similarity {confidence:.2f} below "
                    f"{self.settings.answer_threshold:.2f}"
                ),
            )
            return

        self._reply_or_escalate(
            conversation,
            message,
            decision,
            text=text,
            kind="faq",
            confidence=confidence,
            sources=[source for source in answer.get("sources") or [] if source],
        )

    # ------------------------------------------------------------- escalate

    def _escalate(
        self,
        conversation: Any,
        message: IncomingMessage,
        decision: TriageDecision,
        reason_override: str | None = None,
        offer_holding: bool = True,
    ) -> None:
        """Forward, post, pause, label - and give the sender the holding message.

        The first four happen in every mode and whether or not the sender gets
        anything, because knowing about a safeguarding mail is not something a
        delivery switch should be able to turn off. Only the fifth is gated: a
        thread that already has its one bot reply gets no second one.

        The forward goes out before the holding message is attempted, and a
        holding message that cannot be drafted does not stop the rest. The
        inbound row is already committed, so anything that raised here would
        leave a mail the next run dedupes as done without a person ever
        having seen it.
        """
        reason = reason_override or escalation_reason(
            decision, self.settings.triage_confidence_threshold
        )

        summary = summarise_for_escalation(
            message.text, decision.language, self.gemini_client
        )
        self.notifier.notify(
            EscalationEvent(
                message=message,
                decision=decision,
                reason=reason,
                summary=summary,
                gmail_thread_url=self.adapter.thread_url(message.thread_key),
            )
        )
        self._summary.forwarded += 1

        delivered: DeliveryResult | None = None
        holding_error: Exception | None = None
        if offer_holding and self.conversations.can_bot_reply(conversation):
            try:
                delivered = self._deliver_reply(
                    conversation,
                    message,
                    decision,
                    text=render_holding_message(decision.language),
                    kind="holding",
                    label_after=False,
                )
            except Exception as exc:
                holding_error = exc
                logger.error(
                    "Holding message for %s failed: %s",
                    message.provider_message_id,
                    type(exc).__name__,
                )

        self.conversations.pause(conversation, decision.tier, reason)

        # Terminal: a person has been told. Marked after the notifier rather
        # than before, so a forward that never went out leaves the mail for the
        # next run instead of writing it off.
        self._finish(message)

        labels = [LABEL_ESCALATED, category_label(decision.category)]
        if delivered is not None:
            labels.append(LABEL_SENT if delivered.action == "sent" else LABEL_DRAFTED)
        labels.append(LABEL_SEEN)
        self._label(message, labels)

        if holding_error is not None:
            self._note_failure(
                f"holding message failed: {type(holding_error).__name__}"
            )

    def _reply_or_escalate(
        self,
        conversation: Any,
        message: IncomingMessage,
        decision: TriageDecision,
        **reply: Any,
    ) -> None:
        """Deliver an answer, or hand the mail to a person if delivery fails.

        By now the inbound row is committed, so a delivery error that simply
        propagated would leave the mail deduped as done on the next run with no
        reply and nobody told. Escalating instead means a person answers it.
        """
        try:
            self._deliver_reply(conversation, message, decision, **reply)
        except Exception as exc:
            logger.error(
                "Reply to %s could not be delivered: %s",
                message.provider_message_id,
                type(exc).__name__,
            )
            self._escalate(
                conversation,
                message,
                decision.with_tier("needs_admin"),
                reason_override=(
                    "the reply was sent but could not be recorded"
                    if isinstance(exc, UnrecordedSendError)
                    else "the reply could not be delivered"
                ),
                offer_holding=False,
            )
            self._note_failure(f"reply delivery failed: {type(exc).__name__}")

    # ------------------------------------------------------------- delivery

    def _deliver_reply(
        self,
        conversation: Any,
        message: IncomingMessage,
        decision: TriageDecision,
        *,
        text: str,
        kind: str,
        confidence: float | None = None,
        sources: list[str] | None = None,
        label_after: bool = True,
    ) -> DeliveryResult | None:
        sink = choose_sink(
            self.mode,
            decision.language,
            self.settings.auto_languages,
            self.sinks,
            message.sender_key,
            on_capped=self._count_capped,
        )
        if sink is None:
            return None

        # Signed here, not in the transport: which line signs off a
        # Vietnamese reply is copy, and the stored text has to be exactly what
        # was offered so the draft-acceptance comparison in E2 is meaningful.
        signed = f"{text.rstrip()}\n\n{signature(decision.language)}"

        reply = OutboundReply(
            thread_key=message.thread_key,
            to_address=message.sender_address,
            subject=message.subject,
            in_reply_to=message.rfc_message_id,
            references=message.references,
            text=signed,
            language=decision.language,
            kind=kind,
        )
        result = sink.deliver(reply)

        try:
            self.conversations.record_outbound(
                conversation,
                text=signed,
                action=result.action,
                language=decision.language,
                gmail_message_id_out=result.gmail_message_id_out,
                gmail_draft_id=result.gmail_draft_id,
                confidence=confidence,
                sources=sources,
                kind=kind,
            )
        except Exception as exc:
            # The draft already exists in Gmail and only its audit row is
            # missing, which is the worst of both: ``outstanding_draft`` reads
            # the rows, so nothing can ever find that draft again, and
            # ``_take_over`` can never delete it. It sits in the thread one
            # click from being sent, with no record that the bot wrote it.
            #
            # Removing it is therefore the safe direction. The rollback matters
            # as much: without it the caller's fallback escalation hits a
            # session that still needs one, and its own ``pause()`` commit
            # raises too, which would re-open the lost-mail path this guards.
            self._discard_undone_delivery(result)
            if result.action == "sent":
                self._summary.sent += 1
                raise UnrecordedSendError(
                    f"reply to {message.provider_message_id} was sent but not "
                    f"recorded: {type(exc).__name__}"
                ) from exc
            raise

        if result.action == "sent":
            self._summary.sent += 1
        else:
            self._summary.drafted += 1

        if label_after:
            # Terminal only for the answer paths. A holding message inside an
            # escalation passes label_after=False, and that escalation marks
            # the mail itself once the forward has gone out.
            self._finish(message)
            outcome = LABEL_SENT if result.action == "sent" else LABEL_DRAFTED
            self._label(
                message, [outcome, category_label(decision.category), LABEL_SEEN]
            )

        return result

    def _count_capped(self, _decision: CapDecision) -> None:
        # Drafted while the mode says send because a cap was reached. Counted
        # separately so the dashboard does not read it as a quiet day.
        self._summary.capped += 1

    def _discard_undone_delivery(self, result: DeliveryResult) -> None:
        """Roll the session back and remove a draft that was never recorded.

        Best-effort on the delete, because the alternative to a failed cleanup
        is raising over the original error and losing it. A draft that survives
        this is at least logged with its id.
        """
        try:
            self.db.rollback()
        except Exception:
            logger.error(
                "Could not roll back after a failed audit write", exc_info=True
            )

        if not result.gmail_draft_id:
            # Nothing to clean up. A send cannot be recalled, and E3's caps
            # count sent rows, so a send whose row was lost is reported by the
            # run counters being ahead of the audit table rather than silently.
            return

        try:
            self.adapter.delete_draft(result.gmail_draft_id)
        except Exception:
            logger.error(
                "Orphaned draft %s could not be deleted after a failed audit "
                "write; it is still in the thread with no row",
                result.gmail_draft_id,
            )

    # ---------------------------------------------------------------- state

    def _conversation(self, message: IncomingMessage):
        return self.conversations.get_or_create(
            message.channel, message.thread_key, message.sender_key
        )

    def _signup_facts(self) -> SignupFacts:
        return SignupFacts(
            teaching_days=self.settings.teaching_days,
            class_start=self.settings.class_start_time,
            class_end=self.settings.class_end_time,
            signup_form_link=self.settings.signup_form_link,
        )

    def _finish(self, message: IncomingMessage) -> None:
        """Mark this mail handled, so the next run does not pick it up again.

        Best-effort: by the time this is called the forward, draft or skip has
        already happened, so failing the message instead would be the wrong
        trade. A failure leaves ``handled_at`` NULL on a mail that was in fact
        handled; the marker label applied next keeps it out of later listings,
        so it is not retried.
        """
        row = getattr(self, "_inbound_row", None)
        if row is None:
            return
        try:
            self.conversations.mark_handled(row)
        except Exception:
            logger.error(
                "Could not mark %s handled; its action already happened",
                message.provider_message_id,
                exc_info=True,
            )

    def _label(self, message: IncomingMessage, names: list[str]) -> None:
        """Apply outcome labels, with the marker label last.

        Best-effort on purpose: labels are the human-visible mirror of the audit
        rows, never the source of truth, so a labelling failure must not undo
        work that is already committed. It simply means the next run sees the
        message again and the dedupe check makes that a no-op.
        """
        try:
            self.adapter.label(message.provider_message_id, names)
        except Exception as exc:
            logger.warning(
                "Could not label %s: %s",
                message.provider_message_id,
                type(exc).__name__,
            )

    def _label_names(self) -> list[str]:
        return [*OUTCOME_LABELS, *(category_label(name) for name in CATEGORY_TIERS)]

    def _note_failure(self, reason: str) -> None:
        self._summary.errors += 1
        self._consecutive_failures += 1
        self._failed_this_message = True
        if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            raise _CircuitOpen(
                f"{self._consecutive_failures} consecutive infrastructure "
                f"failures; last was {reason}"
            )

    # ------------------------------------------------------------- run rows

    def _acquire_run(self) -> EmailBotRun | None:
        """Claim the run, or decline because one is already in flight.

        A scheduler retry arriving while the previous attempt is still running
        must do nothing rather than double every draft. A row older than
        ``STALE_RUN_AFTER`` with no ``finished_at`` is a recycled instance, not
        a live run, and is stepped over.
        """
        in_flight = (
            self.db.query(EmailBotRun)
            .filter(EmailBotRun.finished_at.is_(None))
            .order_by(EmailBotRun.started_at.desc())
            .first()
        )
        if in_flight is not None:
            started = in_flight.started_at
            if started is not None and started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if started is not None and datetime.now(UTC) - started < STALE_RUN_AFTER:
                logger.info("A run started at %s is still in flight", started)
                return None
            in_flight.finished_at = datetime.now(UTC)
            in_flight.aborted_reason = "abandoned; superseded by a later run"
            self.db.commit()

        run_row = EmailBotRun(started_at=datetime.now(UTC), mode=str(self.mode))
        self.db.add(run_row)
        self.db.commit()
        self.db.refresh(run_row)
        return run_row

    def _finish_run(self, run_row: EmailBotRun) -> None:
        run_row.finished_at = datetime.now(UTC)
        run_row.listed = self._summary.listed
        run_row.processed = self._summary.processed
        run_row.drafted = self._summary.drafted
        run_row.sent = self._summary.sent
        run_row.forwarded = self._summary.forwarded
        run_row.skipped = self._summary.skipped
        run_row.reconciled = self._summary.reconciled
        run_row.capped = self._summary.capped
        run_row.errors = self._summary.errors
        run_row.aborted_reason = self._summary.aborted_reason
        self.db.commit()

    def _record_last_error(self, reason: str) -> None:
        try:
            set_setting(self.db, SETTING_LAST_ERROR, reason)
        except Exception:
            logger.warning("Could not record EMAIL_BOT_LAST_ERROR", exc_info=True)

    def _clear_last_error(self) -> None:
        try:
            set_setting(self.db, SETTING_LAST_ERROR, "")
        except Exception:
            logger.warning("Could not clear EMAIL_BOT_LAST_ERROR", exc_info=True)
