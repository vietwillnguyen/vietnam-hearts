"""Every URL the admin dashboard fetches must be a route mounted on the app.

Commit f5bea73 unmounted the bot routers while the Messenger bot waits on Meta
App Review, but left the dashboard's Bot Management tab calling them. FastAPI
resolves routing before dependencies, so those calls answered a bare
``404 {"detail":"Not Found"}`` - which is what the admin saw, in an alert, just
for opening the tab. Nothing failed at build or import time, because a fetch
URL in a template is only a string.

These tests close that gap from the browser's side: they parse the *rendered*
dashboard, pull out every path it fetches, and put each through the app's own
router with its own method. The rendered page is the right input - the gate
that hides the bot tab is a Jinja ``{% if %}``, so the raw template still holds
those URLs while the browser never receives them. And ``route_match`` is the
real routing logic rather than a reimplementation of it, so this cannot drift
from what the server actually serves.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import re

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from starlette.routing import Match

from app.main import app
from app.routers.admin.helpers import bot_routes_mounted, route_match

TEMPLATE = "admin/dashboard.html"

# The dashboard renders without any of its data; these tests only care about
# the URLs its JavaScript carries, so the context stays minimal.
BASE_CONTEXT = {
    "total_volunteers": 0,
    "total_emails": 0,
    "volunteers": [],
    "emails": [],
    "settings": [],
    "version": "test",
}

# Fetches whose first argument is a variable rather than a literal. Each is
# listed with the paths that variable can hold, read from the function that
# builds it. Keeping them here rather than silently skipping them means a new
# dynamic fetch fails these tests until someone writes down where it points.
DYNAMIC_FETCH_PATHS = {
    # rotateSchedule(): '/admin/rotate-schedule', optionally ?display_weeks=N
    "url": ["/admin/rotate-schedule"],
}

_FETCH_LITERAL = re.compile(r"""fetch\(\s*(['"`])(/[^'"`\s]*)\1""")
_FETCH_DYNAMIC = re.compile(r"fetch\(\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*,")
_INTERPOLATION = re.compile(r"\$\{[^}]*\}")
_METHOD = re.compile(r"""method:\s*['"](\w+)['"]""")


def render(**context) -> str:
    """Render the dashboard exactly as the handler does."""
    templates = Jinja2Templates(directory="templates/web")
    return templates.get_template(TEMPLATE).render(BASE_CONTEXT | context)


def concrete(path: str) -> str:
    """A requestable path: query string dropped, interpolations filled in.

    Every path parameter in this app is matched by Starlette's default string
    converter, so any non-empty segment stands in for one.
    """
    path = path.split("?", 1)[0].split("#", 1)[0]
    return _INTERPOLATION.sub("1", path)


def method_of(html: str, call_start: int) -> str:
    """The HTTP method of the fetch starting at ``call_start``.

    ``fetch`` defaults to GET, and every options object in this template puts
    ``method:`` inside the same call, so look only as far as the next fetch.
    """
    next_call = html.find("fetch(", call_start + 1)
    window = html[call_start : next_call if next_call != -1 else len(html)]
    match = _METHOD.search(window)
    return match.group(1).upper() if match else "GET"


def fetched_requests(html: str) -> set[tuple[str, str]]:
    """Every (method, path) pair the rendered page can request."""
    requests = set()

    for match in _FETCH_LITERAL.finditer(html):
        requests.add((method_of(html, match.start()), concrete(match.group(2))))

    for match in _FETCH_DYNAMIC.finditer(html):
        variable = match.group(1)
        assert variable in DYNAMIC_FETCH_PATHS, (
            f"fetch({variable}, ...) passes a variable this test cannot read. "
            f"Add {variable} to DYNAMIC_FETCH_PATHS with the paths it can hold."
        )
        method = method_of(html, match.start())
        for path in DYNAMIC_FETCH_PATHS[variable]:
            requests.add((method, concrete(path)))

    return requests


def unserved(html: str, application: FastAPI) -> list[str]:
    """The requests the page makes that this app would not answer.

    ``PARTIAL`` is called out separately because a dashboard button wired to
    the wrong method is a 405 - a different error for the same mistake.
    """
    report = []
    for method, path in sorted(fetched_requests(html)):
        match = route_match(application, method, path)
        if match is Match.NONE:
            report.append(f"{method} {path} (no route - 404)")
        elif match is Match.PARTIAL:
            report.append(f"{method} {path} (wrong method - 405)")
    return report


def app_with_bot() -> FastAPI:
    """The app as it would be with the bot routers included again."""
    from app.routers.bot import admin_router as bot_admin_router
    from app.routers.bot import public_bot_router

    with_bot = FastAPI()
    with_bot.routes.extend(app.routes)
    with_bot.include_router(public_bot_router)
    with_bot.include_router(bot_admin_router)
    return with_bot


class TestRenderedDashboardOnlyCallsMountedRoutes:
    def test_the_dashboard_as_shipped_calls_nothing_unmounted(self):
        """The regression itself: what the admin's browser actually loads."""
        html = render(bot_enabled=bot_routes_mounted(app))

        assert unserved(html, app) == []

    def test_the_parser_finds_the_calls_it_is_meant_to_guard(self):
        """A parser that matched nothing would pass the test above vacuously."""
        requests = fetched_requests(render(bot_enabled=False))

        assert ("GET", "/auth/me") in requests
        assert ("POST", "/admin/send-weekly-reminders") in requests
        assert ("GET", "/admin/logs") in requests
        assert ("POST", "/admin/rotate-schedule") in requests
        assert ("POST", "/admin/volunteers/1/deactivate") in requests
        assert len(requests) > 15

    def test_an_unmounted_call_is_reported(self):
        """The failure mode this guard exists for, forced on purpose."""
        html = (
            "<script>fetch('/admin/bot/knowledge-sync/status', "
            "{method: 'GET'})</script>"
        )

        assert unserved(html, app) == [
            "GET /admin/bot/knowledge-sync/status (no route - 404)"
        ]

    def test_the_right_method_is_required(self):
        """GET /admin/send-weekly-reminders is a 405, and that is a bug too."""
        html = "<script>fetch('/admin/send-weekly-reminders')</script>"

        assert unserved(html, app) == [
            "GET /admin/send-weekly-reminders (wrong method - 405)"
        ]


class TestBotTabFollowsTheMountedRouters:
    """The tab is gated on the routing table, not on a flag of its own.

    Re-including the bot routers in app/main.py is meant to be the only edit
    needed to bring the tab back, so the flag is checked in both directions.
    """

    def test_the_bot_routers_are_not_mounted_on_the_app(self):
        """Paused on Meta App Review; re-enabling is the captain's call."""
        assert bot_routes_mounted(app) is False

    def test_the_flag_is_true_when_the_routers_are_included(self):
        assert bot_routes_mounted(app_with_bot()) is True

    def test_the_hidden_tab_leaves_nothing_behind(self):
        html = render(bot_enabled=False)

        assert "Bot Management" not in html
        assert 'id="bot-tab"' not in html
        assert 'id="bot"' not in html
        assert "/admin/bot/" not in html
        assert "knowledge" not in html.lower()

    def test_the_tab_comes_back_with_the_routers(self):
        html = render(bot_enabled=True)

        assert 'id="bot-tab"' in html
        assert "Check Knowledge Status" in html
        assert "/admin/bot/knowledge-sync/status" in html

    def test_the_restored_tab_would_call_only_mounted_routes(self):
        """Guards the gated block too, so re-enabling cannot ship a 404."""
        assert unserved(render(bot_enabled=True), app_with_bot()) == []


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/admin/logs?limit=1", "/admin/logs"),
        ("/admin/volunteers/${id}/deactivate", "/admin/volunteers/1/deactivate"),
        ("/settings/${key}", "/settings/1"),
        (
            "/admin/bot/documents?folder_id=${encodeURIComponent(f)}",
            "/admin/bot/documents",
        ),
    ],
)
def test_concrete(path, expected):
    assert concrete(path) == expected
