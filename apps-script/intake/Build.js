/**
 * Vietnam Hearts - public request intake, one-shot builder.
 *
 * buildIntake() creates everything the intake design needs, so nobody has to
 * build it by hand: the Google Form with its four branches, the response tab
 * and every working tab, the dropdowns, the overdue flag, the protection, the
 * progress chart, and the two triggers. Then it emails the team the form URL,
 * a pre-filled link template, the spreadsheet URL and the handful of steps
 * Apps Script genuinely cannot do.
 *
 * HOW TO RUN IT
 *   1. As the shared team Gmail, make a blank spreadsheet.
 *   2. Extensions -> Apps Script, push this project with clasp, then run
 *      buildIntake() once and accept the authorization prompt.
 *   3. Read the email it sends. Do the numbered manual steps in it.
 *
 * It is IDEMPOTENT and safe to re-run. The form it created is remembered in
 * script properties, every tab is created only when absent, and every
 * dropdown, format and protection is rewritten from scratch each run - so a
 * second run repairs a hand-edited sheet instead of duplicating it. What it
 * will NOT do on a second run is re-add the form's questions: a form with
 * questions is left exactly as it is, because a volunteer may have reworded
 * one, and the form is the source of truth for the always-needed list
 * (see the report, section 7.5.2).
 *
 * It creates NOTHING outside this spreadsheet and the one form: no Cloud Run
 * change, no website change, no Instagram change. The intake runs entirely in
 * Apps Script inside the sheet.
 */

/* Every bilingual string in this file and in Code.js reads "English / Tiếng
   Việt", in that order: question titles, help text, section titles, choice
   values, the consent line and the confirmation message. Two reasons the order
   is a contract rather than a preference. The team reads English first, and -
   less obviously - every HEADER needle in Code.js is an English fragment, so
   English leading is what keeps col_() resolving a column whose header is the
   whole bilingual question title. Flip a string and you must re-check the
   needle that finds it; test/harness.js matches all of them against the real
   titles for exactly that reason. */

/** The spreadsheet's own time zone. A new spreadsheet comes up on whatever
 *  zone the owning account has - Etc/GMT on the first live build - and the
 *  "Next action by" dates would then read a day off to the team in Vietnam.
 *  `appsscript.json` pins the SCRIPT's zone; this pins the SHEET's. */
var SHEET_TIME_ZONE = 'Asia/Ho_Chi_Minh';

/** Properties that make a re-run idempotent. */
var PROP_FORM_ID = 'intakeFormId';

/** The one URL in the form that points outside Google. It is the FastAPI
 *  app's public privacy page, served by the Cloud Run service - NOT by
 *  vietnamhearts.org, which is a separate Hostinger site with no /privacy
 *  page. Do not invent a vietnamhearts.org URL here. */
var PRIVACY_URL = 'https://vietnam-hearts-automation-367619842919.northamerica-northeast1.run.app/privacy';

/** The team's working columns, inserted to the left of the form's own.
 *  Order is the contract: the report documents A through G. */
var TEAM_COLUMNS = [
  'Status',
  'Priority',
  'Next action by',
  'Owner',
  'Decision',
  'Decision note',
  'Reply sent'
];

/** Two branch choice values that other code matches on, so they are named
 *  rather than retyped: the pre-filled link template picks the partnership
 *  branch by value, and Code.js tests the supplies branch with the English
 *  fragment "Donate supplies". */
var SUPPLIES_CHOICE = 'Donate supplies or goods / Tặng đồ dùng, vật phẩm';
var PARTNERSHIP_CHOICE = 'A long-term partnership / Hợp tác dài hạn';

var STATUS_VALUES = ['TODO', 'IN PROGRESS', 'BLOCKED - WAITING ON ANOTHER PARTY', 'DONE'];
var PRIORITY_VALUES = ['High', 'Normal', 'Low'];
var DECISION_CHOICES = ['Approved', 'Declined'];

/** Tab names. CONFIG in Code.js owns the three the handlers read; everything
 *  else is named here so there is one list to look at. */
var TABS = {
  teamTasks: 'Team tasks',
  allWork: 'All work',
  standingNeeds: 'Standing needs',
  volunteers: 'Volunteers',
  tiers: 'Donation tiers',
  progress: 'Progress',
  progressChart: 'Progress chart'
};

/** The always-needed list. This is the ONLY place the five items are written
 *  down, and they are written into the form's checkbox choices, never into a
 *  constant the handlers read. Code.js asks "did they tick anything", so the
 *  list can change by editing the form and nothing else. */
var STANDING_ITEMS = [
  'Cooking oil / Dầu ăn',
  'Sugar / Đường',
  'Small milk cartons / Sữa hộp nhỏ',
  'Fish sauce / Nước mắm',
  'Seasoning powder, MSG / Bột nêm, bột ngọt'
];

/** Asked at the end of every branch, in exactly these words on purpose.
 *  Google Forms still makes one sheet column per item, so this produces four
 *  identically headed columns, one per branch, and each row fills only one. */
var CLOSING_QUESTION =
  'What do you need from us, or anything else we should know? / ' +
  'Bạn cần gì từ Vietnam Hearts, hoặc có điều gì khác muốn cho chúng tôi biết?';

/** The photo ask on the supplies section's Condition question. The build sets
 *  it on a new form, and the delete-file-upload manual step quotes it for a
 *  form an earlier version built, so both read this one string. */
var CONDITION_PHOTO_HELP =
  'Photos help a lot - please do not attach them here. After you submit, reply ' +
  'to our acknowledgement email with them, or send them to us on Zalo using the ' +
  'link at the end of that email. / Ảnh giúp chúng tôi rất nhiều - xin đừng đính ' +
  'kèm ảnh ở đây. Sau khi gửi biểu mẫu, bạn hãy trả lời email xác nhận của chúng ' +
  'tôi kèm theo ảnh, hoặc gửi ảnh qua Zalo bằng đường dẫn ở cuối email đó.';

/** The photo line in each half of the Standing needs B2 block, each under that
 *  half's number line. The build seeds B2 with them and the delete-file-upload
 *  step quotes them for a B2 that predates them. */
var TERMS_PHOTO_LINE_EN =
  'Photos of what you are giving are welcome on that number - please do not attach them to the form.';
var TERMS_PHOTO_LINE_VI =
  'Rất hoan nghênh bạn gửi ảnh đồ tặng qua số đó - xin đừng đính kèm ảnh vào biểu mẫu.';

/* ------------------------------------------------------------------ build */

