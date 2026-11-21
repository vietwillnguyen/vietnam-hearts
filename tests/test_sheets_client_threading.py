"""
The Sheets client must not be shared across threads.

googleapiclient sends every request through one httplib2.Http per built
service, and httplib2 is not thread-safe. FastAPI runs the sync
/review-and-sync handler in its threadpool while /rotate-schedule runs on the
event loop thread, and both use the module-level sheets_service. When Cloud
Scheduler fires them in the same minute (every second hour) they interleave on
one connection and the rotation dies with
"'NoneType' object has no attribute 'read'".
"""

import threading
from unittest.mock import patch

from google.auth.credentials import AnonymousCredentials
from googleapiclient.discovery import build as real_build

from app.services.google_sheets import GoogleSheetsService


class _ServiceAccountCredentials(AnonymousCredentials):
    service_account_email = "rotation-test@example.iam.gserviceaccount.com"


def _client_in_new_thread(svc: GoogleSheetsService):
    holder = {}

    def run():
        holder["sheet"] = svc.sheet
        holder["service"] = svc.service

    t = threading.Thread(target=run)
    t.start()
    t.join()
    return holder


def _service():
    svc = GoogleSheetsService()
    patch.object(svc, "_validate_config").start()
    return svc


@patch(
    "app.services.google_sheets.get_scoped_credentials",
    return_value=_ServiceAccountCredentials(),
)
def test_each_thread_gets_its_own_http_transport(_creds):
    svc = _service()

    main_service = svc.service
    other = _client_in_new_thread(svc)

    assert other["service"] is not main_service
    # The transport is what httplib2 cannot share; distinct service objects
    # wrapping one Http would not fix anything.
    assert other["service"]._http is not main_service._http
    assert other["sheet"]._http is not svc.sheet._http


@patch(
    "app.services.google_sheets.get_scoped_credentials",
    return_value=_ServiceAccountCredentials(),
)
def test_client_is_reused_within_a_thread(_creds):
    svc = _service()

    with patch("app.services.google_sheets.build", wraps=real_build) as build:
        first = svc.sheet
        assert svc.sheet is first
        assert svc.service is svc.service
        assert build.call_count == 1


def test_injected_client_is_visible_in_the_same_thread():
    """Tests (and callers) that assign _sheet/_initialized directly keep working."""
    svc = GoogleSheetsService.__new__(GoogleSheetsService)
    sentinel = object()
    svc._sheet = sentinel
    svc._initialized = True

    assert svc.sheet is sentinel
