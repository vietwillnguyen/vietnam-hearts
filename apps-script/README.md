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

### If you changed `appsscript.json`, you are not done after the push

**A change to `intake/appsscript.json` that adds an OAuth scope, including enabling an advanced service, stops every installed trigger from firing until the owner opens the editor and re-authorizes the project.**
The push reports success. The script is deployed. The form keeps accepting submissions, and each one lands in the sheet with a Timestamp and **nothing else**: no `Status`, no acknowledgement to the person who submitted it, no notice to the team, and nothing anywhere saying it happened.
This is not hypothetical: it cost a real submission on the live pass that added the Drive advanced service, and the row sat unnoticed because a half-processed row looks exactly like a new one nobody has got to yet.

So whenever that file changes, immediately after `clasp push`, signed in as the shared team Gmail:

1. Apps Script editor -> run **`buildIntake`** -> accept the new permissions prompt.
   This re-authorizes the project, and because `buildIntake()` ends with `sweepUnstamped()`, the same run also repairs the rows that arrived while the triggers were down: each is stamped and its sender acknowledged, exactly as the trigger would have.
2. Check the sheet. **Every row with a `Timestamp` must have a `Status`.** A row with one and not the other is a person who has heard nothing.

`npm run verify` prints a loud reminder of this whenever `apps-script/intake/appsscript.json` differs from the default branch (`tools/scope-change-reminder.js`).
It is advice and never fails the run; a checkout with no base to compare against simply prints nothing.
If you ever need the repair on its own, `sweepUnstamped()` is exported and safe to run by hand - it only touches rows that have a `Timestamp` (`Dấu thời gian` on a Vietnamese sheet) and none of the three stamps the submit trigger writes, and it re-reads each row under the same document lock the trigger takes, so it cannot email anybody twice even when a submission arrives mid-sweep.

To check whether anyone edited in the browser:

```bash
cd apps-script/intake && npx clasp pull && git -C ../.. diff --exit-code
```

## The intake script