function buildIntake() {
  var ss = SpreadsheetApp.getActive();
  var manual = [];
  var notes = [];

  // First, before any date is written anywhere.
  setSheetTimeZone_(ss, notes, manual);

  var form = openOrCreateForm_(notes, manual);

  // Both of these can only end in a manual step that has to come FIRST, and
  // the later unshift wins the front, so the order of these two calls is the
  // order of their steps: a form carrying a file upload question shows a
  // sign-in wall even when it is correctly published, so that one goes above.
  ensureFormPublished_(form, notes, manual);
  warnOnFileUploadItems_(form, manual);

  linkFormToSheet_(form, ss, notes, manual);

  var responses = ss.getSheetByName(CONFIG.SHEET_NAME);
  if (!responses) {
    throw new Error('The form was linked but no "' + CONFIG.SHEET_NAME +
      '" tab appeared. Stop and check the form\'s response destination.');
  }

  addTeamColumns_(responses, notes);
  buildStandingNeedsTab_(ss, notes);
  var volunteers = buildVolunteersTab_(ss, notes);
  applyTeamColumnFormatting_(responses, volunteers);
  buildTeamTasksTab_(ss, notes);
  buildAllWorkTab_(ss, notes);
  buildTiersTab_(ss, notes);
  buildProgressTabs_(ss, notes, manual);
  removeDefaultTabIfEmpty_(ss, notes, manual);

  // The triggers last, so nothing can fire against a half-built sheet.
  //
  // setUp() refuses to install anything unless every column it needs resolves,
  // which is right - but it must never stop this function from reporting,
  // because the email below is the only set of instructions anybody gets. A
  // build that dies here leaves a volunteer with a half-built sheet and no
  // idea what to do next, which is the worst of the available outcomes.
  try {
    setUp();
    notes.push('Installed the onIntakeFormSubmit and onIntakeEdit triggers.');
  } catch (err) {
    notes.push('Triggers NOT installed: ' + err.message);
    manual.push('Do the form settings steps above first, then run **setUp()** from ' +
      'the Apps Script editor, or simply run buildIntake() again. It would not install ' +
      'the triggers yet because a column it needs is missing: "' + err.message +
      '". Until that succeeds a submission is still recorded in the sheet, but ' +
      'nobody is emailed - not the submitter and not the team.');
  }

  // After the triggers, and last of all, because this is the step that needs
  // them: a new OAuth scope pushed with clasp leaves every installed trigger
  // dead until somebody opens the editor and re-authorizes, and running
  // buildIntake() is what re-authorizing looks like. Live pass 3 lost a real
  // submission that way, so the run that fixes the cause also repairs the
  // rows it cost. Never fatal - a build that dies here would take the
  // instructions email down with it.
  try {
    var repaired = sweepUnstamped();
    if (repaired) {
      notes.push('Processed ' + repaired + ' submission' +
        (repaired === 1 ? '' : 's') + ' that arrived while the triggers were ' +
        'not running: each row was stamped and its sender acknowledged, the ' +
        'same way the form-submit trigger would have.');
    }
  } catch (err) {
    notes.push('Could not check for submissions missed while the triggers ' +
      'were down: ' + plainReason_(err));
  }

  collectManualSteps_(manual);
  reportBuild_(form, ss, notes, manual);
  return { formUrl: form.getPublishedUrl(), sheetUrl: ss.getUrl() };
}

/** Pin the spreadsheet's time zone. Reported as a note, not asked for as a
 *  manual step, because Apps Script can simply do it - and a volunteer who
 *  skips the step gets dates that are quietly a day out rather than visibly
 *  broken. Only becomes a manual step if the call itself fails. */
function setSheetTimeZone_(ss, notes, manual) {
  try {
    var before = String(ss.getSpreadsheetTimeZone() || '');
    if (before === SHEET_TIME_ZONE) {
      notes.push('The spreadsheet time zone is already ' + SHEET_TIME_ZONE + '.');
      return;
    }
    ss.setSpreadsheetTimeZone(SHEET_TIME_ZONE);
    notes.push('Set the spreadsheet time zone to ' + SHEET_TIME_ZONE +
      ' (it was ' + (before || 'unset') + '). On any other zone every ' +
      '"Next action by" date reads a day off to the team in Vietnam.');
  } catch (err) {
    manual.push('In the spreadsheet: File -> Settings -> **Time zone: ' +
      'Asia/Ho Chi Minh**, to match the script. This build could not set it (' +
      err + '). On any other zone every "Next action by" date reads a day off ' +
      'to the team in Vietnam.');
  }
}

/** Google's own blank first tab. A volunteer starts from a blank spreadsheet,
 *  so "Sheet1" is always there and is dead weight once the build has made its
 *  own tabs - the first live build left it sitting in front of them.
 *
 *  Deleted only when it is genuinely untouched and only when it is a default
 *  name, so a tab a volunteer renamed or typed into is never destroyed by a
 *  re-run; that case becomes a manual step instead. Never the last tab left. */
function removeDefaultTabIfEmpty_(ss, notes, manual) {
  var sheets = ss.getSheets();
  if (sheets.length < 2) return;
  for (var i = 0; i < sheets.length; i++) {
    var sh = sheets[i];
    var name = sh.getName();
    // "Trang tính1" is the same tab on a Vietnamese-locale account.
    if (!/^(?:Sheet|Trang tính)\s?\d+$/.test(name)) continue;
    if (sh.getLastRow() !== 0 || sh.getLastColumn() !== 0 ||
        sh.getCharts().length !== 0) {
      notes.push('Left the default "' + name + '" tab alone: it is not empty.');
      manual.push('In the spreadsheet: the default "' + name + '" tab has ' +
        'something on it, so this build kept it rather than deleting a ' +
        'volunteer\'s work. Move anything worth keeping onto one of the named ' +
        'tabs, then delete it by hand.');
      continue;
    }
    try {
      ss.deleteSheet(sh);
      notes.push('Deleted the empty default "' + name + '" tab.');
    } catch (err) {
      notes.push('Could not delete the empty default "' + name + '" tab: ' + err);
    }
    return;
  }
}

/* ------------------------------------------------------------------- form */

function openOrCreateForm_(notes, manual) {
  var props = PropertiesService.getScriptProperties();
  var id = props.getProperty(PROP_FORM_ID);

  if (id) {
    try {
      var existing = FormApp.openById(id);
      notes.push('Reused the form this script already created: "' +
        existing.getTitle() + '".');
      if (existing.getItems().length === 0) {
        addFormItems_(existing, manual);
        notes.push('That form was empty, so its questions were added.');
      } else {
        notes.push('Its ' + existing.getItems().length + ' questions were left ' +
          'untouched: the form is the source of truth for the always-needed list, ' +
          'so this builder never rewrites a form that already has questions.');
      }
      return existing;
    } catch (err) {
      notes.push('Script properties named form ' + id + ' but it could not be ' +
        'opened (' + err + '), so a new form was created.');
    }
  }

  var form = FormApp.create('Offer support to Vietnam Hearts / Đề xuất hỗ trợ Vietnam Hearts');
  props.setProperty(PROP_FORM_ID, form.getId());
  applyFormSettings_(form, manual);
  addFormItems_(form, manual);
  notes.push('Created the form and remembered its id in script properties, so ' +
    're-running this function will never make a second one.');
  return form;
}

function applyFormSettings_(form, manual) {
  form.setDescription(
    'Want to donate supplies, partner with us long term, or host an event? Tell us here. ' +
    'We are a volunteer team; someone will read this and get back to you.\n\n' +
    'Bạn muốn tặng đồ dùng, hợp tác dài hạn, hoặc tổ chức một sự kiện cùng chúng tôi? ' +
    'Hãy cho chúng tôi biết tại đây. Chúng tôi là nhóm tình nguyện, sẽ có người đọc và ' +
    'liên hệ lại với bạn.');

  // No date is promised anywhere a submitter can read. The seven-day target
  // lives in the sheet's "Next action by" column, for the team only.
  form.setConfirmationMessage(
    'Thank you. We have your request and will get back to you. ' +
    'You can also message us on Zalo or WhatsApp.\n\n' +
    'Cảm ơn bạn. Chúng tôi đã nhận được đề xuất của bạn và sẽ liên hệ lại. ' +
    'Bạn cũng có thể nhắn cho chúng tôi trên Zalo hoặc WhatsApp.');

  form.setProgressBar(true);
  form.setLimitOneResponsePerUser(false);   // limiting forces a Google sign-in
  form.setShowLinkToRespondAgain(false);
  form.setAllowResponseEdits(false);

  // Email collection must be RESPONDER INPUT, not VERIFIED: verified
  // collection forces a Google sign-in, which is the single biggest drop-off
  // risk for submitters on a phone in Vietnam. The older setCollectEmail(true)
  // means VERIFIED, so it is deliberately never called here.
  var collected = false;
  try {
    if (FormApp.EmailCollectionType && form.setEmailCollectionType) {
      form.setEmailCollectionType(FormApp.EmailCollectionType.RESPONDER_INPUT);
      collected = true;
    }
  } catch (err) {
    collected = false;
  }
  if (!collected) {
    manual.push('In the form: Settings -> Responses -> Collect email addresses -> ' +
      '**Responder input**. This build could not set it from script. Do NOT pick ' +
      '"Verified": that forces submitters to sign in to a Google account, and the ' +
      'script needs the email column to exist before setUp() will resolve its headers.');
  }

  // There is no Apps Script setting for response receipts. Off is the default.
  manual.push('In the form: Settings -> Responses -> confirm **Response receipts** ' +
    'is off. The script sends a better bilingual acknowledgement with the Zalo link, ' +
    'and two confirmation emails for one submission looks broken.');
}

