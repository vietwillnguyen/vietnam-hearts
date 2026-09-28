"""The email channel's own orchestration: settings, replies, delivery, pipeline.

Everything channel-neutral lives one level up in ``channels`` and ``triage``.
What is here is the part Messenger will have no equivalent of: the three-way
delivery mode, the drafts, and the per-run poll that Cloud Scheduler drives.
"""
