"""BotService must refuse rather than answer without grounding."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.bot_service import (
    BotService,
    GenerationUnavailable,
    NoRelevantContext,
)
from app.services.knowledge_service import EmbeddingsUnavailable


def _bot(similarity_search) -> BotService:
    bot = BotService.__new__(BotService)
    bot.knowledge_service = MagicMock()
    bot.knowledge_service.similarity_search = similarity_search
    bot.document_service = MagicMock()
    bot.supabase = MagicMock()
    return bot


def _grounded_bot(gemini_client) -> BotService:
    bot = _bot(AsyncMock(return_value=[{"content": "some text", "similarity": 0.8}]))
    bot.knowledge_service.gemini_client = gemini_client
    return bot


class TestChatRefusesInsteadOfGuessing:
    @pytest.mark.asyncio
    async def test_propagates_embeddings_unavailable(self):
        bot = _bot(AsyncMock(side_effect=EmbeddingsUnavailable("down")))
        with pytest.raises(EmbeddingsUnavailable):
            await bot.chat("how do I volunteer")

    @pytest.mark.asyncio
    async def test_raises_when_no_relevant_chunks_are_found(self):
        bot = _bot(AsyncMock(return_value=[]))
        with pytest.raises(NoRelevantContext):
            await bot.chat("how do I volunteer")

    @pytest.mark.asyncio
    async def test_raises_when_the_gemini_client_is_missing(self):
        bot = _grounded_bot(None)
        with pytest.raises(GenerationUnavailable):
            await bot.chat("how do I volunteer")

    @pytest.mark.asyncio
    async def test_raises_when_the_generation_call_fails(self):
        client = MagicMock()
        client.models.generate_content.side_effect = RuntimeError(
            "429 RESOURCE_EXHAUSTED"
        )
        bot = _grounded_bot(client)
        with pytest.raises(GenerationUnavailable):
            await bot.chat("how do I volunteer")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("generated", [None, "", "   \n  "])
    async def test_raises_when_the_generated_text_is_empty(self, generated):
        client = MagicMock()
        client.models.generate_content.return_value = MagicMock(text=generated)
        bot = _grounded_bot(client)
        with pytest.raises(GenerationUnavailable):
            await bot.chat("how do I volunteer")

    @pytest.mark.asyncio
    async def test_never_returns_a_keyword_matched_canned_answer(self):
        # The historical failure: an unreachable knowledge base still produced
        # "you don't need a formal teaching certificate" as a confident claim.
        bot = _bot(AsyncMock(side_effect=EmbeddingsUnavailable("down")))
        with pytest.raises(EmbeddingsUnavailable):
            await bot.chat("do I need a teaching certificate")

    def test_the_canned_response_helpers_are_gone(self):
        assert not hasattr(BotService, "_generate_simple_response")
        assert not hasattr(BotService, "_generate_fallback_response")


class TestFailureModesStaySeparable:
    # Phase 1 dispatches on exception type, so a generation outage, a
    # retrieval outage and a knowledge base gap have to stay catchable
    # independently. Making any of these a subclass of another would silently
    # re-merge them at every existing call site.
    def test_no_failure_mode_subclasses_another(self):
        modes = (EmbeddingsUnavailable, NoRelevantContext, GenerationUnavailable)
        for raised in modes:
            for caught in modes:
                if raised is not caught:
                    assert not issubclass(raised, caught)


class TestChatSucceeds:
    @pytest.mark.asyncio
    async def test_returns_the_top_similarity_as_confidence(self):
        chunks = [
            {"content": "a", "similarity": 0.42, "source_document_id": "faq"},
            {"content": "b", "similarity": 0.81, "source_document_id": "faq"},
        ]
        bot = _bot(AsyncMock(return_value=chunks))
        client = MagicMock()
        client.models.generate_content.return_value = MagicMock(
            text="  Grounded answer.  "
        )
        bot.knowledge_service.gemini_client = client

        result = await bot.chat("how do I volunteer")

        assert result["response"] == "Grounded answer."
        assert result["confidence"] == 0.81
        assert result["context_used"] == 2
        assert result["sources"] == ["faq", "faq"]


class TestEmptyContextNeverReachesTheGenerator:
    """The phase-0 carry-over.

    ``_build_context`` swallows its own errors and returns an empty string, and
    it also returns one when every retrieved chunk has empty content. Reaching
    the generator with that is how an answer gets invented from the question
    alone - the exact failure this whole path exists to prevent.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "chunks",
        [
            [{"content": "", "similarity": 0.9}],
            [{"content": "   \n  ", "similarity": 0.9}],
            [{"similarity": 0.9}],
            [{"content": "", "similarity": 0.9}, {"content": "  ", "similarity": 0.8}],
        ],
    )
    async def test_chunks_that_assemble_to_nothing_raise(self, chunks):
        client = MagicMock()
        bot = _bot(AsyncMock(return_value=chunks))
        bot.knowledge_service.gemini_client = client

        with pytest.raises(NoRelevantContext):
            await bot.chat("how do I volunteer")

        client.models.generate_content.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_build_context_failure_raises_rather_than_guessing(self):
        client = MagicMock()
        bot = _grounded_bot(client)
        bot._build_context = MagicMock(return_value="")

        with pytest.raises(NoRelevantContext):
            await bot.chat("how do I volunteer")

        client.models.generate_content.assert_not_called()