/** Whatever a Google API throws, in words a volunteer can act on: no class
 *  name, nothing longer than a sentence. The first live build pasted a raw
 *  TypeError into the build email, which told the reader nothing and hid the
 *  step it was attached to. */
function plainReason_(err) {
  return String((err && err.message) || err || 'unknown')
    .replace(/\b(?:Type|Reference|Range|Syntax|Eval|URI)?Error\b:?\s*/g, '')
    .replace(/\bException\b:?\s*/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 180);
}

/** Make the form reachable by a signed-out stranger, and keep it that way.
 *
 *  Two independent things have to be true, and Google splits them across two
 *  APIs. The form must be PUBLISHED (Forms service), and its responders must
 *  include "anyone with the link" (a Drive permission in the `published`
 *  view). Since Google's 2025 publishing change a form created from a script
 *  can satisfy neither by default: the second live build handed the team a
 *  URL that answered HTTP 401 to anybody not signed in as an editor.
 *
 *  Re-asserted on every run rather than only at creation, because a volunteer
 *  who clicks Unpublish in the editor breaks the public link and "run
 *  buildIntake() again" has to be the one repair instruction we owe them.
 *
 *  Either half failing is a manual step at the FRONT of the list: a form the
 *  public cannot open makes every other step on it pointless. */
function ensureFormPublished_(form, notes, manual) {
  var reasons = [];
  var why = setFormPublished_(form, notes);
  if (why) reasons.push(why);
  why = setAnyoneWithLinkResponder_(form, notes);
  if (why) reasons.push(why);
  if (!reasons.length) return;

  manual.unshift('In the form: **Publish** (top right) -> Responders -> ' +
    '**Anyone with the link**, then **Publish**. Do this before the form link goes ' +
    'anywhere: until it is done the public form URL answers a Google sign-in wall ' +
    'instead of the questions, so nothing else on this list matters. This build could not do ' +
    'it from script (' + reasons.join('; ') + '). Check it afterwards from a ' +
    'signed-out browser, or run `curl -s -o /dev/null -w "%{http_code}" "' +
    form.getPublishedUrl() + '"`, which must print `200` and not `401`.');
}

/** Publish state, through the Forms service. Returns '' when the form is
 *  published, or why it is not. */
function setFormPublished_(form, notes) {
  if (typeof form.setPublished !== 'function' ||
      typeof form.isPublished !== 'function' ||
      typeof form.supportsAdvancedResponderPermissions !== 'function') {
    return 'this account\'s Forms API has no publish control';
  }
  try {
    if (!form.supportsAdvancedResponderPermissions()) {
      // A legacy form, from before publishSettings existed. isPublished and
      // setPublished both throw on one, so there is nothing to assert and the
      // human has to look at it.
      return 'the form is a legacy form with no publish settings';
    }
    if (form.isPublished()) {
      notes.push('The form is already published.');
      return '';
    }
    form.setPublished(true);
    if (!form.isPublished()) return 'the publish setting did not stick';
    notes.push('Published the form, so its public URL serves the questions rather ' +
      'than a Google sign-in wall. A form created from a script is not published ' +
      'by default, so this is asserted on every run.');
    return '';
  } catch (err) {
    return plainReason_(err);
  }
}

/** Responder access, through the advanced Drive service: "anyone with the
 *  link" is a permission with type anyone, role reader and view published.
 *  Drive hides published-view permissions from an ordinary list, so the read
 *  has to ask for them by name or it would create a second one every run. */
function setAnyoneWithLinkResponder_(form, notes) {
  if (typeof Drive === 'undefined' || !Drive || !Drive.Permissions) {
    return 'the advanced Drive API service is not enabled in this script project';
  }
  var id = form.getId();
  try {
    var listed = Drive.Permissions.list(id, { includePermissionsForView: 'published' });
    var perms = (listed && listed.permissions) || [];
    for (var i = 0; i < perms.length; i++) {
      if (perms[i].type === 'anyone' && perms[i].role === 'reader' &&
          perms[i].view === 'published') {
        notes.push('Anyone with the link could already respond to the form.');
        return '';
      }
    }
    Drive.Permissions.create({ type: 'anyone', view: 'published', role: 'reader' }, id);
    notes.push('Opened the form to anyone with the link, so a member of the public ' +
      'can respond to it without signing in to a Google account.');
    return '';
  } catch (err) {
    return plainReason_(err);
  }
}

/** A file upload question forces a Google sign-in on the WHOLE form, from
 *  page one. Google says so in the form's own settings: "because this form has
 *  File Upload questions, respondents will be required to sign in". That is
 *  what the second live build's login wall actually was - the team had added
 *  the question an earlier version of this builder asked them to add, and
 *  "optional and last" defers nothing.
 *
 *  There is no API for the item at all, so the build cannot delete it; it can
 *  only say so, above even the publish step, because a published form with a
 *  file upload question is still unusable by the public. */
function warnOnFileUploadItems_(form, manual) {
  var uploads;
  try {
    if (!FormApp.ItemType || !FormApp.ItemType.FILE_UPLOAD) return;
    uploads = form.getItems(FormApp.ItemType.FILE_UPLOAD) || [];
  } catch (err) {
    return;
  }
  if (!uploads.length) return;

  var titles = [];
  for (var i = 0; i < uploads.length; i++) {
    titles.push('"' + uploads[i].getTitle() + '"');
  }
  manual.unshift('In the form: **delete** the file upload question ' +
    titles.join(', ') + '. Google requires a sign-in for a form that has one, ' +
    'and it forces every submitter to sign in from the first page, so the ' +
    'public form is unusable while it is there and nothing else on this list ' +
    'matters. The acknowledgement email already asks every supplies submitter ' +
    'for photos, from the script, whatever the form says. A form built by an ' +
    'earlier version of this builder asks for them nowhere else, so also paste ' +
    'this into the help text of the supplies section\'s **Condition** question: "' +
    CONDITION_PHOTO_HELP + '" And if \'Standing needs\'!B2 has no line about ' +
    'photos, add both of these lines there. Put "' + TERMS_PHOTO_LINE_EN +
    '" under the English Zalo or WhatsApp number line, and put "' +
    TERMS_PHOTO_LINE_VI + '" under the Vietnamese one, below the --- line, ' +
    'not at the end of the block.');
}

