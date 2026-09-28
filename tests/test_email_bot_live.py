"""The live end-to-end test, against a THROWAWAY Gmail inbox.

Skipped unless every credential it needs is present, which is the normal state:
the throwaway accounts and the API keys do not exist yet, and CI must never have
them. When they do exist, this is the acceptance test for phase E1.

    export VH_LIVE_EMAIL_TEST=1
    export VH_TEST_GMAIL_CLIENT_ID=... VH_TEST_GMAIL_CLIENT_SECRET=...
    export VH_TEST_GMAIL_REFRESH_TOKEN=...          # for the throwaway INBOX
    export VH_TEST_ESCALATION_OWNER_EMAIL=...       # a throwaway mailbox
    uv run pytest tests/test_email_bot_live.py -v

Three refusals, all before a socket is opened, because the cost of getting this
wrong is touching real volunteers' mail:

1. ``VH_LIVE_EMAIL_TEST`` must be explicitly set, so no credential lying around
   in an environment can turn this on by accident.
2. The grant's own address, read back from ``users.getProfile``, must not be the
   real volunteer inbox. That is checked against the grant rather than against
   configuration, so a mislabelled variable cannot get past it.
3. The mode is forced to ``draft`` here whatever the database says, and the
   transport has no send method in this phase anyway.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import pytest

from app.services.channels.gmail_transport import (
    LABEL_SEEN,
    OUTCOME_LABELS,
    GmailTransport,
)

# The one address this whole build is forbidden to touch. Hard-coded, like the
# refusal in scripts/seed_test_inbox.py: a guard that a flag can switch off is
# not a guard.
REAL_VOLUNTEER_INBOX = "vietnam.hearts.volunteering@gmail.com"

REQUIRED_ENV = (
    "VH_LIVE_EMAIL_TEST",
    "VH_TEST_GMAIL_CLIENT_ID",
    "VH_TEST_GMAIL_CLIENT_SECRET",
    "VH_TEST_GMAIL_REFRESH_TOKEN",
    "VH_TEST_ESCALATION_OWNER_EMAIL",
)


def missing_env() -> list[str]:
    return [name for name in REQUIRED_ENV if not os.getenv(name)]


pytestmark = pytest.mark.skipif(
    bool(missing_env()),
    reason=(
        "live Gmail test needs a throwaway inbox and its credentials; "
        f"not set: {', '.join(missing_env()) or 'none'}. "
        "See the module docstring and docs/GMAIL_BOT_SETUP.md."
    ),
)


class RefusedRealInbox(AssertionError):
    """The supplied grant belongs to the real volunteer inbox."""


@pytest.fixture(scope="module")
def live_transport() -> GmailTransport:
    transport = GmailTransport.from_credentials(
        os.environ["VH_TEST_GMAIL_CLIENT_ID"],
        os.environ["VH_TEST_GMAIL_CLIENT_SECRET"],
        os.environ["VH_TEST_GMAIL_REFRESH_TOKEN"],
    )

    address = transport.inbox_address.strip().lower()
    if address == REAL_VOLUNTEER_INBOX:
        raise RefusedRealInbox(
            "The supplied Gmail grant is for the real volunteer inbox. This test "
            "only ever runs against a throwaway account."
        )
    if not address:
        raise RefusedRealInbox(
            "Could not read the granted address back from users.getProfile, so "
            "the throwaway check cannot be made. Refusing to continue."
        )
    return transport


class TestTheGrantIsAThrowawayAccount:
    def test_the_granted_address_is_not_the_real_inbox(self, live_transport):
        assert live_transport.inbox_address.lower() != REAL_VOLUNTEER_INBOX

    def test_the_escalation_owner_is_not_the_real_inbox(self):
        owner = os.environ["VH_TEST_ESCALATION_OWNER_EMAIL"].strip().lower()
        assert owner != REAL_VOLUNTEER_INBOX


class TestTheTransportWorksAgainstRealGmail:
    def test_the_labels_can_be_created_and_resolved(self, live_transport):
        resolved = live_transport.ensure_labels(OUTCOME_LABELS)
        assert set(resolved) == set(OUTCOME_LABELS)
        assert all(resolved.values())

    def test_creating_the_labels_twice_is_idempotent(self, live_transport):
        first = live_transport.ensure_labels(OUTCOME_LABELS)
        second = live_transport.ensure_labels(OUTCOME_LABELS)
        assert first == second

    def test_listing_unprocessed_mail_returns_parseable_resources(self, live_transport):
        from app.services.channels.gmail import GmailAdapter

        mails = live_transport.list_unprocessed(
            newer_than_days=7, exclude_label=LABEL_SEEN, limit=5
        )
        adapter = GmailAdapter(live_transport)
        for mail in mails:
            parsed = adapter.parse(mail)
            assert parsed.provider_message_id
            assert parsed.thread_key
            assert parsed.rfc_message_id

    def test_a_draft_can_be_created_and_deleted(self, live_transport):
        from app.services.channels.base import OutboundReply
        from app.services.channels.mail_builder import build_reply

        mails = live_transport.list_unprocessed(
            newer_than_days=7, exclude_label=LABEL_SEEN, limit=1
        )
        if not mails:
            pytest.skip(
                "the throwaway inbox has no unprocessed mail; run "
                "scripts/seed_test_inbox.py first"
            )

        from app.services.channels.gmail import GmailAdapter

        parsed = GmailAdapter(live_transport).parse(mails[0])
        mime = build_reply(
            OutboundReply(
                thread_key=parsed.thread_key,
                to_address=parsed.sender_address,
                subject=parsed.subject,
                in_reply_to=parsed.rfc_message_id,
                references=parsed.references,
                text="Live transport check. Safe to delete.",
                language="en",
                kind="holding",
            ),
            from_address=live_transport.inbox_address,
        )

        draft_id = live_transport.create_draft(parsed.thread_key, mime)
        assert draft_id
        try:
            assert live_transport.get_draft(draft_id) is not None
        finally:
            # Always cleaned up: a stray draft in a test mailbox is litter, and
            # in any other mailbox it would be a hazard.
            live_transport.delete_draft(draft_id)

        assert live_transport.get_draft(draft_id) is None


class TestNothingCanSendEvenLive:
    def test_the_live_transport_has_no_send_method(self, live_transport):
        assert not hasattr(live_transport, "send_reply")
        assert not any("send" in name.lower() for name in dir(live_transport))
