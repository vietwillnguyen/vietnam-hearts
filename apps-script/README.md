# Apps Script projects

Google Apps Script that lives inside the team's Google Sheets, kept in this repository rather than only in the browser editor.

A script that exists only in the Apps Script editor has no history, no review and no blame.
When the emails stop going out there is no way to tell what changed or when.
Everything here is plain JavaScript, which is what the Apps Script editor speaks natively, and it is pushed with [`clasp`](https://github.com/google/clasp).

## What is here

| Directory | Bound to | What it does |
|---|---|---|
| `intake/` | the "Vietnam Hearts - Requests" spreadsheet | Public intake for donation offers, partnership proposals and event ideas. See [`intake/`](#the-intake-script) below. |

## This changes nothing about the deployed service

The intake runs entirely in Apps Script, inside the spreadsheet, and deploys with `clasp push`.
It adds nothing to the Cloud Run service, nothing to the container image, and no new infrastructure of any kind.
`.gcloudignore` and `.dockerignore` both exclude this directory so it never reaches a build context.

The one URL here that points at the Cloud Run service is the public privacy page the form's consent line links to:
<https://vietnam-hearts-automation-367619842919.northamerica-northeast1.run.app/privacy>.
That page is served by the FastAPI app and is public on purpose (`app/routers/public.py`).
`vietnamhearts.org` is a separate Hostinger site and has no `/privacy` page, so do not write one into the form.

## Two rules, because both failures are quiet

1. **The repository is the source of truth. The browser editor is for running functions and reading execution logs, not for edits.**
   `clasp push` overwrites the server copy wholesale, so a typo fixed in the browser disappears the next time anyone pushes.
   If somebody did edit there, run `clasp pull` first and commit what they did.
2. **`~/.clasprc.json` is a credential. Never commit it, and never put it in CI.**
   It is a refresh token for the account that owns the team's Gmail, the forms, the sheets and the service account.
   This repository is public and already carries an issue for a leaked OAuth client secret; the same mistake with a clasp token would be worse, because the token is for the whole account rather than for one client.
   Nothing in `.github/workflows/apps-script.yml` logs in, pushes, or needs a secret.

## Setup, once per machine

`clasp` is not an officially supported Google product, so the version is pinned.

```bash
cd apps-script
npm install                      # installs the pinned @google/clasp
# Enable the Apps Script API for the shared team account, once:
#   https://script.google.com/home/usersettings
npx clasp login                  # browser flow; writes ~/.clasprc.json
```

Log in as the **shared team Gmail**, not a personal account.
Everything the script does runs as whoever owns the Apps Script project: the emails come from that mailbox, and the daily send quota is that account's.

## Deploying a change

```bash
cd apps-script
npm run verify                   # node --check plus both stub harnesses
cd intake && npx clasp push      # this IS the deploy
```

**`clasp push` is the deploy.**
A container-bound trigger script needs no deployment or version: the installable triggers run whatever code is currently saved, so a successful push is live immediately.
That is worth knowing in both directions - it is why there is no release step, and why a bad push is in production the moment it lands.

To check whether anyone edited in the browser:

```bash
cd apps-script/intake && npx clasp pull && git -C ../.. diff --exit-code
```

## The intake script

| File | What it is |
|---|---|
| `intake/Code.js` | The two trigger handlers, run on every submission and every edit |
| `intake/Build.js` | `buildIntake()`, the one-shot builder that creates the form, the sheet tabs and the triggers |
| `intake/test/harness.js` | Stub harness for the handlers: twelve cases against a fake `SpreadsheetApp` / `MailApp` |
| `intake/test/build-harness.js` | Stub harness for the builder, including a second run and a degraded Google account |
| `intake/appsscript.json` | Runtime, exception logging, and the time zone |
| `intake/.clasp.json.example` | Copy to `.clasp.json` and fill in the script id. The real file is gitignored because `clasp clone-script` writes it and the id differs per deployment |

### Setting the whole thing up from nothing

1. As the shared team Gmail, create a blank Google spreadsheet.
2. In it, **Extensions -> Apps Script**. Note the script id from the URL.
3. `cp intake/.clasp.json.example intake/.clasp.json` and paste that id in.
4. `cd intake && npx clasp push`.
5. In the Apps Script editor, run **`buildIntake`** once and accept the authorization prompt.
6. Read the email it sends to the team address. It carries the form's public URL, a pre-filled link template, the spreadsheet URL, and a numbered list of the steps Apps Script cannot do.
7. Do those manual steps.
8. Submit a test response. Two `[DRY RUN]` emails should arrive at the team address.
9. Set `CONFIG.DRY_RUN = false` in `Code.js`, `npm run verify`, `clasp push`.

`buildIntake()` is idempotent and safe to re-run: it remembers the form it created in script properties, creates each tab only when absent, and rewrites the dropdowns, formats and protection every time, so a second run repairs a hand-edited sheet rather than duplicating it.
It will not re-add the form's questions once the form has any, because a volunteer may have reworded one.

### What Apps Script cannot do, so a human must

`buildIntake()` collects these into the email it sends and counts them in the subject line.
They are listed in `Build.js` (`collectManualSteps_` and the `manual.push` calls), which is the source of truth; this is a summary.

1. **Publish to web.** There is no Apps Script API for it. Publish **only** the `Progress chart` tab or its chart, never "Entire document" and never the response tab, which holds submitters' names, emails and phone numbers.
2. **Filter views.** No API. One per working state plus one for `Priority = High`. Filter views are per-viewer, so two volunteers can filter differently at the same time; a plain filter is shared and they would fight over it.
3. **Email collection mode**, when the account's API does not expose it. It must be **Responder input**. Verified forces a Google sign-in, which is the biggest drop-off risk for a submitter on a phone, and the script needs that column before `setUp()` will install the triggers.
4. **Response receipts** must stay off. No API, and off is the default. The script's own acknowledgement is bilingual and carries the Zalo link; two confirmation emails for one submission looks broken.
5. **File upload questions**, on accounts where `addFileUploadItem` is unavailable. Keep it optional and last in the supplies section so nobody meets the sign-in wall before their answers are saved.
6. **The spreadsheet's own time zone**: File -> Settings -> `Asia/Ho Chi Minh`, to match `appsscript.json`.
7. **The `Volunteers` tab rows**, and the team Zalo or WhatsApp number in `Standing needs!B2`.

### Things to know before changing the script

- **Columns are found by header text, not by index**, so adding a form question cannot break it. `col_()` prefers an exact header match over a substring one, which is what lets `Decision` and `Decision note` coexist.
- **`onIntakeEdit` must not be renamed to `onEdit`.** A function called `onEdit` is installed as a *simple* trigger, and simple triggers cannot send email. It would fail silently, with nothing in any log a volunteer would read. The name is the guard.
- **Only the `Decision` column can email a member of the public.** `Status` is the team's own task tracking and emails nobody, which is what lets one spreadsheet hold both intake rows and ordinary team tasks. Case E in `test/harness.js` is that property.
- **Nothing in the script approves anything.** The submit handler writes `Status`, `Priority` and `Next action by`; only a human sets `Decision`.
- **The always-needed list lives in the form's checkbox choices and nowhere else.** The script asks "did they tick anything" and "did they write anything else", both emptiness checks, so changing what the team always accepts is an edit to the form and nothing more.
- **`Reply sent` is an idempotency stamp.** While it is non-empty the script will not email that row again. Clearing it by hand is the one way to make it re-email somebody.
- **The four branches end with the same question in the same words.** Google Forms creates one response column per *item*, so this really does produce four identically-headed columns, one per branch, each empty for the rows that took another branch. That is the cost of asking it everywhere, and it is worth paying: it is the only question that lets a submitter say the thing the form did not think to ask. The `All work` tab reads `Decision note` rather than one of those four, and no `HEADER` needle matches their wording, so `col_()` never sees the ambiguity.
- **No automated email states the school's address or hours, or promises a date.**
  The only place named is the public area, Thạnh Mỹ Tây, TP.HCM.
  Both are checked by the harness.
- **No automated email contains a phone number.** The `Volunteers` tab maps a name to an email and the script reads nothing else from it. The harness asserts this across every message both handlers produce.

### Running the harnesses

```bash
cd apps-script && npm run verify
```

Or directly, which is what CI does:

```bash
node --check apps-script/intake/Code.js
node --check apps-script/intake/Build.js
node apps-script/intake/test/harness.js apps-script/intake/Code.js
node apps-script/intake/test/build-harness.js apps-script/intake/Code.js apps-script/intake/Build.js
```

Each harness runs the real file in a `vm` context against fake Google services.
No account, no network, no credential, which is why CI can run them.
They prove the script's own logic, not that Google's API behaves as documented; a live test in a real sheet is still step 8 above.

One trap if you add assertions: the script creates its `Date` objects inside the `vm` realm, so `value instanceof Date` is false against the host realm's constructor. Duck-type on `getTime()` instead.