function addFormItems_(form, manual) {
  // Page breaks first, so the branch choices can point at them. A
  // PageBreakItem begins a page; setGoToPage on a page break controls where
  // the form goes after the page BEFORE it, which is why each branch's jump
  // is set on the page break that follows it.
  var pbSupplies = form.addPageBreakItem().setTitle('Donate supplies / Tặng đồ dùng');
  var pbPartner = form.addPageBreakItem().setTitle('Long-term partnership / Hợp tác dài hạn');
  var pbEvent = form.addPageBreakItem().setTitle('Event / Sự kiện');
  var pbOther = form.addPageBreakItem().setTitle('Something else / Điều khác');
  var pbFinal = form.addPageBreakItem().setTitle('Consent and send / Đồng ý và gửi');

  // Page 1, everyone. The branch question has to be created before the page
  // breaks exist to point at, so it is added now and moved to the top.
  var branch = form.addMultipleChoiceItem()
    .setTitle('What would you like to offer? / Bạn muốn đề xuất điều gì?')
    .setRequired(true);
  // These four values are data, not just copy: PARTNERSHIP_CHOICE below and
  // Code.js's "Donate supplies" test both compare against them.
  branch.setChoices([
    branch.createChoice(SUPPLIES_CHOICE, pbSupplies),
    branch.createChoice(PARTNERSHIP_CHOICE, pbPartner),
    branch.createChoice('Host or sponsor an event / Tổ chức hoặc tài trợ sự kiện', pbEvent),
    branch.createChoice('Something else / Điều khác', pbOther)
  ]);
  form.moveItem(branch.getIndex(), 0);

  var name = form.addTextItem()
    .setTitle('Your name / Tên của bạn')
    .setRequired(true);
  form.moveItem(name.getIndex(), 1);

  var phone = form.addTextItem()
    .setTitle('Zalo or WhatsApp number / Số Zalo hoặc WhatsApp')
    .setHelpText('Either is fine - we use both Zalo and WhatsApp. / ' +
      'Số nào cũng được - chúng tôi dùng cả Zalo và WhatsApp.');
  form.moveItem(phone.getIndex(), 2);

  var org = form.addTextItem()
    .setTitle('Organization or company (if any) / Tổ chức hoặc công ty (nếu có)');
  form.moveItem(org.getIndex(), 3);

  var lang = form.addMultipleChoiceItem()
    .setTitle('Which language should we reply in? / Bạn muốn chúng tôi trả lời bằng ngôn ngữ nào?');
  lang.setChoiceValues(['English', 'Tiếng Việt']);
  form.moveItem(lang.getIndex(), 4);

  // Branch jumps. Each page break carries the jump for the page before it.
  pbPartner.setGoToPage(pbFinal);
  pbEvent.setGoToPage(pbFinal);
  pbOther.setGoToPage(pbFinal);
  // The "Something else" page is the page before pbFinal, so it falls through
  // to the final section by normal progression and needs no jump.

  addSuppliesQuestions_(form, pbSupplies, manual);
  addPartnershipQuestions_(form, pbPartner);
  addEventQuestions_(form, pbEvent);
  addOtherQuestions_(form, pbOther);
  addFinalSection_(form, pbFinal);
}

/** Items are appended to the end of the form, then moved to just after their
 *  page break, which is how a branch gets its own questions in order. */
function appendTo_(form, pageBreak, item, offset) {
  form.moveItem(item.getIndex(), pageBreak.getIndex() + 1 + offset);
  return item;
}

function addSuppliesQuestions_(form, pb, manual) {
  var n = 0;

  // The always-needed checkbox question. "Other" stays OFF: an unexpected tick
  // would flag the row High priority and tell the donor it is "almost
  // certainly a yes" about something the team never agreed to.
  var standing = form.addCheckboxItem()
    .setTitle('We always need these - are you giving any of them? / ' +
      'Chúng tôi luôn cần những món này - bạn có tặng món nào không?');
  standing.setChoiceValues(STANDING_ITEMS);
  standing.showOtherOption(false);
  appendTo_(form, pb, standing, n++);

  appendTo_(form, pb, form.addParagraphTextItem()
    .setTitle('Other items not on the list above / Các món khác (không có trong danh sách trên)'),
    n++);

  appendTo_(form, pb, form.addTextItem()
    .setTitle('Roughly how much of each? / Số lượng khoảng bao nhiêu?')
    .setHelpText('e.g. 5 L oil, 2 kg sugar. Above roughly 20 kg or 20 L, please message ' +
      'us first. / ví dụ: 5 lít dầu ăn, 2 kg đường. Nhiều hơn khoảng 20 kg hoặc 20 lít ' +
      'thì xin nhắn tin trước.'),
    n++);

  // Photos are asked for here, in words, rather than with a file upload
  // question. Google requires a Google sign-in for any form that has a file
  // upload item, for the whole form from page one, so the question that used
  // to be a manual step was the login wall the second live build hit. See
  // warnOnFileUploadItems_.
  var condition = form.addMultipleChoiceItem().setTitle('Condition / Tình trạng')
    .setHelpText(CONDITION_PHOTO_HELP);
  condition.setChoiceValues([
    'New / Mới',
    'Gently used / Đã dùng, còn tốt',
    'Mixed / Hỗn hợp'
  ]);
  appendTo_(form, pb, condition, n++);

  appendTo_(form, pb, form.addDateItem()
    .setTitle('When could you hand them over? / Khi nào bạn có thể giao?')
    .setHelpText('We will agree the exact time with you afterwards. / ' +
      'Chúng tôi sẽ hẹn cụ thể với bạn sau.'),
    n++);

  appendTo_(form, pb, form.addTextItem()
    .setTitle('Where are the items now? (district, city) / Đồ hiện đang ở đâu? (quận, thành phố)'),
    n++);

  // The help text here answers "where do I bring it" without giving an
  // address. The school's address is disclosed only after someone signs up as
  // a volunteer and joins the Zalo group, so the public geography stops at the
  // district.
  var deliverer = form.addMultipleChoiceItem()
    .setTitle('Who delivers? / Ai sẽ giao?')
    .setHelpText('We arrange each handover with a volunteer. Our area: Thạnh Mỹ Tây, ' +
      'Ho Chi Minh City. / Chúng tôi hẹn từng lần nhận đồ cùng một tình nguyện viên. ' +
      'Khu vực của chúng tôi: Thạnh Mỹ Tây, TP.HCM.');
  deliverer.setChoiceValues([
    'I can deliver / Tôi giao được',
    'We need Vietnam Hearts to collect / Cần Vietnam Hearts đến lấy',
    'Not sure yet / Chưa rõ'
  ]);
  appendTo_(form, pb, deliverer, n++);

  appendTo_(form, pb, form.addParagraphTextItem().setTitle(CLOSING_QUESTION), n++);

  // There is deliberately NO file upload question and no manual step asking
  // for one. Two separate reasons, and the second is the fatal one:
  // FormApp.Form has no addFileUploadItem on any account (the first live build
  // raised a TypeError reaching for it), and a form that has such a question
  // makes Google require a sign-in from every respondent on page one, which
  // was the login wall behind the second live build's HTTP 401. "Optional and
  // last" defers nothing. The photo ask lives in the Condition help text above
  // and in the acknowledgement email instead. Do not add either back.
}

function addPartnershipQuestions_(form, pb) {
  var n = 0;
  appendTo_(form, pb, form.addParagraphTextItem()
    .setTitle('Your organization and what it does / Tổ chức của bạn và tổ chức đó làm gì?')
    .setRequired(true), n++);
  appendTo_(form, pb, form.addParagraphTextItem()
    .setTitle('What do you have in mind? / Bạn đang nghĩ đến điều gì?')
    .setRequired(true), n++);
  var timeframe = form.addMultipleChoiceItem()
    .setTitle('Timeframe / Khoảng thời gian');
  timeframe.setChoiceValues([
    'One-off / Một lần',
    '3 months / 3 tháng',
    '6 months / 6 tháng',
    'Ongoing / Lâu dài',
    'Not sure / Chưa rõ'
  ]);
  appendTo_(form, pb, timeframe, n++);
  appendTo_(form, pb, form.addTextItem()
    .setTitle('Website or social link / Website hoặc trang mạng xã hội'), n++);
  appendTo_(form, pb, form.addParagraphTextItem().setTitle(CLOSING_QUESTION), n++);
}

function addEventQuestions_(form, pb) {
  var n = 0;
  appendTo_(form, pb, form.addParagraphTextItem()
    .setTitle('What kind of event? / Sự kiện gì?')
    .setRequired(true), n++);
  appendTo_(form, pb, form.addTextItem()
    .setTitle('Proposed date or window / Ngày hoặc khoảng thời gian dự kiến'), n++);
  appendTo_(form, pb, form.addTextItem()
    .setTitle('Expected number of people / Dự kiến bao nhiêu người?'), n++);
  var venue = form.addMultipleChoiceItem().setTitle('Venue / Địa điểm');
  venue.setChoiceValues([
    'You provide one / Chúng tôi có địa điểm',
    'We need one / Cần Vietnam Hearts tìm',
    'Not sure / Chưa rõ'
  ]);
  appendTo_(form, pb, venue, n++);
  appendTo_(form, pb, form.addParagraphTextItem()
    .setTitle('Budget or sponsorship offered (if any) / Ngân sách hoặc tài trợ (nếu có)'), n++);
  appendTo_(form, pb, form.addParagraphTextItem().setTitle(CLOSING_QUESTION), n++);
}

