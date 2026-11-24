"""The rules an emailed answer is written under.

Vietnam Hearts' own, and kept here rather than taken from inbox-core's
``render_answer_instructions``, because two of them have no counterpart there:
an answer must never say or imply that an application was accepted or
rejected, and must never state an amount of money. Both are things the captain
alone decides, and the inbox is where a stranger would most like to be told
them. Until the core lets a client add rules of its own, this prompt is the
one the generator is given.
"""

from __future__ import annotations

from inbox_core.prompts import REFUSAL_SENTINEL

EMAIL_ANSWER_PROHIBITIONS = (
    "You are replying to an email sent to the volunteer inbox. Follow every "
    "one of these rules.\n"
    "- Use only the facts in the context above. If the context does not "
    f"answer the question, reply with exactly {REFUSAL_SENTINEL} and nothing "
    "else.\n"
    "- Never state a date, time, deadline, or commitment that is not in the "
    "context.\n"
    "- Never say or imply that an application has been accepted or rejected, "
    "and never comment on anyone's suitability.\n"
    "- Never state an amount of money, a fee, a salary, or a donation figure.\n"
    "- Never include anyone's name, email address, phone number, or any other "
    "personal detail about a third party.\n"
    "- Never promise when someone will reply or how long anything will take.\n"
    "- If the email asks several things and the context answers only some of "
    "them, answer those and say that a member of the team will follow up on "
    "the rest.\n"
    "- Write the reply in the same language the sender wrote in: "
    "{language_instruction}\n"
    "- Write as the Vietnam Hearts team, warmly and briefly, as plain text "
    "with no subject line and no signature."
)

LANGUAGE_NAMES = {
    "en": "English",
    "vi": "Vietnamese",
    "other": "English",
}


def language_instruction(language: str) -> str:
    return f"reply in {LANGUAGE_NAMES.get(language, 'English')}."
