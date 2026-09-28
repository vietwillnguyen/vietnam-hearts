# Recorded classifier responses

Response bodies for the two triage implementations, committed as JSON so the
contract tests run against the shape the providers actually return rather than
against a mock shaped like our parsing code.

## Provenance

`jev_*.json` are `SystemOneResponse` bodies. Their structure is checked against
the installed `typesafe-sdk` itself: `test_triage_classifiers.py` loads each one
through `typesafe_sdk.SystemOneResponse.model_validate`, so a fixture that has
drifted from the vendor's model fails the test rather than passing a fake.

`litellm_*.json` are OpenAI-shaped `chat.completions` bodies as LiteLLM
normalises every provider onto, with the structured-output object in
`choices[0].message.content` as a JSON string.

The *values* in both are hand-written for the fixture. No live API call is made
in CI, and no real mail is represented: the subjects and bodies these responses
classify are the fictional ones in `../gmail_recorded/`.

Replace them with real recorded bodies once the Jev and Gemini keys exist, keeping
the filenames; the tests assert on parsed values, not on bytes.
