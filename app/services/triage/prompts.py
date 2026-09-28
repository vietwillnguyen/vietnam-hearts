"""The classifier instructions, written once and shared by both implementations.

Shared on purpose. The whole value of running a shadow classifier is that its
disagreements say something about the model; if the two were asked subtly
different questions, every disagreement would be about the wording instead.

The category instruction carries the parent spec's multi-topic rule: pick the
highest-tier category present, so a mail that asks about class times and also
offers a donation escalates as a donation rather than being answered as an FAQ.
"""

CATEGORY_INSTRUCTIONS = (
    "This is an email sent to the volunteer inbox of Vietnam Hearts, a "
    "volunteer-run charity in Ho Chi Minh City that teaches English to "
    "underprivileged children. Choose the single category that best describes "
    "what the sender wants.\n"
    "\n"
    "If the email covers more than one topic, choose the most serious one, "
    "using this order of seriousness, most serious first: safeguarding_legal, "
    "acceptance, sponsorship, donation, partnership, press, volunteer_ops, "
    "human_other, signup, faq.\n"
    "\n"
    "signup: the sender wants to volunteer, teach, or assist, and is asking "
    "how to join or offering their time.\n"
    "faq: a general question about the organisation, the classes, the "
    "location, the schedule, or what volunteering involves, from someone who "
    "is not reporting a problem.\n"
    "volunteer_ops: an existing volunteer writing about logistics, their own "
    "shift, swapping a class, materials, or attendance.\n"
    "sponsorship: an offer of, or a request about, sponsorship or funding.\n"
    "donation: an offer of, or a question about, donating money or goods.\n"
    "partnership: a company, school, NGO, or agency proposing to work "
    "together.\n"
    "press: a journalist, blogger, student researcher, or media request.\n"
    "safeguarding_legal: anything about a child's safety or welfare, abuse, a "
    "complaint about a person, a legal threat, a contract, insurance, or a "
    "data or privacy demand.\n"
    "acceptance: the sender is asking whether their application was accepted "
    "or rejected, or asking about the status of their application.\n"
    "human_other: a real person writing about something none of the above "
    "covers.\n"
    "automated: not written by a person for this inbox. Newsletters, "
    "marketing, receipts, delivery failures, calendar notifications, "
    "out-of-office replies, and security alerts.\n"
)

LANGUAGE_INSTRUCTIONS = (
    "What language did the sender write this email in? Answer en for English, "
    "vi for Vietnamese, and other for any other language. If the email mixes "
    "English and Vietnamese, answer with whichever language most of the "
    "sender's own words are in."
)

ASKS_FOR_HUMAN_INSTRUCTIONS = (
    "Is the sender explicitly asking to speak with, hear from, or be called "
    "by a person, or asking to be put in touch with someone specific? A "
    "general question that a person happens to be able to answer is not the "
    "same thing; answer no for that."
)

MONEY_INSTRUCTIONS = (
    "Does this email mention money, payment, fees, salary, a donation, "
    "sponsorship, an invoice, a contract, or a commitment that would bind "
    "Vietnam Hearts to something? Answer yes if it does, even in passing."
)

# The generator emits this verbatim when the retrieved context does not answer
# the question. Treated as NoRelevantContext, so a model that has correctly
# noticed it cannot answer never has that noticing rendered as an answer.
REFUSAL_SENTINEL = "INSUFFICIENT_CONTEXT"

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