function addOtherQuestions_(form, pb) {
  var n = 0;
  appendTo_(form, pb, form.addParagraphTextItem()
    .setTitle('Tell us about it / Hãy cho chúng tôi biết')
    .setRequired(true), n++);
  appendTo_(form, pb, form.addParagraphTextItem().setTitle(CLOSING_QUESTION), n++);
}

function addFinalSection_(form, pb) {
  var n = 0;
  var consent = form.addCheckboxItem()
    .setTitle('I agree that Vietnam Hearts may store this information and contact me ' +
      'about this request. / Tôi đồng ý để Vietnam Hearts lưu thông tin này và liên hệ ' +
      'với tôi về đề xuất này.')
    .setHelpText('Privacy policy / Chính sách quyền riêng tư: ' + PRIVACY_URL)
    .setRequired(true);
  consent.setChoiceValues(['I agree / Tôi đồng ý']);
  appendTo_(form, pb, consent, n++);

  appendTo_(form, pb, form.addTextItem()
    .setTitle('How did you hear about us? / Bạn biết đến Vietnam Hearts từ đâu?'), n++);
}

/** A link with the request type already chosen, for forwarding to a prospect.
 *  Returns '' rather than throwing: a missing template costs the team a
 *  convenience, not the build. */
function prefilledPartnershipUrl_(form) {
  try {
    var items = form.getItems(FormApp.ItemType.MULTIPLE_CHOICE);
    for (var i = 0; i < items.length; i++) {
      if (items[i].getTitle().indexOf('What would you like to offer') === -1) continue;
      var mc = items[i].asMultipleChoiceItem();
      return form.createResponse()
        .withItemResponse(mc.createResponse(PARTNERSHIP_CHOICE))
        .toPrefilledUrl();
    }
  } catch (err) {
    Logger.log('Could not build a pre-filled URL: %s', err);
  }
  return '';
}

/* ------------------------------------------------------------------ sheet */

function linkFormToSheet_(form, ss, notes, manual) {
  var destination = '';
  try {
    destination = form.getDestinationId();
  } catch (err) {
    destination = '';         // Forms throws when a form has no destination
  }
  if (destination === ss.getId() && ss.getSheetByName(CONFIG.SHEET_NAME)) {
    notes.push('The form is already linked to this spreadsheet, so its destination ' +
      'was left alone.');
    return;
  }

  // A response tab that this form does not write to belongs to an earlier
  // form, and its rows cannot be recreated. Move it aside, never over it.
  var orphan = ss.getSheetByName(CONFIG.SHEET_NAME);
  if (orphan) {
    var aside = CONFIG.SHEET_NAME + ' (old form)';
    for (var n = 2; ss.getSheetByName(aside); n++) {
      aside = CONFIG.SHEET_NAME + ' (old form ' + n + ')';
    }
    orphan.setName(aside);
    notes.push('**The "' + CONFIG.SHEET_NAME + '" tab was not written by this form**, ' +
      'so it was renamed to "' + aside + '" with every row kept, and the form was ' +
      'linked to a new tab.');
    manual.push('**Decide what to do with the "' + aside + '" tab.** It holds the ' +
      'submissions of a form this build could no longer open; nothing reads that tab ' +
      'now, so no reply or reminder will go out for its rows. Copy any row still in ' +
      'progress into "' + CONFIG.SHEET_NAME + '" or "' + TABS.teamTasks + '".');
  }

  var before = ss.getSheets().map(function (sh) { return sh.getName(); });
  form.setDestination(FormApp.DestinationType.SPREADSHEET, ss.getId());
  SpreadsheetApp.flush();

  // Google names the new tab itself, and has changed the capitalisation over
  // the years, so find it by difference rather than by guessing the name.
  var added = ss.getSheets().filter(function (sh) {
    return before.indexOf(sh.getName()) === -1;
  });
  if (added.length !== 1) {
    throw new Error('Linking the form added ' + added.length + ' tabs; expected 1. ' +
      'Open the spreadsheet and check before re-running.');
  }
  if (added[0].getName() !== CONFIG.SHEET_NAME) {
    notes.push('Renamed the response tab from "' + added[0].getName() + '" to "' +
      CONFIG.SHEET_NAME + '", which is the name CONFIG uses.');
    added[0].setName(CONFIG.SHEET_NAME);
  }
  notes.push('Linked the form to this spreadsheet.');
}

/** Insert the team's seven columns to the left of the form's own, the same
 *  move the volunteer signup sheet already makes with applicant_status at A. */
function addTeamColumns_(responses, notes) {
  var headers = responses.getRange(1, 1, 1, Math.max(1, responses.getLastColumn()))
    .getValues()[0].map(function (h) { return String(h).trim(); });

  if (headers[0] === TEAM_COLUMNS[0]) {
    notes.push('The team columns are already in place; their headers were left as they are.');
    return;
  }

  responses.insertColumnsBefore(1, TEAM_COLUMNS.length);
  responses.getRange(1, 1, 1, TEAM_COLUMNS.length).setValues([TEAM_COLUMNS]);
  notes.push('Inserted the ' + TEAM_COLUMNS.length + ' team columns (' +
    TEAM_COLUMNS.join(', ') + ') to the left of the form answers.');
}

function applyTeamColumnFormatting_(responses, volunteers) {
  var lastRow = 1000;
  var c = {};
  TEAM_COLUMNS.forEach(function (h, i) { c[h] = i + 1; });

  function listRule(values) {
    return SpreadsheetApp.newDataValidation()
      .requireValueInList(values, true)
      .setAllowInvalid(false)       // reject input, so a typo cannot invent a status
      .build();
  }

  responses.getRange(2, c['Status'], lastRow - 1, 1)
    .setDataValidation(listRule(STATUS_VALUES));
  responses.getRange(2, c['Priority'], lastRow - 1, 1)
    .setDataValidation(listRule(PRIORITY_VALUES));
  responses.getRange(2, c['Decision'], lastRow - 1, 1)
    .setDataValidation(listRule(DECISION_CHOICES));

  // Owner reads the Volunteers tab, so adding a volunteer there adds them to
  // the dropdown and to the Reply-To lookup in one edit.
  responses.getRange(2, c['Owner'], lastRow - 1, 1).setDataValidation(
    SpreadsheetApp.newDataValidation()
      .requireValueInRange(volunteers.getRange('A2:A200'), true)
      .setAllowInvalid(true)        // a name not yet on the tab is a warning, not a block
      .build());

  responses.getRange(2, c['Next action by'], lastRow - 1, 1)
    .setNumberFormat('yyyy-mm-dd');
  responses.getRange(2, c['Reply sent'], lastRow - 1, 1)
    .setNumberFormat('yyyy-mm-dd hh:mm');

  var statusA1 = colLetter_(c['Status']);
  var dueA1 = colLetter_(c['Next action by']);
  var priorityA1 = colLetter_(c['Priority']);

  // Colour PLUS the word, never colour alone: the status text stays in the
  // cell, so the sheet is still readable to anyone who cannot tell the
  // backgrounds apart.
  var rules = [
    textRule_(responses, statusA1, 'TODO', '#fff3cd'),
    textRule_(responses, statusA1, 'IN PROGRESS', '#cfe2ff'),
    textRule_(responses, statusA1, 'BLOCKED', '#f8d7da'),
    textRule_(responses, statusA1, 'DONE', '#d1e7dd'),
    textRule_(responses, priorityA1, 'High', '#ffe0cc'),
    // The overdue flag: a date in the past on a row nobody has finished. The
    // cheapest accountability mechanism in the whole design.
    SpreadsheetApp.newConditionalFormatRule()
      .whenFormulaSatisfied('=AND($' + dueA1 + '2<>"", $' + dueA1 + '2<TODAY(), $' +
        statusA1 + '2<>"DONE")')
      .setBackground('#f4b6b6')
      .setBold(true)
      .setRanges([responses.getRange(2, c['Next action by'], lastRow - 1, 1)])
      .build()
  ];
  responses.setConditionalFormatRules(rules);

  responses.setFrozenRows(1);
  responses.setFrozenColumns(3);

  // The submitter's own words are protected with a warning rather than a lock:
  // a volunteer who really needs to fix a typo still can, but not by accident.
  responses.getProtections(SpreadsheetApp.ProtectionType.RANGE)
    .forEach(function (p) { p.remove(); });
  var firstFormColumn = TEAM_COLUMNS.length + 1;
  var width = Math.max(1, responses.getLastColumn() - TEAM_COLUMNS.length);
  responses.getRange(1, firstFormColumn, lastRow, width)
    .protect()
    .setDescription('What the submitter wrote. Edit the A:G team columns instead.')
    .setWarningOnly(true);

  responses.autoResizeColumns(1, TEAM_COLUMNS.length);
}

