"""Frozen-clock helper shared by the schedule week tests.

Lives in tests.fixtures rather than tests.conftest because a conftest is
imported by pytest under its own top-level name; importing it again as
``tests.conftest`` would execute the module a second time under a second name
and define a duplicate set of fixtures that pytest never registers.
"""

from datetime import UTC, datetime
from unittest.mock import patch


class FrozenDatetime(datetime):
    """datetime whose now() reports a fixed instant, converting tz for real.

    Patching ``datetime.now`` with a plain MagicMock would ignore the tzinfo
    argument entirely, so the conversion under test would never actually run.
    This subclass keeps the real astimezone() maths and only freezes the clock.
    """

    frozen_utc = datetime(1970, 1, 1, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.frozen_utc.replace(tzinfo=None)
        return cls.frozen_utc.astimezone(tz)


def frozen_at(iso_utc: str):
    """Patch the schedule_dates clock to a fixed UTC instant.

    Lives here rather than in one test module because every caller of the
    shared week anchor - rotation, the fallback dates, the reminder subject -
    needs the same clock frozen at the same seam.
    """
    frozen = type(
        "Frozen",
        (FrozenDatetime,),
        {"frozen_utc": datetime.fromisoformat(iso_utc).replace(tzinfo=UTC)},
    )
    return patch("app.utils.schedule_dates.datetime", frozen)
