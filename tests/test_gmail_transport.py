"""The Gmail transport against a mocked Discovery client.

The headline test in this file is the one that fails if anything so much as
*reaches for* a send method. The design's "draft mode by construction" control
is the absence of a send path, and an absence is only enforceable by something
that notices an attempt.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import base64
from unittest.mock import MagicMock

import pytest

from app.services.channels.gmail_transport import (
    LABEL_SEEN,
    OUTCOME_LABELS,
    GmailTransport,
    RawMail,
    category_label,
)
from tests.fixtures import gmail_payloads as payloads
from tests.fixtures.email_bot import load_gmail


class SendIsForbidden(AssertionError):
    """Raised the moment anything touches a send-shaped attribute."""


class GuardedMessages:
    """A ``users().messages()`` stand-in that explodes on ``send``.

    Deliberately not ``MagicMock(spec=...)``: a spec would only refuse a call
    with the wrong signature, and this has to refuse the *attribute lookup*, so
    that a future refactor reintroducing a send path fails here rather than in
    production.
    """

    def __init__(self, service) -> None:
        self._service = service
        self.list = service.list_calls
        self.get = service.get_calls
        self.modify = service.modify_calls

    def __getattr__(self, name: str):
        if "send" in name.lower():
            raise SendIsForbidden(
                f"users().messages().{name} must not exist before phase E3"
            )
        raise AttributeError(name)


class FakeGmailService:
    """Just enough of the Discovery client to drive the transport."""

    def __init__(
        self,
        messages=(),
        labels=(),
        thread_messages=None,
        profile_address=payloads.TEST_INBOX,
    ) -> None:
        self._messages = {resource["id"]: resource for resource in messages}
        self._listed = list(messages)
        self._labels = payloads.labels_response(*labels)
        self._thread_messages = thread_messages
        self._profile_address = profile_address

        self.list_queries: list[dict] = []
        self.created_labels: list[dict] = []
        self.modified: list[dict] = []
        self.created_drafts: list[dict] = []
        self.deleted_drafts: list[str] = []

    # --- the users() chain -------------------------------------------------

    def users(self):
        return self

    def getProfile(self, userId):  # noqa: N803 - the API's own parameter name
        return _Executable({"emailAddress": self._profile_address})

    def messages(self):
        return GuardedMessages(self)

    def list_calls(self, userId, q, maxResults):  # noqa: N803
        self.list_queries.append({"q": q, "maxResults": maxResults})
        return _Executable(payloads.list_response(*self._listed[:maxResults]))

    def get_calls(self, userId, id, format):  # noqa: A002,N803
        return _Executable(self._messages[id])

    def modify_calls(self, userId, id, body):  # noqa: A002,N803
        self.modified.append({"id": id, "body": body})
        return _Executable({"id": id})

    def threads(self):
        return _Threads(self)

    def labels(self):
        return _Labels(self)

    def drafts(self):
        return _Drafts(self)


class _Executable:
    def __init__(self, result) -> None:
        self._result = result

    def execute(self):
        return self._result


class _Threads:
    def __init__(self, service) -> None:
        self._service = service

    def get(self, userId, id, format):  # noqa: A002,N803
        messages = self._service._thread_messages or [
            resource for resource in self._service._listed if resource["threadId"] == id
        ]
        return _Executable(payloads.thread(*messages, thread_id=id))


class _Labels:
    def __init__(self, service) -> None:
        self._service = service

    def list(self, userId):  # noqa: N803
        return _Executable(self._service._labels)

    def create(self, userId, body):  # noqa: N803
        self._service.created_labels.append(body)
        new_id = f"Label_new_{len(self._service.created_labels)}"
        self._service._labels["labels"].append(
            {"id": new_id, "name": body["name"], "type": "user"}
        )
        return _Executable({"id": new_id, "name": body["name"]})


class _Drafts:
    def __init__(self, service) -> None:
        self._service = service

    def create(self, userId, body):  # noqa: N803
        self._service.created_drafts.append(body)
        return _Executable({"id": f"draft-{len(self._service.created_drafts)}"})

    def get(self, userId, id, format):  # noqa: A002,N803
        return _Executable({"message": next(iter(self._service._messages.values()))})

    def delete(self, userId, id):  # noqa: A002,N803
        self._service.deleted_drafts.append(id)
        return _Executable({})


@pytest.fixture
def service():
    return FakeGmailService(
        messages=[load_gmail("signup_en.json"), load_gmail("faq_en.json")],
        labels=[LABEL_SEEN],
    )


@pytest.fixture
def transport(service):
    return GmailTransport(service)


class TestNothingCanSend:
    def test_the_transport_has_no_send_method(self):
        # The one assertion that makes "nothing can send while the mode is off
        # or draft" a property of the code rather than of a setting.
        assert not hasattr(GmailTransport, "send_reply")
        assert not any(
            "send" in name.lower() for name in dir(GmailTransport)
        ), "a send-shaped method appeared on GmailTransport before phase E3"

    def test_touching_users_messages_send_fails_the_test(self, service):
        with pytest.raises(SendIsForbidden):
            # The attribute lookup itself is the assertion.
            _ = service.users().messages().send

    def test_a_whole_listing_and_drafting_cycle_never_touches_send(
        self, transport, service
    ):
        transport.ensure_labels(OUTCOME_LABELS)
        mails = transport.list_unprocessed(
            newer_than_days=7, exclude_label=LABEL_SEEN, limit=20
        )
        for mail in mails:
            transport.add_labels(mail.id, ["Label_100"])
            transport.create_draft(mail.thread_id, b"From: x\r\n\r\nbody\r\n")
        # No SendIsForbidden means the guarded chain was never reached.
        assert len(service.created_drafts) == len(mails)


class TestListing:
    def test_the_query_excludes_the_marker_label_and_bounds_by_age(
        self, transport, service
    ):
        transport.list_unprocessed(newer_than_days=7, exclude_label=LABEL_SEEN, limit=5)
        query = service.list_queries[0]["q"]
        assert "in:inbox" in query
        assert "newer_than:7d" in query
        assert f"-label:{LABEL_SEEN}" in query

    def test_the_limit_is_passed_to_the_api_and_honoured(self, transport, service):
        mails = transport.list_unprocessed(
            newer_than_days=7, exclude_label=LABEL_SEEN, limit=1
        )
        assert service.list_queries[0]["maxResults"] == 1
        assert len(mails) == 1

    def test_each_listed_stub_is_fetched_in_full(self, transport):
        mails = transport.list_unprocessed(
            newer_than_days=7, exclude_label=LABEL_SEEN, limit=20
        )
        # A stub carries only ids; the full resource is what has the headers.
        assert all(mail.payload.get("payload") for mail in mails)
        assert mails[0].header("Subject") == "Volunteering with Vietnam Hearts"

    def test_an_empty_mailbox_yields_no_mail(self):
        transport = GmailTransport(FakeGmailService())
        assert (
            transport.list_unprocessed(
                newer_than_days=7, exclude_label=LABEL_SEEN, limit=20
            )
            == []
        )

    def test_a_stub_with_no_id_is_skipped(self):
        service = FakeGmailService()
        service.list_calls = lambda userId, q, maxResults: _Executable(
            {"messages": [{"threadId": "t"}]}
        )
        transport = GmailTransport(service)
        assert (
            transport.list_unprocessed(
                newer_than_days=7, exclude_label=LABEL_SEEN, limit=20
            )
            == []
        )


class TestLabels:
    def test_missing_labels_are_created_once_and_existing_ones_reused(
        self, transport, service
    ):
        first = transport.ensure_labels([LABEL_SEEN, category_label("signup")])
        created_after_first = len(service.created_labels)
        second = transport.ensure_labels([LABEL_SEEN, category_label("signup")])

        assert set(first) == set(second)
        # LABEL_SEEN already existed, so exactly one label was created, and the
        # second call created nothing.
        assert created_after_first == 1
        assert len(service.created_labels) == 1

    def test_no_names_means_no_api_call(self, transport, service):
        assert transport.ensure_labels([]) == {}
        assert service.created_labels == []

    def test_a_duplicate_name_conflict_is_resolved_by_re_reading(self, service):
        from googleapiclient.errors import HttpError

        conflict = HttpError(MagicMock(status=409), b"already exists")

        class ConflictingLabels(_Labels):
            def create(self, userId, body):  # noqa: N803
                # Simulate a concurrent run having created it first.
                self._service._labels["labels"].append(
                    {"id": "Label_race", "name": body["name"], "type": "user"}
                )
                raise conflict

        service.labels = lambda: ConflictingLabels(service)
        transport = GmailTransport(service)
        resolved = transport.ensure_labels(["VH-Bot/New"])
        assert resolved["VH-Bot/New"] == "Label_race"

    def test_adding_labels_sends_add_label_ids(self, transport, service):
        transport.add_labels("msg-1", ["Label_100", "Label_101"])
        assert service.modified == [
            {"id": "msg-1", "body": {"addLabelIds": ["Label_100", "Label_101"]}}
        ]

    def test_adding_no_labels_makes_no_call(self, transport, service):
        transport.add_labels("msg-1", [])
        assert service.modified == []


class TestDrafts:
    def test_a_draft_is_created_inside_its_thread(self, transport, service):
        mime = b"From: a@example.com\r\nSubject: Re: x\r\n\r\nbody\r\n"
        draft_id = transport.create_draft("thread-9", mime)

        assert draft_id == "draft-1"
        body = service.created_drafts[0]["message"]
        assert body["threadId"] == "thread-9"
        # Gmail wants base64url, and getting the alphabet wrong produces a
        # draft whose body is silently corrupt.
        assert base64.urlsafe_b64decode(body["raw"]) == mime

    def test_deleting_a_draft_calls_delete(self, transport, service):
        transport.delete_draft("draft-7")
        assert service.deleted_drafts == ["draft-7"]

    def test_deleting_an_already_gone_draft_is_not_an_error(self, service):
        from googleapiclient.errors import HttpError

        class MissingDrafts(_Drafts):
            def delete(self, userId, id):  # noqa: A002,N803
                raise HttpError(MagicMock(status=404), b"not found")

        service.drafts = lambda: MissingDrafts(service)
        # The captain sending or deleting the draft between two polls is normal,
        # not a failure.
        GmailTransport(service).delete_draft("draft-gone")

    def test_a_missing_draft_reads_as_none(self, service):
        from googleapiclient.errors import HttpError

        class MissingDrafts(_Drafts):
            def get(self, userId, id, format):  # noqa: A002,N803
                raise HttpError(MagicMock(status=404), b"not found")

        service.drafts = lambda: MissingDrafts(service)
        assert GmailTransport(service).get_draft("draft-gone") is None

    def test_a_non_404_error_propagates(self, service):
        from googleapiclient.errors import HttpError

        class BrokenDrafts(_Drafts):
            def get(self, userId, id, format):  # noqa: A002,N803
                raise HttpError(MagicMock(status=500), b"server error")

        service.drafts = lambda: BrokenDrafts(service)
        with pytest.raises(HttpError):
            GmailTransport(service).get_draft("draft-1")


class TestThreadsAndProfile:
    def test_a_thread_is_read_in_full(self, transport):
        thread = transport.get_thread("18f2a1b4c5d6e7f0")
        assert [mail.id for mail in thread] == ["18f2a1b4c5d6e7f0"]

    def test_the_inbox_address_comes_from_the_grant(self, transport):
        # Read from the grant rather than from configuration, so a token for
        # the wrong mailbox cannot masquerade as the volunteer inbox.
        assert transport.inbox_address == payloads.TEST_INBOX

    def test_the_inbox_address_is_read_once(self, service):
        transport = GmailTransport(service)
        calls = []
        service.getProfile = lambda userId: calls.append(1) or _Executable(
            {"emailAddress": payloads.TEST_INBOX}
        )
        _ = transport.inbox_address
        _ = transport.inbox_address
        assert len(calls) == 1

    def test_a_supplied_address_skips_the_profile_call(self, service):
        service.getProfile = lambda userId: pytest.fail("should not be called")
        assert GmailTransport(service, "given@example.com").inbox_address == (
            "given@example.com"
        )


class TestRawMail:
    def test_headers_are_keyed_by_lower_cased_name(self):
        mail = RawMail.from_resource(load_gmail("signup_en.json"))
        assert mail.header("subject") == "Volunteering with Vietnam Hearts"
        assert mail.header("SUBJECT") == "Volunteering with Vietnam Hearts"

    def test_a_missing_header_is_an_empty_string(self):
        mail = RawMail.from_resource(load_gmail("signup_en.json"))
        assert mail.header("x-not-present") == ""

    def test_the_first_occurrence_of_a_repeated_header_wins(self):
        resource = payloads.message()
        resource["payload"]["headers"].append({"name": "Subject", "value": "second"})
        assert RawMail.from_resource(resource).header("subject") == "Volunteering"

    def test_label_ids_are_a_tuple(self):
        mail = RawMail.from_resource(load_gmail("newsletter.json"))
        assert isinstance(mail.label_ids, tuple)
        assert "CATEGORY_UPDATES" in mail.label_ids