function textRule_(sheet, columnLetter, text, background) {
  return SpreadsheetApp.newConditionalFormatRule()
    .whenTextContains(text)
    .setBackground(background)
    .setRanges([sheet.getRange(columnLetter + '2:' + columnLetter + '1000')])
    .build();
}

function colLetter_(index) {
  var s = '';
  while (index > 0) {
    var r = (index - 1) % 26;
    s = String.fromCharCode(65 + r) + s;
    index = (index - r - 1) / 26;
  }
  return s;
}

/** Create a tab if it is absent, and hand it back either way. */
function tab_(ss, name, notes) {
  var sh = ss.getSheetByName(name);
  if (sh) return sh;
  sh = ss.insertSheet(name);
  notes.push('Created the "' + name + '" tab.');
  return sh;
}

function buildStandingNeedsTab_(ss, notes) {
  var sh = tab_(ss, CONFIG.TERMS_TAB, notes);
  if (String(sh.getRange(CONFIG.TERMS_CELL).getValue() || '').trim()) return sh;

  sh.getRange('A1').setValue('Wording the acknowledgement email quotes. Edit B2.');
  sh.getRange('A2').setValue('Handover and condition block (B2):');
  sh.getRange(CONFIG.TERMS_CELL).setValue(
    'We arrange each handover with a volunteer.\n' +
    'Our area: Thạnh Mỹ Tây, Ho Chi Minh City.\n' +
    'A volunteer will message you to agree a time and place - please wait for that before bringing anything.\n' +
    'You can also message us on Zalo or WhatsApp: <number>.\n' +
    TERMS_PHOTO_LINE_EN + '\n\n' +
    'Food please: sealed, in its original packaging, at least 3 months before the expiry date.\n' +
    'Milk cartons: at least 2 weeks before expiry.\n' +
    'We cannot take home-made or repackaged food, opened items, or dented, rusty or unlabelled containers.\n' +
    'If you have more than roughly 20 kg or 20 L, please message first so we can make space.\n\n' +
    '---\n' +
    'Chúng tôi hẹn từng lần nhận đồ cùng một tình nguyện viên.\n' +
    'Khu vực của chúng tôi: Thạnh Mỹ Tây, TP.HCM.\n' +
    'Một tình nguyện viên sẽ nhắn bạn để hẹn giờ và địa điểm - xin đừng mang đến trước khi hẹn xong.\n' +
    'Bạn cũng có thể nhắn Zalo hoặc WhatsApp: <số>.\n' +
    TERMS_PHOTO_LINE_VI + '\n\n' +
    'Thực phẩm xin: còn nguyên seal, nguyên bao bì gốc, còn hạn ít nhất 3 tháng.\n' +
    'Sữa hộp: còn hạn ít nhất 2 tuần.\n' +
    'Chúng tôi không nhận đồ ăn tự làm hoặc chia lại, đồ đã mở, hộp/lon bị móp, rỉ hoặc mất nhãn.\n' +
    'Nếu nhiều hơn khoảng 20 kg hoặc 20 lít, xin nhắn trước để chúng tôi chuẩn bị chỗ chứa.');
  sh.getRange(CONFIG.TERMS_CELL).setWrap(true);
  sh.setColumnWidth(2, 560);

  sh.getRange('A4').setValue(
    'DO NOT put the school address in B2. This cell is quoted into an email that ' +
    'goes to anyone who fills in the public form, and the address is only given out ' +
    'after someone signs up as a volunteer and joins the Zalo group. The public ' +
    'area is Thạnh Mỹ Tây, TP.HCM, and nothing finer: no street address and no hours.');
  sh.getRange('A5').setValue(
    'The always-needed item list is NOT here. It lives in the form\'s checkbox ' +
    'question, which is the only copy. To change what the team always accepts, edit ' +
    'the form. Then update the three public surfaces: the Instagram highlight, the ' +
    'pinned Zalo message, and the website block.');
  sh.getRange('A4:A5').setWrap(true);
  sh.setColumnWidth(1, 420);
  return sh;
}

function buildVolunteersTab_(ss, notes) {
  var sh = tab_(ss, TABS.volunteers, notes);
  if (String(sh.getRange('A1').getValue() || '').trim()) return sh;

  sh.getRange('A1:C1').setValues([['Name', 'Email', 'Notes']]).setFontWeight('bold');
  sh.getRange(2, 2, 199, 1).setDataValidation(
    SpreadsheetApp.newDataValidation().requireTextContains('@')
      .setAllowInvalid(false)
      .setHelpText('An email address, for example mai@example.com.').build());
  sh.getRange('E1').setValue(
    'One row per volunteer who can own a request. "Name" must match the Owner ' +
    'dropdown exactly. The Approved email names this person and sets Reply-To to ' +
    'their address, so a submitter answers a human. Nothing else on this tab is read ' +
    'by the script: do not expect a phone number here to reach anybody, and never ' +
    'put one in an email.');
  sh.getRange('E1').setWrap(true);
  sh.setColumnWidth(1, 180);
  sh.setColumnWidth(2, 260);
  sh.setColumnWidth(5, 420);
  sh.setFrozenRows(1);
  return sh;
}

function buildTeamTasksTab_(ss, notes) {
  var sh = tab_(ss, TABS.teamTasks, notes);
  if (String(sh.getRange('A1').getValue() || '').trim()) return sh;

  sh.getRange('A1:E1')
    .setValues([['Status', 'Priority', 'Next action by', 'Owner', 'What']])
    .setFontWeight('bold');
  sh.getRange(2, 1, 999, 1).setDataValidation(
    SpreadsheetApp.newDataValidation().requireValueInList(STATUS_VALUES, true)
      .setAllowInvalid(false).build());
  sh.getRange(2, 2, 999, 1).setDataValidation(
    SpreadsheetApp.newDataValidation().requireValueInList(PRIORITY_VALUES, true)
      .setAllowInvalid(false).build());
  sh.getRange(2, 3, 999, 1).setNumberFormat('yyyy-mm-dd');
  sh.getRange('G1').setValue(
    'Work that did not come from the form. There is deliberately no Decision ' +
    'column here: nothing on this tab can email anybody. Keep task rows OFF the ' +
    'response tab, where the form appends rows and would push them around.');
  sh.getRange('G1').setWrap(true);
  sh.setColumnWidth(7, 420);
  sh.setFrozenRows(1);
  return sh;
}

