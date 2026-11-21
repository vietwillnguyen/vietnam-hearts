"""Every deterministic guard rule in the design, table-driven.

These rules are the reason a vacation responder never gets a holding message
back, so each one gets its own row rather than being covered incidentally by a
pipeline test. A missing rule here is a two-machine mail loop in production.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import pytest

from app.services.channels.gmail_transport import RawMail
from app.services.channels.mail_guards import (
    BULK_GMAIL_LABELS,
    NO_REPLY_LOCAL_PARTS,
    human_replied,
    is_automated,
    is_internal_sender,
    normalise_address,
    sender_key,
)
from tests.fixtures import gmail_payloads as payloads
from tests.fixtures.email_bot import load_gmail, raw

HUMAN = {"from": "Someone <someone@example.com>", "subject": "Volunteering"}


class TestIsAutomatedHeaderRules:
    @pytest.mark.parametrize(
        "headers,expected_rule",
        [
            ({"auto-submitted": "auto-replied"}, "auto-submitted"),
            ({"auto-submitted": "auto-generated"}, "auto-submitted"),
            # RFC 3834 allows a parameter after the keyword.
            (
                {"auto-submitted": "auto-replied; owner-email=x@example.com"},
                "auto-submitted",
            ),
            ({"precedence": "bulk"}, "precedence"),
            ({"precedence": "list"}, "precedence"),
            ({"precedence": "junk"}, "precedence"),
            ({"list-id": "<a.lists.example.com>"}, "list-id"),
            ({"list-unsubscribe": "<https://example.com/u>"}, "list-unsubscribe"),
            ({"x-auto-response-suppress": "All"}, "x-auto-response-suppress"),
        ],
    )
    def test_each_rule_is_recognised_by_name(self, headers, expected_rule):
        assert is_automated({**HUMAN, **headers}) == expected_rule

    def test_auto_submitted_no_is_not_automated(self):
        # RFC 3834 says "no" explicitly means a human wrote it, so this header
        # must not be read as merely "present".
        assert is_automated({**HUMAN, "auto-submitted": "no"}) is None

    @pytest.mark.parametrize(
        "casing", ["Auto-Submitted", "AUTO-SUBMITTED", "auto-submitted"]
    )
    def test_header_casing_does_not_matter(self, casing):
        assert is_automated({**HUMAN, casing: "auto-replied"}) == "auto-submitted"

    def test_an_ordinary_mail_is_not_automated(self):
        assert is_automated(HUMAN, ("INBOX", "UNREAD")) is None

    def test_empty_header_values_do_not_trigger_a_rule(self):
        # A client that emits "Precedence:" with nothing after it is not
        # declaring itself bulk.
        assert is_automated({**HUMAN, "precedence": "", "list-id": "  "}) is None


class TestIsAutomatedSenderRules:
    @pytest.mark.parametrize("local_part", sorted(NO_REPLY_LOCAL_PARTS))
    def test_every_no_reply_local_part_is_recognised(self, local_part):
        headers = {"from": f"{local_part}@example.com", "subject": "x"}
        assert is_automated(headers) == "no-reply-sender"

    def test_the_local_part_rule_is_case_insensitive(self):
        assert is_automated({"from": "NoReply@Example.COM"}) == "no-reply-sender"

    def test_a_display_name_containing_noreply_is_not_enough(self):
        # The rule is about the address, not the display name. "Do Not Reply
        # Team <team@example.com>" is a real mailbox somebody reads.
        headers = {"from": "Do Not Reply Team <team@example.com>"}
        assert is_automated(headers) is None


class TestIsAutomatedGmailCategories:
    @pytest.mark.parametrize("label", sorted(BULK_GMAIL_LABELS))
    def test_every_bulk_gmail_category_is_recognised(self, label):
        rule = is_automated(HUMAN, ("INBOX", label))
        assert rule is not None
        assert rule.startswith("gmail-")

    def test_category_personal_is_not_bulk(self):
        assert is_automated(HUMAN, ("INBOX", "CATEGORY_PERSONAL")) is None


class TestInternalSenderRule:
    """Review Focus item 3: our own people are never triaged."""

    def test_the_inbox_writing_to_itself_is_internal(self):
        assert is_internal_sender(payloads.TEST_INBOX, payloads.TEST_INBOX)

    def test_the_escalation_owner_is_internal(self):
        assert is_internal_sender(
            payloads.TEST_OWNER, payloads.TEST_INBOX, payloads.TEST_OWNER
        )

    def test_an_admin_address_is_internal(self):
        assert is_internal_sender(
            "coordinator@example.com",
            payloads.TEST_INBOX,
            payloads.TEST_OWNER,
            ["coordinator@example.com", "other@example.com"],
        )

    def test_a_member_of_the_public_is_not_internal(self):
        assert not is_internal_sender(
            payloads.TEST_SENDER,
            payloads.TEST_INBOX,
            payloads.TEST_OWNER,
            ["coordinator@example.com"],
        )

    def test_matching_ignores_display_name_and_case(self):
        assert is_internal_sender(
            f'"Vietnam Hearts" <{payloads.TEST_INBOX.upper()}>', payloads.TEST_INBOX
        )

    def test_an_empty_sender_is_not_internal(self):
        # Fails open to "not internal" so an unparseable From cannot make a
        # stranger's mail look like our own and skip triage entirely.
        assert not is_internal_sender("", payloads.TEST_INBOX)

    def test_unset_configuration_matches_nobody(self):
        assert not is_internal_sender(payloads.TEST_SENDER, None, None, None)


class TestHumanReplied:
    def _thread(self, *resources):
        return [RawMail.from_resource(resource) for resource in resources]

    def test_a_reply_from_the_inbox_counts_as_a_human(self):
        thread = load_gmail("thread_with_human_reply.json")["messages"]
        assert human_replied(self._thread(*thread), payloads.TEST_INBOX, set())

    def test_the_bots_own_reply_does_not_count(self):
        thread = load_gmail("thread_with_human_reply.json")["messages"]
        bot_sent = {thread[1]["id"]}
        assert not human_replied(self._thread(*thread), payloads.TEST_INBOX, bot_sent)

    def test_an_inbound_only_thread_has_no_human_reply(self):
        assert not human_replied([raw("faq_en.json")], payloads.TEST_INBOX, set())

    def test_no_inbox_address_fails_closed_to_a_human_reply(self):
        # With nothing to compare against, no message can be attributed to the
        # bot either, so the thread is paused rather than replied over.
        assert human_replied([raw("faq_en.json")], "", set())
        assert human_replied([raw("faq_en.json")], None, set())

    def test_an_unsent_draft_from_the_inbox_is_not_a_reply(self):
        draft = payloads.message(
            message_id="r-draft",
            from_address=payloads.TEST_INBOX,
            label_ids=("DRAFT",),
        )
        thread = self._thread(load_gmail("faq_en.json"), draft)
        assert not human_replied(thread, payloads.TEST_INBOX, set())

    def test_a_multi_recipient_from_header_still_matches(self):
        resource = payloads.message(
            message_id="m-multi",
            from_address=f"Someone <other@example.com>, <{payloads.TEST_INBOX}>",
        )
        assert human_replied(self._thread(resource), payloads.TEST_INBOX, set())


class TestSenderKey:
    def test_the_same_address_always_hashes_the_same(self):
        assert sender_key("Person@Example.com") == sender_key("person@example.com")

    def test_different_addresses_hash_differently(self):
        assert sender_key("a@example.com") != sender_key("b@example.com")

    def test_the_address_is_not_recoverable_from_the_key(self):
        key = sender_key("person@example.com")
        assert "person" not in key
        assert "@" not in key
        assert len(key) == 64

    def test_a_display_name_does_not_change_the_key(self):
        assert sender_key('"A Person" <person@example.com>') == sender_key(
            "person@example.com"
        )


class TestNormaliseAddress:
    @pytest.mark.parametrize(
        "raw_value,expected",
        [
            ('"A Person" <A.Person@Example.COM>', "a.person@example.com"),
            ("plain@example.com", "plain@example.com"),
            ("  spaced@example.com  ", "spaced@example.com"),
            ("", ""),
            (None, ""),
            ("not an address", ""),
        ],
    )
    def test_normalisation(self, raw_value, expected):
        assert normalise_address(raw_value) == expected


class TestRecordedFixturesExerciseTheRules:
    """The recorded payloads are the other half of the remedy for defect 2."""

    @pytest.mark.parametrize(
        "fixture,expected_rule",
        [
            ("newsletter.json", "precedence"),
            ("vacation_autoreply.json", "auto-submitted"),
            ("bounce.json", "auto-submitted"),
        ],
    )
    def test_automated_fixtures_are_skipped(self, fixture, expected_rule):
        mail = raw(fixture)
        assert is_automated(mail.headers(), mail.label_ids) == expected_rule

    @pytest.mark.parametrize(
        "fixture",
        [
            "signup_en.json",
            "signup_vi.json",
            "faq_en.json",
            "safeguarding_vi.json",
            "sponsorship_en.json",
            "multi_topic_en.json",
        ],
    )
    def test_human_fixtures_reach_the_classifier(self, fixture):
        mail = raw(fixture)
        assert is_automated(mail.headers(), mail.label_ids) is None
