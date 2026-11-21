# Recorded Gmail payloads

Anonymised `users.messages.get(format="full")` and `users.threads.get` resources,
committed as JSON and used by the adapter and guard tests.

## Provenance, and what still has to change

The plan's live end-to-end protocol says these are captured from the **throwaway**
test inbox during the E1 live run and anonymised before being committed.
That run has not happened yet: it needs a throwaway Gmail account with its own
OAuth consent, which does not exist (see the "Captain actions" section of the E1
pull request).

Every file here is therefore **hand-built to the published Gmail API contract**
rather than captured from a live mailbox.
They are shaped from Google's own reference for the `Message` resource, and
`tests/fixtures/gmail_payloads.py` generates the same structure programmatically.

When the throwaway account exists, replace these files with real captured
payloads, anonymised, and keep the filenames so no test has to change.
The tests assert on structure and on extracted values, not on the exact bytes.

## The anonymisation rule

No real volunteer's name, address, or message text ever appears here, in any
form, captured or hand-built.
Addresses use `example.com`, which RFC 2606 reserves for documentation.
Bodies are written for the fixture.
The signup sheet - passport numbers, dates of birth, addresses - is never a
source for anything in this directory.
