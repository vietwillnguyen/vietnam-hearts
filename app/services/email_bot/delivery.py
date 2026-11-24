"""How a reply leaves the bot: inbox-core's ``Delivery`` over the Gmail sink.

The decision is the core's and is one pure table: ``off`` delivers nothing,
``draft`` holds every reply for review, and ``auto`` sends only a reply in a
language cleared by ``EMAIL_BOT_AUTO_LANGUAGES`` and within the daily and
per-sender caps. Every reason not to send holds the reply rather than dropping
it, and for this channel "held" is a Gmail draft in the thread, one click from
the captain.

What is ours is the wiring: the Gmail sink, which can only send when it is
built for ``auto``, and ``CapsState``, which counts today's sends from the
``messages`` rows.
"""

from __future__ import annotations

from collections.abc import Iterable

from inbox_core import Delivery, DeliveryMode, ReplySink
from inbox_core.delivery import SendCaps

from app.services.channels.gmail import GmailAdapter, GmailReplySink
from app.services.email_bot.caps import CapsState


def build_delivery(
    adapter: GmailAdapter,
    *,
    mode: DeliveryMode,
    auto_languages: Iterable[str],
    caps: SendCaps | None = None,
    sink: ReplySink | None = None,
) -> Delivery:
    """The delivery one run uses.

    The sink can send only in ``auto``, so a draft-mode run holds nothing that
    could reach a member of the public. ``off`` and ``draft`` never consult the
    caps, so they get an empty ``CapsState`` and the day's counts are read only
    when the mode could send.
    """
    auto = mode is DeliveryMode.AUTO
    return Delivery(
        sink or GmailReplySink(adapter, can_send=auto),
        mode=mode,
        auto_languages=frozenset(auto_languages),
        caps=caps if caps is not None else CapsState(),
    )
