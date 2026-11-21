"""On-demand evaluation of the inbox bot. Not imported by the application.

A package rather than loose scripts so the runners can share the loader, the
metric arithmetic and the judge, and so the parts that can be wrong for free -
the golden set's own validation and the percentages - are importable by tests
that run in CI without making an API call.
"""
