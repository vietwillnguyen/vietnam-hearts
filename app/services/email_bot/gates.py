"""The draft-acceptance gates, stated once.

The share of drafts per answer path that must go out unchanged, over two weeks
of draft mode, before automatic sending may be turned on. The metrics endpoint
and the dashboard card both render from this table, and a test holds it to the
design's Evaluation record, so no two places can come to state different gates.
"""

from __future__ import annotations

from types import MappingProxyType

ACCEPTANCE_GATES = MappingProxyType({"signup": 0.9, "faq": 0.8})