function allWorkQuery_() {
  return '=IFERROR(QUERY({' +
    'ARRAYFORMULA(IF(\'' + CONFIG.SHEET_NAME + '\'!A2:A<>"","Request","")),' +
    '\'' + CONFIG.SHEET_NAME + '\'!A2:D,\'' + CONFIG.SHEET_NAME + '\'!F2:F;' +
    'ARRAYFORMULA(IF(\'' + TABS.teamTasks + '\'!A2:A<>"","Task","")),' +
    '\'' + TABS.teamTasks + '\'!A2:D,\'' + TABS.teamTasks + '\'!E2:E' +
    '},"select * where Col1 is not null and Col2 <> \'DONE\' order by Col4",0),"")';
}

function buildAllWorkTab_(ss, notes) {
  var sh = tab_(ss, TABS.allWork, notes);

  // Read-only union of the two task sources, newest deadline first, finished
  // work left out but never deleted. Written on every run, after every
  // structural change, because Sheets rewrites references into a tab when it
  // is renamed or gains columns, so a formula written earlier can be stale.
  sh.getRange('A1').setFormula(
    '={"Where","Status","Priority","Next action by","Owner","What"}');
  sh.getRange('A2').setFormula(allWorkQuery_());
  sh.getRange('H1').setValue(
    'Read-only. Both task sources in one list, overdue first, DONE rows left out. ' +
    'buildIntake() rewrites A1 and A2 on every run, so an edit to those cells does ' +
    'not survive: to read a different column, change allWorkQuery_ in Build.js.');
  sh.getRange('H1').setWrap(true);
  sh.setColumnWidth(8, 420);
  sh.setFrozenRows(1);
  return sh;
}

/** The published tab.
 *
 *  A chart cannot filter its own rows, so this tab is a QUERY over the team's
 *  Progress tab. Three things fall out of that: "Show on site = NO" hides a
 *  project without deleting it, a project can be prepared privately, and the
 *  tab that reaches the public web holds nothing but project names and
 *  numbers.
 *
 *  Every project is charted as PERCENT FUNDED, never as a raw amount. The
 *  Progress tab deliberately allows one currency per row, so raw totals on one
 *  axis compare a VND number with a USD one: the first live build stacked
 *  "Monthly rice fund (USD)" 12382 of 13000 against "Tết gifts 2027 (VND)"
 *  400000 of 2600000, and the VND bar dwarfed the USD one while neither bar
 *  meant anything. A fraction is unit-free, so it is right whatever currency a
 *  row is in and - unlike converting everything to USD - it needs no exchange
 *  rate that goes stale silently between the two tabs. The amounts are not
 *  lost: they travel in each bar's label, formatted, beside the project name
 *  that states the currency.
 *
 *  E:G hold the filtered QUERY and are hidden. A:C are what the chart reads,
 *  and A2 is ONE FILTER that spills exactly as many rows as the QUERY
 *  returned: a formula padded to row 30 with "" would hand the chart a value
 *  in every spare row, and a bar chart draws each one as an empty category.
 *
 *  A1:G30 is cleared first because the whole block is build-owned: a sheet
 *  built by an earlier version holds a note in F1 and formulas in B2 and C2,
 *  and either would block a spill and leave #REF! where the chart reads. */
function buildProgressChartTab_(sh) {
  sh.getRange('A1:G30').clearContent();
  sh.getRange('A1').setFormula('={"Project","Funded","Remaining"}');

  sh.getRange('E1').setFormula('={"Project","Raised","Goal"}');
  sh.getRange('E2').setFormula(
    '=IFERROR(QUERY(' + TABS.progress + '!A2:F30,' +
    '"select Col1, Col3, Col2 where Col6 = \'YES\' and Col1 is not null",0),"")');

  // IFERROR element by element, so a goal of 0 is 0 rather than a #DIV/0!
  // that would blank the whole chart; capped at 1 so over-funding cannot
  // overflow the axis.
  sh.getRange('A2').setFormula(
    '=ARRAYFORMULA(IFERROR(LET(' +
    'f,IFERROR(IF(F2:F30/G2:G30>1,1,F2:F30/G2:G30),0),' +
    'FILTER({E2:E30&" - "&TEXT(F2:F30,"#,##0")&" of "&TEXT(G2:G30,"#,##0"),f,1-f},' +
    'E2:E30<>"")),""))');
  sh.getRange('B2:C30').setNumberFormat('0%');

  sh.hideColumns(5, 3);

  sh.getRange('I1').setValue(
    'Published to the web. Nothing personal may ever be added to this tab. ' +
    'A1:G30 and the chart are rewritten by buildIntake() on every run, so ' +
    'an edit to them does not survive: change buildProgressChartTab_ in ' +
    'Build.js instead. The two charted series are fractions of each project\'s ' +
    'own goal, which is what lets one chart hold a VND project and a USD one; ' +
    'the amounts are in the bar labels. Columns E to G are hidden on purpose.');
  sh.getRange('I1').setWrap(true);
  sh.setColumnWidth(9, 420);
  sh.setFrozenRows(1);
  return sh;
}

function buildTiersTab_(ss, notes) {
  var sh = tab_(ss, TABS.tiers, notes);
  if (String(sh.getRange('A1').getValue() || '').trim()) return sh;

  // VND leads the two amount columns because a tier IS a VND amount and the
  // USD figure is the conversion; the two OUTCOME columns read English before
  // Vietnamese, like every other bilingual pair in this project.
  sh.getRange('A1:E1').setValues([[
    'VND', 'USD (approx)', 'Outcome (English)', 'Outcome (Tiếng Việt)', 'As of'
  ]]).setFontWeight('bold');

  sh.getRange('A2:E6').setValues([
    [50000, 2, 'Rice for one child for a month, plus two cartons of milk',
      'Gạo cho một em trong một tháng, cộng hai hộp sữa', '2026-11-13'],
    [100000, 4,
      'Rice for three children for a month, or a month of cooking oil and sugar for one class',
      'Gạo cho ba em trong một tháng, hoặc dầu ăn và đường cho một lớp', '2026-11-13'],
    [250000, 10,
      'Rice for eight children for a month, or a full kitchen basket for one class: oil, sugar, fish sauce, seasoning',
      'Gạo cho tám em trong một tháng, hoặc một giỏ bếp đầy đủ cho một lớp: dầu ăn, đường, nước mắm, bột nêm',
      '2026-11-13'],
    [600000, 23, 'Rice for twenty children for a month',
      'Gạo cho hai mươi em trong một tháng', '2026-11-13'],
    [1300000, 50, 'Rice for forty children for a month',
      'Gạo cho bốn mươi em trong một tháng', '2026-11-13']
  ]);
  sh.getRange('A2:A6').setNumberFormat('#,##0');
  sh.getRange('G1').setValue(
    'Head the published table "Khoảng / About", never "Your donation pays for": a ' +
    'promise that a specific amount buys a specific thing reads as a restricted gift, ' +
    'which the organisation is then obliged to honour. Re-check every six months, ' +
    'rice first, at the regular shelf price rather than a promotion, and refresh the ' +
    '"As of" date in the same sitting.');
  sh.getRange('G1').setWrap(true);
  sh.setColumnWidth(7, 420);
  sh.setFrozenRows(1);
  return sh;
}

