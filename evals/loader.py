"""Typed loading and validation of the golden set.

The validation runs in CI and makes no API call, which is the point: the golden
set is the thing every other eval is measured against, so a case with a
mistyped category or a hand-chosen tier would quietly corrupt every number the
runners print.

The tier invariant is the important one. ``expected_tier`` is never trusted as
written: it is recomputed from the case's expected signals with the real
``derive_tier`` and compared. That way the golden set cannot drift from the
policy it is measuring, and a change to the policy shows up as a failing
validation rather than as a mysteriously improved recall score.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.triage.policy import decide
from app.services.triage.protocol import CATEGORIES, LANGUAGES, TriageSignals

GOLDEN_SET_PATH = Path(__file__).parent / "golden_qa.yaml"

# The confidence a correct classifier would report on an unambiguous case. Used
# only to derive the expected tier, never compared against a real answer.
GOLDEN_CONFIDENCE = 1.0

# The threshold the expected tiers are derived at. Deliberately the design's
# default rather than whatever is configured: the golden set is a fixed
# reference, and a calibration sweep that changed what the expectations *mean*
# would be measuring itself.
GOLDEN_THRESHOLD = 0.6


class GoldenSetInvalid(ValueError):
    """The golden set does not satisfy its own contract."""


@dataclass(frozen=True)
class GoldenCase:
    """One case, validated."""

    id: str
    language: str
    subject: str
    text: str
    expected_category: str
    expected_tier: str
    expected_asks_for_human: bool = False
    expected_mentions_money: bool = False
    must_mention: tuple[str, ...] = ()
    must_not_answer: tuple[str, ...] = ()
    headers: Mapping[str, str] = field(default_factory=dict)
    label_ids: tuple[str, ...] = ()
    note: str = ""

    @property
    def expected_signals(self) -> TriageSignals:
        """What a perfect classifier would report for this case."""
        return TriageSignals(
            category=self.expected_category,
            language=self.expected_language,
            confidence=GOLDEN_CONFIDENCE,
            asks_for_human=self.expected_asks_for_human,
            mentions_money_or_commitment=self.expected_mentions_money,
            classifier="golden",
        )

    @property
    def expected_language(self) -> str:
        return self.language

    @property
    def is_guard_case(self) -> bool:
        """Whether the deterministic guards should catch this before any model.

        A guard case is one that carries the headers or labels the guards read.
        Those cases are still classified by the eval runners, because the
        ``automated`` category is the second line of defence behind the guards
        and its accuracy is worth knowing on its own.
        """
        return bool(self.headers) or bool(self.label_ids)

    @property
    def is_executive(self) -> bool:
        return self.expected_tier == "needs_executive"

    @property
    def is_answerable(self) -> bool:
        return self.expected_tier == "auto_answer"


def load_golden_set(path: Path | None = None) -> tuple[GoldenCase, ...]:
    """Every case, validated. Raises ``GoldenSetInvalid`` on any problem.

    Validates the whole file and reports every problem at once rather than
    stopping at the first: fixing a golden set one error per run is how it stops
    being maintained.
    """
    source = path or GOLDEN_SET_PATH
    raw = _read(source)

    cases: list[GoldenCase] = []
    problems: list[str] = []

    for index, entry in enumerate(raw):
        try:
            cases.append(_build_case(entry, index))
        except GoldenSetInvalid as exc:
            problems.append(str(exc))

    problems.extend(_collective_problems(cases))

    if problems:
        raise GoldenSetInvalid(
            f"{len(problems)} problem(s) in {source.name}:\n  - "
            + "\n  - ".join(problems)
        )
    return tuple(cases)


def _read(source: Path) -> list[Mapping[str, Any]]:
    import yaml

    if not source.is_file():
        raise GoldenSetInvalid(f"no golden set at {source}")

    loaded = yaml.safe_load(source.read_text(encoding="utf-8"))
    cases = loaded.get("cases") if isinstance(loaded, Mapping) else loaded
    if not isinstance(cases, list) or not cases:
        raise GoldenSetInvalid(f"{source} contains no list of cases")
    return cases


def _build_case(entry: Mapping[str, Any], index: int) -> GoldenCase:
    where = entry.get("id") or f"case #{index}"

    for required in (
        "id",
        "language",
        "subject",
        "text",
        "expected_category",
        "expected_tier",
    ):
        if not entry.get(required):
            raise GoldenSetInvalid(f"{where}: missing {required}")

    category = entry["expected_category"]
    if category not in CATEGORIES:
        raise GoldenSetInvalid(f"{where}: unknown category {category!r}")

    language = entry["language"]
    if language not in LANGUAGES:
        raise GoldenSetInvalid(f"{where}: unknown language {language!r}")

    case = GoldenCase(
        id=entry["id"],
        language=language,
        subject=entry["subject"],
        text=entry["text"],
        expected_category=category,
        expected_tier=entry["expected_tier"],
        expected_asks_for_human=bool(entry.get("expected_asks_for_human", False)),
        expected_mentions_money=bool(entry.get("expected_mentions_money", False)),
        must_mention=tuple(entry.get("must_mention") or ()),
        must_not_answer=tuple(entry.get("must_not_answer") or ()),
        headers=dict(entry.get("headers") or {}),
        label_ids=tuple(entry.get("label_ids") or ()),
        note=entry.get("note", "") or "",
    )

    derived = decide(case.expected_signals, GOLDEN_THRESHOLD).tier
    if derived != case.expected_tier:
        raise GoldenSetInvalid(
            f"{where}: expected_tier is {case.expected_tier!r} but derive_tier "
            f"gives {derived!r} for category {category!r}, language {language!r}, "
            f"asks_for_human={case.expected_asks_for_human}, "
            f"mentions_money_or_commitment={case.expected_mentions_money}"
        )

    if case.is_answerable and not case.must_mention:
        raise GoldenSetInvalid(
            f"{where}: an auto_answer case needs must_mention, or the "
            "groundedness judge has nothing to judge against"
        )

    return case


def _collective_problems(cases: list[GoldenCase]) -> Iterator[str]:
    seen: dict[str, int] = {}
    for case in cases:
        seen[case.id] = seen.get(case.id, 0) + 1
    for case_id, count in seen.items():
        if count > 1:
            yield f"duplicate id {case_id!r} ({count} times)"

    by_category: dict[str, set[str]] = {}
    for case in cases:
        by_category.setdefault(case.expected_category, set()).add(case.language)

    for category in CATEGORIES:
        languages = by_category.get(category, set())
        if not languages:
            yield f"category {category!r} has no cases at all"
            continue
        for language in ("en", "vi"):
            if language not in languages:
                yield f"category {category!r} has no {language} case"

    if not any(case.is_guard_case for case in cases):
        yield "no case carries guard headers or labels"

    if not any(len(case.must_not_answer) > 1 for case in cases):
        yield "no multi-constraint case; the multi-topic rule is unmeasured"


def executive_cases(cases: tuple[GoldenCase, ...]) -> tuple[GoldenCase, ...]:
    """The cases the 100 percent recall gate is measured on."""
    return tuple(case for case in cases if case.is_executive)


def answerable_cases(cases: tuple[GoldenCase, ...]) -> tuple[GoldenCase, ...]:
    """The cases the groundedness judge is measured on."""
    return tuple(case for case in cases if case.is_answerable)


def as_incoming(case: GoldenCase):
    """One golden case as the ``IncomingMessage`` a classifier expects.

    Built here rather than in each runner so every runner measures the
    classifier on exactly the same input the pipeline would give it.
    """
    from app.services.channels.base import EMAIL_CHANNEL, IncomingMessage
    from app.services.channels.mail_guards import sender_key

    # A fixed fictional sender: the classifier is never shown the address, and
    # a per-case one would only invite a runner to start reading it.
    address = "golden@example.com"
    return IncomingMessage(
        channel=EMAIL_CHANNEL,
        thread_key=f"golden-{case.id}",
        provider_message_id=f"golden-{case.id}",
        sender_key=sender_key(address),
        sender_address=address,
        subject=case.subject,
        text=case.text,
        rfc_message_id=f"<{case.id}@golden.example.com>",
        headers={name.lower(): value for name, value in case.headers.items()},
        label_ids=case.label_ids,
    )
