"""The two fixed copies, rendered from settings.

The English sign-up copy is asserted verbatim against the captain's own words,
including the two known typos, because "keep the captain's wording" was an
explicit instruction and a well-meaning silent fix is exactly what this test
exists to catch.
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import pytest

from app.services.email_bot.replies import (
    SignupFacts,
    SignupReplyUnavailable,
    format_class_time,
    localise_days,
    render_holding_message,
    render_signup_reply,
    resolve_language,
    signature,
)

FORM = "https://docs.google.com/forms/d/e/1FAIpQLSexample/viewform"


def facts(
    teaching_days: str = "Tuesday, Thursday",
    start: str = "09:30",
    end: str = "10:30",
    link: str = FORM,
) -> SignupFacts:
    return SignupFacts(
        teaching_days=teaching_days,
        class_start=start,
        class_end=end,
        signup_form_link=link,
    )


class TestEnglishSignupCopyIsTheCaptainsOwn:
    def test_it_renders_the_captains_words_verbatim(self):
        rendered = render_signup_reply("en", facts())
        assert rendered == (
            "Thank you very much for your message and interest in volunteering "
            "with Vietnam Hearts 💚\n"
            "\n"
            "We teach in HCM Tuesdays and Thursdays from 9:30-10:30am and are "
            "always looking for volunteers to come in to support our teachers "
            "as TA or to teach.\n"
            "\n"
            "If you in the area and interested, you can fill out the form "
            "below! we will email you all our onboarding and send you a link to "
            "our groupchat when its approved.\n"
            "\n"
            f"{FORM}"
        )

    @pytest.mark.parametrize("phrase", ["If you in the area", "when its approved"])
    def test_the_two_known_typos_are_left_alone(self, phrase):
        # "Keep the captain's wording; light typo fixes only with his
        # agreement." He has not agreed yet, so the copy renders verbatim and
        # this test is what stops a tidy-up from shipping without him.
        assert phrase in render_signup_reply("en", facts())

    def test_it_points_only_to_the_form(self):
        # Onboarding and the group chat are sent by the existing confirmation
        # email after approval, so the template never links them.
        rendered = render_signup_reply("en", facts())
        assert rendered.count("http") == 1
        assert FORM in rendered


class TestVietnameseSignupCopy:
    def test_it_renders_the_proposed_copy(self):
        rendered = render_signup_reply("vi", facts())
        assert rendered.startswith(
            "Cảm ơn bạn rất nhiều vì đã nhắn tin và quan tâm đến việc làm tình "
            "nguyện viên cùng Vietnam Hearts 💚"
        )
        assert "trợ giảng hoặc đứng lớp" in rendered
        assert FORM in rendered

    def test_the_days_and_time_are_localised(self):
        rendered = render_signup_reply("vi", facts())
        assert "thứ Ba và thứ Năm" in rendered
        assert "09:30 đến 10:30" in rendered

    def test_the_diacritics_survive_rendering(self):
        assert "nguyện" in render_signup_reply("vi", facts())


class TestFactsComeFromSettings:
    def test_changing_the_teaching_days_changes_the_copy(self):
        # These are facts that will change, which is why they are settings and
        # not text.
        rendered = render_signup_reply(
            "en", facts(teaching_days="Monday, Wednesday, Friday")
        )
        assert "Mondays, Wednesdays and Fridays" in rendered

    def test_changing_the_class_time_changes_the_copy(self):
        rendered = render_signup_reply("en", facts(start="14:00", end="15:30"))
        assert "2:00-3:30pm" in rendered

    def test_changing_the_form_link_changes_the_copy(self):
        rendered = render_signup_reply("en", facts(link="https://example.com/form"))
        assert "https://example.com/form" in rendered

    def test_an_empty_form_link_refuses_to_render(self):
        # The whole point of the reply is the link. Sending the copy without it
        # would say "fill out the form below" and then show nothing.
        with pytest.raises(SignupReplyUnavailable):
            render_signup_reply("en", facts(link=""))

    def test_a_whitespace_only_form_link_also_refuses(self):
        with pytest.raises(SignupReplyUnavailable):
            render_signup_reply("en", facts(link="   "))


class TestLocaliseDays:
    @pytest.mark.parametrize(
        "days,expected",
        [
            ("Tuesday, Thursday", "Tuesdays and Thursdays"),
            ("Tuesday and Thursday", "Tuesdays and Thursdays"),
            ("Tuesday", "Tuesdays"),
            ("Monday, Wednesday, Friday", "Mondays, Wednesdays and Fridays"),
            # Week order, not the order they were typed: a set has none.
            ("Thursday, Tuesday", "Tuesdays and Thursdays"),
        ],
    )
    def test_english_day_lists(self, days, expected):
        assert localise_days(days, "en") == expected

    @pytest.mark.parametrize(
        "days,expected",
        [
            ("Tuesday, Thursday", "thứ Ba và thứ Năm"),
            ("Monday", "thứ Hai"),
            ("Saturday, Sunday", "thứ Bảy và Chủ Nhật"),
        ],
    )
    def test_vietnamese_day_lists(self, days, expected):
        assert localise_days(days, "vi") == expected

    def test_a_value_naming_no_weekday_falls_back_to_the_default(self):
        # Shares parse_teaching_days with the schedule sheet and the weekly
        # reminder, so the reply can never disagree with them.
        assert localise_days("whenever", "en") == "Tuesdays and Thursdays"

    def test_an_empty_value_falls_back_to_the_default(self):
        assert localise_days("", "en") == "Tuesdays and Thursdays"


class TestFormatClassTime:
    @pytest.mark.parametrize(
        "start,end,expected",
        [
            ("09:30", "10:30", "9:30-10:30am"),
            ("14:00", "15:30", "2:00-3:30pm"),
            ("11:30", "12:30", "11:30-12:30pm"),
            ("00:30", "01:30", "12:30-1:30am"),
        ],
    )
    def test_english_uses_the_captains_shape(self, start, end, expected):
        assert format_class_time(start, end, "en") == expected

    def test_vietnamese_uses_the_24_hour_clock(self):
        assert format_class_time("09:30", "10:30", "vi") == "09:30 đến 10:30"

    def test_an_unparseable_time_is_passed_through_rather_than_dropped(self):
        # Better to render a visibly odd time than to silently lose it.
        assert "half nine" in format_class_time("half nine", "10:30", "en")


class TestHoldingMessage:
    def test_the_english_copy_matches_the_parent_spec(self):
        assert render_holding_message("en") == (
            "Thanks for your message! I'm an automated assistant, and this one "
            "is better answered by a person. I've passed it to the Vietnam "
            "Hearts team and someone will follow up with you here."
        )

    def test_the_vietnamese_copy_matches_the_parent_spec(self):
        assert render_holding_message("vi") == (
            "Cảm ơn bạn đã nhắn tin! Mình là trợ lý tự động, và câu hỏi này nên "
            "được một thành viên trả lời trực tiếp. Mình đã chuyển cho đội ngũ "
            "Vietnam Hearts và sẽ có người phản hồi bạn tại đây."
        )

    def test_it_discloses_that_it_is_automated(self):
        # Rule 1 of the parent spec's copy rules: being told a human will follow
        # up only means something if you know you were not talking to one.
        assert "automated assistant" in render_holding_message("en")
        assert "trợ lý tự động" in render_holding_message("vi")

    @pytest.mark.parametrize("language", ["en", "vi"])
    def test_it_promises_no_timeframe(self, language):
        # Rule 2: Vietnam Hearts is volunteer-run and cannot commit to a window.
        copy = render_holding_message(language).lower()
        for forbidden in ("hour", "day", "week", "soon", "giờ", "ngày", "tuần"):
            assert forbidden not in copy


class TestLanguageFallback:
    @pytest.mark.parametrize("language", ["en", "vi"])
    def test_supported_languages_resolve_to_themselves(self, language):
        assert resolve_language(language) == language

    @pytest.mark.parametrize("language", ["other", "fr", "", None])
    def test_everything_else_falls_back_to_english(self, language):
        # derive_tier has already routed these to needs_admin, so a person will
        # follow up; the English copy is the best holding text meanwhile.
        assert resolve_language(language) == "en"

    def test_an_unsupported_language_gets_the_english_holding_message(self):
        assert render_holding_message("fr") == render_holding_message("en")

    def test_an_unsupported_language_gets_the_english_signup_copy(self):
        assert render_signup_reply("fr", facts()) == render_signup_reply("en", facts())


class TestSignature:
    def test_english_and_vietnamese_differ(self):
        assert signature("en") != signature("vi")

    @pytest.mark.parametrize("language", ["en", "vi"])
    def test_it_says_the_reply_is_automated(self, language):
        # The sign-up reply does not disclose it otherwise, and a reader
        # deserves to know either way.
        line = signature(language).lower()
        assert "automated" in line or "tự động" in line

    def test_it_uses_a_plain_hyphen(self):
        for language in ("en", "vi"):
            assert "—" not in signature(language)
            assert "–" not in signature(language)