function buildProgressTabs_(ss, notes, manual) {
  var progress = tab_(ss, TABS.progress, notes);
  if (!String(progress.getRange('A1').getValue() || '').trim()) {
    progress.getRange('A1:H1').setValues([[
      'Project', 'Goal', 'Raised', 'Remaining', 'Percent', 'Show on site', 'Updated', 'Note'
    ]]).setFontWeight('bold');

    progress.getRange('A2:C4').setValues([
      ['Monthly rice fund (USD)', 13000, 12382],
      ['Tết gifts 2027 (VND)', 2600000, 400000],
      ['School fans (USD)', 300, 0]
    ]);
    progress.getRange('F2:H4').setValues([
      ['YES', '2026-11-13', 'GoFundMe total'],
      ['YES', '2026-11-13', '200 notebook and pen sets'],
      ['NO', '', 'not public yet']
    ]);

    // Filled to row 30 so adding a project is typing one row and nothing else.
    progress.getRange('D2:D30').setFormulaR1C1(
      '=IF(AND(ISNUMBER(RC[-2]),ISNUMBER(RC[-1])), MAX(0, RC[-2]-RC[-1]), "")');
    progress.getRange('E2:E30').setFormulaR1C1(
      '=IF(AND(ISNUMBER(RC[-3]),ISNUMBER(RC[-2]),RC[-3]>0), RC[-2]/RC[-3], "")');
    progress.getRange('E2:E30').setNumberFormat('0%');
    progress.getRange(2, 6, 29, 1).setDataValidation(
      SpreadsheetApp.newDataValidation().requireValueInList(['YES', 'NO'], true)
        .setAllowInvalid(false).build());
    progress.getRange('D1:E1').setBackground('#eeeeee');
    progress.getRange('J1').setValue(
      'Type in Project, Goal, Raised, Show on site, Updated and Note. Remaining and ' +
      'Percent are formulas - leave them alone. One currency per row, named in the ' +
      'project; never total the column, because a sum across currencies means nothing. ' +
      'For the same reason the published chart shows PERCENT FUNDED rather than ' +
      'amounts, with the amounts written into each bar\'s label, so a VND project and ' +
      'a USD project can sit on one axis and still be comparable.');
    progress.getRange('J1').setWrap(true);
    progress.setColumnWidth(10, 420);
    progress.setFrozenRows(1);
  }

  var chartTab = tab_(ss, TABS.progressChart, notes);
  // Build-owned, rewritten every run like the All work formulas: these cells
  // are the whole contract of a tab that gets published to the public web, so
  // a hand edit to one of them must not survive unnoticed.
  buildProgressChartTab_(chartTab);

  // Converged in place every run, so a chart left by an earlier version
  // (other title, other axis, anchored in a now-hidden column) takes the
  // current shape while keeping its id: a published chart's embed URL is keyed
  // to that id, and a new chart would blank the iframe on the website.
  var charts = chartTab.getCharts();
  charts.slice(1).forEach(function (c) { chartTab.removeChart(c); });
  SpreadsheetApp.flush();
  // Whole-column ranges with spare rows, so a project added at row 5 appears
  // in the chart without anyone editing the chart. A:C only - the helper
  // columns that hold the raw amounts are never charted.
  var chart = (charts.length ? charts[0].modify().clearRanges() : chartTab.newChart())
    .setChartType(Charts.ChartType.BAR)
    .addRange(chartTab.getRange('A1:A30'))
    .addRange(chartTab.getRange('B1:C30'))
    .setOption('isStacked', true)
    .setOption('title', 'Vietnam Hearts - percent funded')
    .setOption('legend', { position: 'bottom' })
    // Both series are fractions that sum to 1, so the value axis is a
    // percentage from empty to funded and nothing else can land on it.
    .setOption('hAxis', { format: 'percent', viewWindow: { min: 0, max: 1 } })
    .setOption('width', 560)
    .setOption('height', 300)
    .setPosition(3, 9, 0, 0)
    .build();
  if (charts.length) chartTab.updateChart(chart);
  else chartTab.insertChart(chart);
  notes.push('Built the percent-funded progress chart on "' +
    TABS.progressChart + '".');

  manual.push('In the spreadsheet: File -> Share -> **Publish to web**, and publish ' +
    '**only** the "' + TABS.progressChart + '" tab or its chart, then copy the ' +
    '`<iframe>`. Apps Script cannot publish to the web, so this step is yours. Never ' +
    'publish "Entire document" or the response tab: it holds submitters\' names, ' +
    'emails and phone numbers, and publishing is public to anyone with the URL.');
  manual.push('In the spreadsheet: publish the "' + TABS.tiers + '" tab the same way ' +
    'when you are ready to show the tier table on the website.');
}

/* --------------------------------------------------------------- reporting */

function collectManualSteps_(manual) {
  manual.push('Share the spreadsheet only with the named volunteers who handle ' +
    'requests, as editors; never set General access to Anyone with the link; the ' +
    'response tab holds submitters\' names, emails and Zalo/WhatsApp numbers.');
  manual.push('In the spreadsheet: Data -> **Filter views** -> create one per working ' +
    'state (TODO, IN PROGRESS, BLOCKED) and one for Priority = High. Apps Script has no ' +
    'filter-view API. Use filter views rather than a plain filter: a filter view is ' +
    'per-viewer, so two volunteers can filter differently at the same time.');
  manual.push('On the "' + TABS.volunteers + '" tab: add one row per volunteer who can ' +
    'own a request, Name and Email. Until a volunteer is listed, an approved request ' +
    'replies to the team address and the build emails you about it.');
  manual.push('On the "' + CONFIG.TERMS_TAB + '" tab, cell ' + CONFIG.TERMS_CELL +
    ': replace <số> / <number> with the team Zalo or WhatsApp number, and read the ' +
    'whole block once. It is quoted verbatim into the acknowledgement email.');
  manual.push('In Code.js: set CONFIG.DRY_RUN to false and push again, once you have ' +
    'submitted a test response and seen the two [DRY RUN] emails arrive. Until then ' +
    'every message goes to the team address instead of to the submitter.');
}

/** Notes and manual steps are plain text with the README's **bold** and
 * `code`, so the same wording reads right in both. Escape first: a step quotes
 * `<iframe>` and the <số> placeholder, which pasted in raw became real tags and
 * hid every step after them in the team's inbox. */
function stepHtml_(text) {
  return String(text)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

function reportBuild_(form, ss, notes, manual) {
  var prefilled = prefilledPartnershipUrl_(form);
  var lines = [
    '<p>The Vietnam Hearts request intake has been built. Three links to keep:</p>',
    '<ul>',
    '<li><b>The form, to share publicly</b>: <a href="' + form.getPublishedUrl() + '">' +
      form.getPublishedUrl() + '</a><br>' +
      // The one check nothing automated can make, so it is put next to the
      // link rather than buried in the numbered list: this email is the only
      // set of instructions anybody gets. A form that is unpublished, or that
      // carries a file upload question, looks perfectly healthy to everyone on
      // the team, because the team is signed in.
      '<b>Open that link in a private browsing window, signed out of Google, ' +
      'before you share it.</b> You must see the questions. A sign-in screen ' +
      'means the form is not public yet: the first steps below fix it.</li>',
    '<li><b>The form, to edit</b>: <a href="' + form.getEditUrl() + '">' +
      form.getEditUrl() + '</a></li>',
    prefilled
      ? '<li><b>Pre-filled link template</b> (opens with "a long-term partnership" ' +
        'already chosen, for forwarding to a prospect): <a href="' + prefilled + '">' +
        prefilled + '</a></li>'
      : '<li>Pre-filled link template: could not be generated. Make one by hand from ' +
        'the form\'s three-dot menu, "Get pre-filled link".</li>',
    '<li><b>The spreadsheet</b>: <a href="' + ss.getUrl() + '">' + ss.getUrl() + '</a></li>',
    '</ul>',
    '<p>The intake runs in Apps Script inside this spreadsheet and deploys with ' +
      'clasp push. It adds nothing to the Cloud Run service or its container image.</p>',
    '<h3>What this build did</h3><ul>'
  ];
  notes.forEach(function (n) { lines.push('<li>' + stepHtml_(n) + '</li>'); });
  lines.push('</ul>');

  lines.push('<h3>Manual steps, because Apps Script cannot do these</h3><ol>');
  manual.forEach(function (m) { lines.push('<li>' + stepHtml_(m) + '</li>'); });
  lines.push('</ol>');
  lines.push('<p>Re-running buildIntake() is safe: it repairs this sheet rather than ' +
    'building a second one.</p>');

  var html = lines.join('\n');
  Logger.log(html.replace(/<[^>]+>/g, ' '));
  MailApp.sendEmail({
    to: CONFIG.TEAM_EMAIL,
    subject: 'Vietnam Hearts request intake is built - ' + manual.length +
      ' manual steps left',
    htmlBody: html
  });
}
