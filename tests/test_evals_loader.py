"""Validation of the golden set. Runs in CI, makes no API call.

This is the test that keeps every other eval number honest. The golden set is
the reference everything is measured against, so a case with a hand-chosen tier
or a missing language would quietly corrupt a recall score that somebody then
makes a decision on.

The tier invariant is the one to read first: no ``expected_tier`` in the file is
trusted as written, and the loader recomputes each one with the real
``derive_tier``. A change to the policy therefore fails validation here rather
than showing up as a mysteriously improved score.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import pytest

from app.services.triage.policy import decide
from app.services.triage.protocol import CATEGORIES, LANGUAGES
from evals.loader import (
    GOLDEN_SET_PATH,
    GOLDEN_THRESHOLD,
    GoldenSetInvalid,
    answerable_cases,
    as_incoming,
    executive_cases,
    load_golden_set,
)

MINIMUM_CASES = 60


@pytest.fixture(scope="module")
def cases():
    return load_golden_set()


class TestTheFileLoads:
    def test_the_golden_set_is_where_the_runners_look_for_it(self):
        assert GOLDEN_SET_PATH.is_file()

    def test_it_loads_without_a_single_problem(self, cases):
        # load_golden_set reports every problem at once, so a failure here
        # names all of them rather than one per run.
        assert cases

    def test_it_has_at_least_the_designs_minimum(self, cases):
        assert len(cases) >= MINIMUM_CASES


class TestIdentity:
    def test_every_id_is_unique(self, cases):
        ids = [case.id for case in cases]
        assert len(ids) == len(set(ids))

    def test_no_id_is_blank(self, cases):
        assert all(case.id.strip() for case in cases)


class TestCoverage:
    @pytest.mark.parametrize("category", CATEGORIES)
    def test_every_category_appears(self, cases, category):
        assert any(case.expected_category == category for case in cases)

    @pytest.mark.parametrize("category", CATEGORIES)
    @pytest.mark.parametrize("language", ["en", "vi"])
    def test_every_category_appears_in_both_languages(self, cases, category, language):
        # The executive-recall gate is measured on both, and Jev's Vietnamese
        # support is undocumented, so a category with only English cases would
        # certify half of what the gate claims.
        matching = [
            case
            for case in cases
            if case.expected_category == category and case.language == language
        ]
        assert matching, f"no {language} case for {category}"

    def test_every_language_in_the_enum_is_represented(self, cases):
        present = {case.language for case in cases}
        assert present == set(LANGUAGES)

    def test_there_is_at_least_one_other_language_case(self, cases):
        # Answerable in substance, escalated on language alone: the case that
        # proves derive_tier rule 3 fires before the category's own tier.
        other = [case for case in cases if case.language == "other"]
        assert other
        assert all(case.expected_tier == "needs_admin" for case in other)


class TestTheTierInvariant:
    def test_every_expected_tier_is_what_derive_tier_returns(self, cases):
        for case in cases:
            derived = decide(case.expected_signals, GOLDEN_THRESHOLD).tier
            assert derived == case.expected_tier, case.id

    def test_a_hand_edited_tier_is_refused(self, tmp_path):
        # The mechanism, demonstrated: writing a tier that the policy does not
        # produce fails loudly rather than silently changing what is measured.
        broken = tmp_path / "broken.yaml"
        broken.write_text(
            "cases:\n"
            "  - id: wrong-tier\n"
            "    language: en\n"
            "    subject: Sponsorship\n"
            "    text: We would like to sponsor you.\n"
            "    expected_category: sponsorship\n"
            "    expected_tier: auto_answer\n",
            encoding="utf-8",
        )
        with pytest.raises(GoldenSetInvalid, match="derive_tier"):
            load_golden_set(broken)

    def test_every_executive_category_case_is_needs_executive(self, cases):
        from app.services.triage.policy import EXECUTIVE_CATEGORIES

        for case in cases:
            if case.expected_category in EXECUTIVE_CATEGORIES:
                assert case.expected_tier == "needs_executive", case.id

    def test_every_automated_case_is_skip(self, cases):
        for case in cases:
            if case.expected_category == "automated":
                assert case.expected_tier == "skip", case.id


class TestGuardCases:
    def test_at_least_one_case_carries_guard_headers_or_labels(self, cases):
        assert any(case.is_guard_case for case in cases)

    def test_every_guard_case_is_actually_caught_by_a_guard(self, cases):
        # If a case carries guard headers but the guards do not recognise them,
        # the case is testing nothing.
        from app.services.channels.mail_guards import is_automated

        for case in cases:
            if not case.is_guard_case:
                continue
            headers = {name.lower(): value for name, value in case.headers.items()}
            assert is_automated(headers, case.label_ids) is not None, case.id

    def test_no_human_case_is_caught_by_a_guard(self, cases):
        # The converse, and the more dangerous direction: a real person's mail
        # that the guards drop is never triaged, never escalated, and never
        # answered.
        from app.services.channels.mail_guards import is_automated

        for case in cases:
            if case.expected_category == "automated":
                continue
            headers = {name.lower(): value for name, value in case.headers.items()}
            assert is_automated(headers, case.label_ids) is None, case.id

    def test_both_languages_have_an_automated_case(self, cases):
        automated = {
            case.language for case in cases if case.expected_category == "automated"
        }
        assert {"en", "vi"} <= automated


class TestMultiTopicCases:
    def test_at_least_one_case_has_several_prohibitions(self, cases):
        assert any(len(case.must_not_answer) > 1 for case in cases)

    def test_a_multi_topic_case_escalates_on_its_most_serious_part(self, cases):
        # The specific composition: a mail that is answerable in one half and
        # escalating in the other. The escalating half has to win.
        multi = [case for case in cases if case.id.startswith("multi-topic")]
        assert multi, "no multi-topic cases"
        assert all(case.expected_tier == "needs_executive" for case in multi)

    def test_multi_topic_cases_exist_in_both_languages(self, cases):
        multi = {case.language for case in cases if case.id.startswith("multi-topic")}
        assert {"en", "vi"} <= multi


class TestAnswerableCasesCanBeJudged:
    def test_every_answerable_case_declares_facts_to_check(self, cases):
        for case in answerable_cases(cases):
            assert case.must_mention, case.id

    def test_an_answerable_case_without_facts_is_refused(self, tmp_path):
        broken = tmp_path / "broken.yaml"
        broken.write_text(
            "cases:\n"
            "  - id: no-facts\n"
            "    language: en\n"
            "    subject: How do I sign up?\n"
            "    text: How do I sign up?\n"
            "    expected_category: signup\n"
            "    expected_tier: auto_answer\n",
            encoding="utf-8",
        )
        with pytest.raises(GoldenSetInvalid, match="must_mention"):
            load_golden_set(broken)

    def test_only_signup_and_faq_are_answerable(self, cases):
        categories = {case.expected_category for case in answerable_cases(cases)}
        assert categories == {"signup", "faq"}


class TestExecutiveSelection:
    def test_the_gate_set_is_not_empty(self, cases):
        assert executive_cases(cases)

    def test_the_gate_set_covers_both_languages(self, cases):
        languages = {case.language for case in executive_cases(cases)}
        assert {"en", "vi"} <= languages

    def test_safeguarding_is_present_in_both_languages(self, cases):
        # The category whose recall matters most.
        languages = {
            case.language
            for case in cases
            if case.expected_category == "safeguarding_legal"
        }
        assert {"en", "vi"} <= languages


class TestRejectsBadInput:
    def _write(self, tmp_path, body: str):
        path = tmp_path / "cases.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_a_missing_file_is_refused(self, tmp_path):
        with pytest.raises(GoldenSetInvalid, match="no golden set"):
            load_golden_set(tmp_path / "absent.yaml")

    def test_an_empty_file_is_refused(self, tmp_path):
        with pytest.raises(GoldenSetInvalid, match="no list of cases"):
            load_golden_set(self._write(tmp_path, "cases: []\n"))

    def test_an_unknown_category_is_refused(self, tmp_path):
        body = (
            "cases:\n"
            "  - id: bad-category\n"
            "    language: en\n"
            "    subject: x\n"
            "    text: x\n"
            "    expected_category: enquiry\n"
            "    expected_tier: needs_admin\n"
        )
        with pytest.raises(GoldenSetInvalid, match="unknown category"):
            load_golden_set(self._write(tmp_path, body))

    def test_an_unknown_language_is_refused(self, tmp_path):
        body = (
            "cases:\n"
            "  - id: bad-language\n"
            "    language: fr\n"
            "    subject: x\n"
            "    text: x\n"
            "    expected_category: human_other\n"
            "    expected_tier: needs_admin\n"
        )
        with pytest.raises(GoldenSetInvalid, match="unknown language"):
            load_golden_set(self._write(tmp_path, body))

    def test_a_missing_field_is_refused(self, tmp_path):
        body = (
            "cases:\n"
            "  - id: no-text\n"
            "    language: en\n"
            "    subject: x\n"
            "    expected_category: human_other\n"
            "    expected_tier: needs_admin\n"
        )
        with pytest.raises(GoldenSetInvalid, match="missing text"):
            load_golden_set(self._write(tmp_path, body))

    def test_a_duplicate_id_is_refused(self, tmp_path):
        case = (
            "  - id: same\n"
            "    language: {language}\n"
            "    subject: x\n"
            "    text: x\n"
            "    expected_category: human_other\n"
            "    expected_tier: needs_admin\n"
        )
        body = "cases:\n" + case.format(language="en") + case.format(language="vi")
        with pytest.raises(GoldenSetInvalid, match="duplicate id"):
            load_golden_set(self._write(tmp_path, body))

    def test_every_problem_is_reported_at_once(self, tmp_path):
        # Fixing a golden set one error per run is how it stops being
        # maintained.
        body = (
            "cases:\n"
            "  - id: bad-one\n"
            "    language: fr\n"
            "    subject: x\n"
            "    text: x\n"
            "    expected_category: enquiry\n"
            "    expected_tier: needs_admin\n"
            "  - id: bad-two\n"
            "    language: en\n"
            "    subject: x\n"
            "    expected_category: human_other\n"
            "    expected_tier: needs_admin\n"
        )
        with pytest.raises(GoldenSetInvalid) as raised:
            load_golden_set(self._write(tmp_path, body))

        message = str(raised.value)
        assert "bad-one" in message
        assert "bad-two" in message


class TestAsIncoming:
    def test_a_case_becomes_the_message_a_classifier_expects(self, cases):
        case = cases[0]
        message = as_incoming(case)

        assert message.channel == "email"
        assert message.subject == case.subject
        assert message.text == case.text
        assert message.provider_message_id == f"golden-{case.id}"

    def test_the_headers_are_lower_cased_for_the_guards(self, cases):
        guard = next(case for case in cases if case.headers)
        message = as_incoming(guard)
        assert all(name == name.lower() for name in message.headers)

    def test_the_sender_is_fictional_and_identical_for_every_case(self, cases):
        senders = {as_incoming(case).sender_address for case in cases}
        assert senders == {"golden@example.com"}

    def test_the_sender_key_is_a_hash(self, cases):
        message = as_incoming(cases[0])
        assert "@" not in message.sender_key
        assert len(message.sender_key) == 64


class TestNoRealDataInTheGoldenSet:
    def test_no_case_contains_a_real_looking_address(self, cases):
        # Every address in the file must be on a reserved documentation domain.
        import re

        pattern = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
        for case in cases:
            for found in pattern.findall(f"{case.subject}\n{case.text}"):
                assert found.endswith(
                    ("example.com", "example.org", "example.net")
                ), f"{case.id}: {found}"

    def test_the_real_volunteer_inbox_appears_nowhere(self, cases):
        text = "\n".join(f"{case.subject}{case.text}" for case in cases)
        assert "vietnam.hearts.volunteering" not in text

    def test_no_case_carries_a_phone_number_shaped_string(self, cases):
        import re

        # Eight or more consecutive digits, which is what a phone number or an
        # identity document number would look like.
        pattern = re.compile(r"\d{8,}")
        for case in cases:
            assert not pattern.search(f"{case.subject}\n{case.text}"), case.id