class TestTheRefusalSentinel:
    """The model's own "I cannot answer this" has somewhere to go.

    Without this, a correctly hesitant model has its hesitation rendered to a
    stranger as though it were an answer.
    """

    @pytest.mark.asyncio
    async def test_the_sentinel_in_the_generated_text_raises(self):
        from app.services.triage.prompts import REFUSAL_SENTINEL

        client = MagicMock()
        client.models.generate_content.return_value = MagicMock(text=REFUSAL_SENTINEL)
        bot = _grounded_bot(client)

        with pytest.raises(NoRelevantContext):
            await bot.chat("what are the class times")

    @pytest.mark.asyncio
    async def test_the_sentinel_anywhere_in_the_text_raises(self):
        from app.services.triage.prompts import REFUSAL_SENTINEL

        client = MagicMock()
        client.models.generate_content.return_value = MagicMock(
            text=f"I am not sure. {REFUSAL_SENTINEL}"
        )
        bot = _grounded_bot(client)

        with pytest.raises(NoRelevantContext):
            await bot.chat("what are the class times")

    @pytest.mark.asyncio
    async def test_an_ordinary_answer_is_unaffected(self):
        client = MagicMock()
        client.models.generate_content.return_value = MagicMock(
            text="Classes run on Tuesdays and Thursdays."
        )
        bot = _grounded_bot(client)

        result = await bot.chat("what are the class times")
        assert result["response"] == "Classes run on Tuesdays and Thursdays."


class TestTheEmailPromptCarriesEveryProhibition:
    def _prompt(self, language: str = "en") -> str:
        bot = BotService.__new__(BotService)
        return bot._build_prompt(
            "How do I sign up?",
            "Volunteers do not need a certificate.",
            channel="email",
            language=language,
        )

    @pytest.mark.parametrize(
        "prohibition",
        [
            # One row per rule in the design's Answering section. A missing one
            # is a specific harm, not a style regression.
            "Never state a date",
            "Never say or imply that an application has been accepted",
            "Never state an amount of money",
            "Never include anyone's name",
            "Never promise when someone will reply",
            "a member of the team will follow up on the rest",
        ],
    )
    def test_the_prohibition_is_present(self, prohibition):
        assert prohibition in self._prompt()

    def test_the_refusal_sentinel_is_offered_to_the_model(self):
        from app.services.triage.prompts import REFUSAL_SENTINEL

        assert REFUSAL_SENTINEL in self._prompt()

    @pytest.mark.parametrize(
        "language,expected",
        [("en", "English"), ("vi", "Vietnamese"), ("other", "English")],
    )
    def test_the_sender_language_instruction_is_present(self, language, expected):
        prompt = self._prompt(language)
        assert "same language the sender wrote in" in prompt
        assert expected in prompt

    def test_the_web_prompt_is_left_alone(self):
        # The stricter rules exist because an email reply is an unsupervised
        # written statement to a stranger. A chat turn can be corrected in the
        # next one.
        bot = BotService.__new__(BotService)
        web = bot._build_prompt("How do I sign up?", "some context")

        assert "Never state an amount of money" not in web
        assert "Context:\nsome context" in web

    def test_the_context_and_question_still_reach_the_model(self):
        prompt = self._prompt()
        assert "Volunteers do not need a certificate." in prompt
        assert "How do I sign up?" in prompt


class TestChatStillHonoursItsOldContract:
    @pytest.mark.asyncio
    async def test_the_top_similarity_is_still_the_confidence(self):
        # The confidence gate compares this against ANSWER_THRESHOLD, so a
        # change here silently moves the gate.
        chunks = [
            {"content": "a", "similarity": 0.42, "source_document_id": "faq"},
            {"content": "b", "similarity": 0.81, "source_document_id": "faq"},
        ]
        client = MagicMock()
        client.models.generate_content.return_value = MagicMock(text="An answer.")
        bot = _bot(AsyncMock(return_value=chunks))
        bot.knowledge_service.gemini_client = client

        result = await bot.chat("how do I volunteer", channel="email", language="en")
        assert result["confidence"] == 0.81
        assert result["sources"] == ["faq", "faq"]

    @pytest.mark.asyncio
    async def test_the_channel_and_language_are_keyword_only(self):
        # So no existing positional call site can accidentally pass a language
        # where user_context is expected.
        import inspect

        signature = inspect.signature(BotService.chat)
        assert signature.parameters["channel"].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters["language"].kind is inspect.Parameter.KEYWORD_ONLY

    @pytest.mark.asyncio
    async def test_the_existing_two_argument_call_still_works(self):
        client = MagicMock()
        client.models.generate_content.return_value = MagicMock(text="An answer.")
        bot = _grounded_bot(client)

        result = await bot.chat("how do I volunteer", {"source": "web"})
        assert result["response"] == "An answer."