| File | What it is |
|---|---|
| `intake/Code.js` | The two trigger handlers, run on every submission and every edit |
| `intake/Build.js` | `buildIntake()`, the one-shot builder that creates the form, the sheet tabs and the triggers |
| `intake/test/harness.js` | Stub harness for the handlers: cases A to S against a fake `SpreadsheetApp` / `MailApp`, plus every `HEADER` needle against the real bilingual column titles |
| `intake/test/forbidden-shapes.js` | The address, opening-hours and reply-date shapes both harnesses assert never appear |
| `intake/test/build-harness.js` | Stub harness for the builder, including a second run, a degraded Google account, a lost form, a default tab that is not empty, a form somebody unpublished, a file upload question somebody added, and rows that arrived while the triggers were dead |
| `tools/scope-change-reminder.js` | The verify-time warning that a manifest change needs a re-authorization, with its own test under `tools/test/` |
| `intake/appsscript.json` | Runtime, exception logging, the time zone, and the one advanced service the project needs (Drive `v3`, for the form's responder permission) |
| `intake/.clasp.json.example` | Copy to `.clasp.json` and fill in the script id. The real file is gitignored because `clasp clone-script` writes it and the id differs per deployment |

### Setting the whole thing up from nothing

1. As the shared team Gmail, create a blank Google spreadsheet.
2. In it, **Extensions -> Apps Script**. Note the script id from the URL.
3. `cp intake/.clasp.json.example intake/.clasp.json` and paste that id in.
4. `cd intake && npx clasp push`.
5. In the Apps Script editor, run **`buildIntake`** once and accept the authorization prompt.
6. Read the email it sends to the team address. It carries the form's public URL, a pre-filled link template, the spreadsheet URL, and a numbered list of the steps Apps Script cannot do. It also asks, next to the form link, for the signed-out check in step 8: that email is the only set of instructions anybody gets.
7. Do those manual steps.
8. Check the form is reachable by a stranger, from a machine that is not signed in to Google, or with `curl`:

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' '<the form URL from the build email>'
   ```

   It must print `200`.
   A `401` means the form is serving a Google login wall instead of the questions: either it is not published and open to anyone with the link, or somebody has added a file upload question.
   Read the build email's manual steps - those two cases are the first ones on that list - fix it, and run the check again.
9. Submit a test response. Two `[DRY RUN]` emails should arrive at the team address, and the new row must get a `Status`.
   A row with a `Timestamp` and no `Status` means the triggers are not firing, which after a manifest change means the project needs re-authorizing: see the section above.
10. Set `CONFIG.DRY_RUN = false` in `Code.js`, `npm run verify`, `clasp push`. If that push carried a manifest change, run `buildIntake` once more and accept the prompt.

`buildIntake()` is idempotent and safe to re-run: it remembers the form it created in script properties, creates each tab only when absent, and rewrites the dropdowns, formats and protection every time, so a second run repairs a hand-edited sheet rather than duplicating it.
It will not re-add the form's questions once the form has any, because a volunteer may have reworded one.
It decides whether the form is linked by asking the form for its destination, never by whether a response tab exists, so a re-run after the form was lost cannot hand out a public URL whose submissions reach no sheet.
It re-asserts the form's publish state and its anyone-with-the-link responder permission every run, so re-running it is the one repair for a form somebody unpublished in the editor.

### What Apps Script cannot do, so a human must

`buildIntake()` collects these into the email it sends and counts them in the subject line.
They are listed in `Build.js` (`collectManualSteps_` and the `manual.push` calls), which is the source of truth; this is a summary.

1. **Publish to web.** There is no Apps Script API for it. Publish **only** the `Progress chart` tab or its chart, and later the `Donation tiers` tab the same way, never "Entire document" and never the response tab, which holds submitters' names, emails and phone numbers.
2. **The sharing list.** Share the spreadsheet only with the named volunteers who handle requests, as editors; never set General access to Anyone with the link; the response tab holds submitters' names, emails and Zalo/WhatsApp numbers.
3. **Filter views.** No API. One per working state plus one for `Priority = High`. Filter views are per-viewer, so two volunteers can filter differently at the same time; a plain filter is shared and they would fight over it.
4. **Email collection mode**, when the account's API does not expose it. It must be **Responder input**. Verified forces a Google sign-in, which is the biggest drop-off risk for a submitter on a phone, and the script needs that column before `setUp()` will install the triggers.
5. **Response receipts** must stay off. No API, and off is the default. The script's own acknowledgement is bilingual and carries the Zalo link; two confirmation emails for one submission looks broken.
6. **The `Volunteers` tab rows**, and the team Zalo or WhatsApp number in `Standing needs!B2`.
7. **An old response tab**, only when a re-run found a `Form Responses 1` tab that its form does not write to, typically because the remembered form was deleted.
   The build renames that tab to `Form Responses 1 (old form)` with every row kept, links the form to a fresh tab, and asks someone to decide what to do with the old rows.
8. **A default `Sheet1` tab that is not empty.** The build deletes Google's blank first tab, but only when it is untouched; anything typed into it is a volunteer's work, so that case becomes a step instead of a deletion.
9. **Deleting a file upload question somebody added**, which the build cannot do because Forms exposes no API for that item at all. This step is put **first**, above every other, for the reason in the next section.
10. **Publishing the form**, but only when the build could not do it from script. Also at the front of the list: first when it fires alone, and directly below the file upload step when both fire.

Three things used to be on this list and are not any more. The build now pins the spreadsheet's own **time zone** (`Asia/Ho_Chi_Minh`, since a new spreadsheet comes up on the owning account's zone - `Etc/GMT` on the first live build - and every `Next action by` date then reads a day off in Vietnam) and deletes the empty default tab.
The third, **adding a file upload question for photos**, was removed rather than automated: see below.

### Things to know before changing the script

- **Every bilingual string reads `English / Tiếng Việt`, in that order.** Question titles, help text, section titles, choice values, the consent line, the confirmation message, the two `Donation tiers` outcome columns and the bodies and subjects of every automated email.
  The order is a contract, not a preference: each `HEADER` needle in `Code.js` is an *English fragment* of a whole bilingual question title, so English leading is what keeps `col_()` resolving a column at all.
  Flip a string and you must re-check the needle that finds it.
  `test/harness.js` matches every needle against the real bilingual titles, and `test/build-harness.js` asserts the order as a property over every title, help text and choice value on the form, so a half-done flip fails rather than quietly unhooking a column.
- **The published chart shows percent funded, never amounts.** The `Progress` tab deliberately allows one currency per row, so raw totals on one axis compare a VND number with a USD one: the first live build stacked `Monthly rice fund (USD)` 12382 of 13000 against `Tết gifts 2027 (VND)` 400000 of 2600000, and the VND bar dwarfed the USD one while neither bar meant anything.
  A fraction is unit-free, so it is right whatever currency a row is in and needs no exchange rate that goes stale between the two tabs.
  The amounts travel in each bar's label instead.
  `Progress chart` columns `E:G` hold the filtered `QUERY` and are hidden; `A:C` are the label and the two fractions, and they are the only columns the chart reads.
  `A2` is one `FILTER` that spills only the rows the `QUERY` returned, because a formula padding every spare row with `""` would give the chart an empty bar per spare row.
  `buildIntake()` owns `A1:G30` and the chart: it clears that block and rewrites the formulas every run, like `All work`, so a sheet built by an earlier version converges too.
  The chart is updated in place rather than re-inserted, because a published chart's embed URL is keyed to its id and a new chart would blank the iframe on the website.
- **A public form has to be published AND opened to anyone with the link, and those are two different Google APIs.**
  `buildIntake()` asserts both on every run (`ensureFormPublished_`): `Form.setPublished(true)` through the Forms service, and a Drive permission of `type: anyone, role: reader, view: published` through the advanced Drive service.
  Since Google's 2025 publishing change a form created from a script satisfies neither by default, which is why the second live build handed the team a URL that answered `401` to anyone not signed in as an editor.
  The read that decides whether the permission already exists **must** pass `includePermissionsForView: 'published'`; Drive hides published-view permissions from an ordinary list, so without it the build would grant a second one every run.
  Either half failing becomes a manual step at the front of the list, because a form the public cannot open makes every other step pointless.
  This is also the one thing in the project that needs an advanced service: `intake/appsscript.json` enables Drive `v3`, which is what makes Apps Script request the Drive scope at the next authorization prompt.
- **Never put a file upload question on the form, and never ask a volunteer to add one.**
  Google requires a Google sign-in for *any* form that has one, for the whole form from page one - its own settings panel says so - so "optional and last in the supplies section" defers nothing.
  That question, which an earlier version of this README asked the team to add by hand, is what the second live build's login wall actually was.
  `FormApp.Form` has no `addFileUploadItem` either, so the builder cannot create one; what it does instead is detect one (`warnOnFileUploadItems_` over `form.getItems(FormApp.ItemType.FILE_UPLOAD)`) and put "delete it" first in the manual list, above even the publish step, because a published form carrying one is still unusable.
  Photos are asked for in words: the `Condition` question's help text, the acknowledgement email's supplies paragraph, and the `Standing needs!B2` handover block next to the team's Zalo or WhatsApp number.
  The acknowledgement's ask is in code, so it reaches every supplies submitter on any form; the other two are written only into a form and a `B2` the build creates, so on a form an earlier version built the delete step quotes the `Condition` help text (`CONDITION_PHOTO_HELP`) for the team to paste in, and asks for the `B2` photo line if that cell has none.
  The decision mails offer no Zalo or group link at all: they go to a member of the public and ask for a reply instead.
- **Columns are found by header text, not by index**, so adding a form question cannot break it. `col_()` prefers an exact header match over a substring one, which is what lets `Decision` and `Decision note` coexist.
- **A trigger that is installed is not the same as a trigger that is authorized.** The two ways this script goes quiet are different and both are silent: renaming `onIntakeEdit` to `onEdit` (below) makes it a simple trigger that cannot send mail, and a new OAuth scope makes every installed trigger refuse to run until somebody re-authorizes. The second leaves rows with a `Timestamp` and no `Status`; `sweepUnstamped()` is the repair and `buildIntake()` calls it last, after the triggers are installed.
- **`onIntakeEdit` must not be renamed to `onEdit`.** A function called `onEdit` is installed as a *simple* trigger, and simple triggers cannot send email. It would fail silently, with nothing in any log a volunteer would read. The name is the guard.
- **Only the `Decision` column can email a member of the public.** `Status` is the team's own task tracking and emails nobody, which is what lets one spreadsheet hold both intake rows and ordinary team tasks. Case E in `test/harness.js` is that property.
- **Nothing in the script approves anything.** The submit handler writes `Status`, `Priority` and `Next action by`; only a human sets `Decision`.
- **The always-needed list lives in the form's checkbox choices and nowhere else.** The script asks "did they tick anything" and "did they write anything else", both emptiness checks, so changing what the team always accepts is an edit to the form and nothing more.
- **The build owns `All work`!A1 and A2.** `buildIntake()` rewrites both formulas on every run, after the response tab has been renamed or gained columns, because Sheets rewrites references into a tab on both and a formula written earlier would read the wrong columns.
  To make the last column read a different question, change `allWorkQuery_` in `Build.js` rather than the cell, which the next run would overwrite.
- **`Reply sent` is an idempotency stamp.** While it is non-empty the script will not email that row again. Clearing it by hand is the one way to make it re-email somebody.
  Each row's read, send and stamp run under the document lock, and the row is read again once the lock is held, so two overlapping edits of one row send one reply.
  An edit that cannot get the lock within `CONFIG.DECISION_LOCK_WAIT_MS` sends nothing and reports the row.
- **The four branches end with the same question in the same words.** Google Forms creates one response column per *item*, so this really does produce four identically-headed columns, one per branch, each empty for the rows that took another branch. That is the cost of asking it everywhere, and it is worth paying: it is the only question that lets a submitter say the thing the form did not think to ask. The `All work` tab reads `Decision note` rather than one of those four, and no `HEADER` needle matches their wording, so `col_()` never sees the ambiguity.
- **No automated email states the school's address or hours, or promises a date.**
  The only place named is the public area, Thạnh Mỹ Tây, TP.HCM.
  All three are checked by the harness, on every mail and on the seeded handover block.
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
They prove the script's own logic, not that Google's API behaves as documented; a live test in a real sheet is still steps 8 and 9 above.

One trap if you add assertions: the script creates its `Date` objects inside the `vm` realm, so `value instanceof Date` is false against the host realm's constructor. Duck-type on `getTime()` instead.
