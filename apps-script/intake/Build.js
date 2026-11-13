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
  'Dầu ăn / Cooking oil',
  'Đường / Sugar',
  'Sữa hộp nhỏ / Small milk cartons',
  'Nước mắm / Fish sauce',
  'Bột nêm, bột ngọt / Seasoning powder, MSG'
];

/** Asked at the end of every branch, in exactly these words on purpose.
 *  Google Forms still makes one sheet column per item, so this produces four
 *  identically headed columns, one per branch, and each row fills only one. */
var CLOSING_QUESTION =
  'Bạn cần gì từ Vietnam Hearts, hoặc có điều gì khác muốn cho chúng tôi biết? / ' +
  'What do you need from us, or anything else we should know?';

/* ------------------------------------------------------------------ build */

function buildIntake() {
  var ss = SpreadsheetApp.getActive();
  var manual = [];
  var notes = [];

  var form = openOrCreateForm_(notes, manual);
  linkFormToSheet_(form, ss, notes);

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
    manual.push('Do the form settings steps above first, then run <b>setUp()</b> from ' +
      'the Apps Script editor, or simply run buildIntake() again. It would not install ' +
      'the triggers yet because a column it needs is missing: <i>' + err.message +
      '</i>. Until that succeeds a submission is still recorded in the sheet, but ' +
      'nobody is emailed - not the submitter and not the team.');
  }

  collectManualSteps_(manual);
  reportBuild_(form, ss, notes, manual);
  return { formUrl: form.getPublishedUrl(), sheetUrl: ss.getUrl() };
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

  var form = FormApp.create('Đề xuất hỗ trợ Vietnam Hearts / Offer support to Vietnam Hearts');
  props.setProperty(PROP_FORM_ID, form.getId());
  applyFormSettings_(form, manual);
  addFormItems_(form, manual);
  notes.push('Created the form and remembered its id in script properties, so ' +
    're-running this function will never make a second one.');
  return form;
}

function applyFormSettings_(form, manual) {
  form.setDescription(
    'Bạn muốn tặng đồ dùng, hợp tác dài hạn, hoặc tổ chức một sự kiện cùng chúng tôi? ' +
    'Hãy cho chúng tôi biết tại đây. Chúng tôi là nhóm tình nguyện, sẽ có người đọc và ' +
    'liên hệ lại với bạn.\n\n' +
    'Want to donate supplies, partner with us long term, or host an event? Tell us here. ' +
    'We are a volunteer team; someone will read this and get back to you.');

  // No date is promised anywhere a submitter can read. The seven-day target
  // lives in the sheet's "Next action by" column, for the team only.
  form.setConfirmationMessage(
    'Cảm ơn bạn. Chúng tôi đã nhận được đề xuất của bạn và sẽ liên hệ lại. ' +
    'Bạn cũng có thể nhắn cho chúng tôi trên Zalo hoặc WhatsApp.\n\n' +
    'Thank you. We have your request and will get back to you. ' +
    'You can also message us on Zalo or WhatsApp.');

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

function addFormItems_(form, manual) {
  // Page breaks first, so the branch choices can point at them. A
  // PageBreakItem begins a page; setGoToPage on a page break controls where
  // the form goes after the page BEFORE it, which is why each branch's jump
  // is set on the page break that follows it.
  var pbSupplies = form.addPageBreakItem().setTitle('Tặng đồ dùng / Donate supplies');
  var pbPartner = form.addPageBreakItem().setTitle('Hợp tác dài hạn / Long-term partnership');
  var pbEvent = form.addPageBreakItem().setTitle('Sự kiện / Event');
  var pbOther = form.addPageBreakItem().setTitle('Điều khác / Something else');
  var pbFinal = form.addPageBreakItem().setTitle('Đồng ý và gửi / Consent and send');

  // Page 1, everyone. The branch question has to be created before the page
  // breaks exist to point at, so it is added now and moved to the top.
  var branch = form.addMultipleChoiceItem()
    .setTitle('Bạn muốn đề xuất điều gì? / What would you like to offer?')
    .setRequired(true);
  branch.setChoices([
    branch.createChoice('Tặng đồ dùng, vật phẩm / Donate supplies or goods', pbSupplies),
    branch.createChoice('Hợp tác dài hạn / A long-term partnership', pbPartner),
    branch.createChoice('Tổ chức hoặc tài trợ sự kiện / Host or sponsor an event', pbEvent),
    branch.createChoice('Điều khác / Something else', pbOther)
  ]);
  form.moveItem(branch.getIndex(), 0);

  var name = form.addTextItem()
    .setTitle('Tên của bạn / Your name')
    .setRequired(true);
  form.moveItem(name.getIndex(), 1);

  var phone = form.addTextItem()
    .setTitle('Số Zalo hoặc WhatsApp / Zalo or WhatsApp number')
    .setHelpText('Số nào cũng được - chúng tôi dùng cả Zalo và WhatsApp. / ' +
      'Either is fine - we use both Zalo and WhatsApp.');
  form.moveItem(phone.getIndex(), 2);

  var org = form.addTextItem()
    .setTitle('Tổ chức hoặc công ty (nếu có) / Organization or company (if any)');
  form.moveItem(org.getIndex(), 3);

  var lang = form.addMultipleChoiceItem()
    .setTitle('Bạn muốn chúng tôi trả lời bằng ngôn ngữ nào? / Which language should we reply in?');
  lang.setChoiceValues(['Tiếng Việt', 'English']);
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
    .setTitle('Chúng tôi luôn cần những món này - bạn có tặng món nào không? / ' +
      'We always need these - are you giving any of them?');
  standing.setChoiceValues(STANDING_ITEMS);
  standing.showOtherOption(false);
  appendTo_(form, pb, standing, n++);

  appendTo_(form, pb, form.addParagraphTextItem()
    .setTitle('Các món khác (không có trong danh sách trên) / Other items not on the list above'),
    n++);

  appendTo_(form, pb, form.addTextItem()
    .setTitle('Số lượng khoảng bao nhiêu? / Roughly how much of each?')
    .setHelpText('ví dụ: 5 lít dầu ăn, 2 kg đường. Nhiều hơn khoảng 20 kg hoặc 20 lít ' +
      'thì xin nhắn tin trước. / e.g. 5 L oil, 2 kg sugar. Above roughly 20 kg or 20 L, ' +
      'please message us first.'),
    n++);

  var condition = form.addMultipleChoiceItem().setTitle('Tình trạng / Condition');
  condition.setChoiceValues([
    'Mới / New',
    'Đã dùng, còn tốt / Gently used',
    'Hỗn hợp / Mixed'
  ]);
  appendTo_(form, pb, condition, n++);

  appendTo_(form, pb, form.addDateItem()
    .setTitle('Khi nào bạn có thể giao? / When could you hand them over?')
    .setHelpText('Chúng tôi sẽ hẹn cụ thể với bạn sau. / ' +
      'We will agree the exact time with you afterwards.'),
    n++);

  appendTo_(form, pb, form.addTextItem()
    .setTitle('Đồ hiện đang ở đâu? (quận, thành phố) / Where are the items now? (district, city)'),
    n++);

  // The help text here answers "where do I bring it" without giving an
  // address. The school's address is disclosed only after someone signs up as
  // a volunteer and joins the Zalo group, so the public geography stops at the
  // district.
  var deliverer = form.addMultipleChoiceItem()
    .setTitle('Ai sẽ giao? / Who delivers?')
    .setHelpText('Chúng tôi hẹn từng lần nhận đồ cùng một tình nguyện viên. ' +
      'Khu vực của chúng tôi: Thạnh Mỹ Tây, TP.HCM. / We arrange each handover with ' +
      'a volunteer. Our area: Thạnh Mỹ Tây, Ho Chi Minh City.');
  deliverer.setChoiceValues([
    'Tôi giao được / I can deliver',
    'Cần Vietnam Hearts đến lấy / We need Vietnam Hearts to collect',
    'Chưa rõ / Not sure yet'
  ]);
  appendTo_(form, pb, deliverer, n++);

  appendTo_(form, pb, form.addParagraphTextItem().setTitle(CLOSING_QUESTION), n++);

  // File upload is last in the section and optional, so nobody meets Google's
  // sign-in wall before their answers are saved.
  try {
    appendTo_(form, pb, form.addFileUploadItem()
      .setTitle('Ảnh của đồ (không bắt buộc) / Photo of the items (optional)')
      .setHelpText('Google sẽ yêu cầu bạn đăng nhập tài khoản Google để tải ảnh. ' +
        'Nếu không tiện, bạn có thể gửi ảnh qua Zalo hoặc WhatsApp. / Google will ask ' +
        'you to sign in to attach a photo. If that is inconvenient, send photos on ' +
        'Zalo or WhatsApp instead.'),
      n++);
  } catch (err) {
    manual.push('In the form, on the supplies section, add a **File upload** question ' +
      'titled "Ảnh của đồ (không bắt buộc) / Photo of the items (optional)" as the LAST ' +
      'question of that section. This build could not add it (' + err + '). Keep it ' +
      'optional and last: attaching a file forces a Google sign-in, and last means the ' +
      'submitter has already answered everything else before meeting it.');
  }
}

function addPartnershipQuestions_(form, pb) {
  var n = 0;
  appendTo_(form, pb, form.addParagraphTextItem()
    .setTitle('Tổ chức của bạn và tổ chức đó làm gì? / Your organization and what it does')
    .setRequired(true), n++);
  appendTo_(form, pb, form.addParagraphTextItem()
    .setTitle('Bạn đang nghĩ đến điều gì? / What do you have in mind?')
    .setRequired(true), n++);
  var timeframe = form.addMultipleChoiceItem()
    .setTitle('Khoảng thời gian / Timeframe');
  timeframe.setChoiceValues([
    'Một lần / One-off',
    '3 tháng / 3 months',
    '6 tháng / 6 months',
    'Lâu dài / Ongoing',
    'Chưa rõ / Not sure'
  ]);
  appendTo_(form, pb, timeframe, n++);
  appendTo_(form, pb, form.addTextItem()
    .setTitle('Website hoặc trang mạng xã hội / Website or social link'), n++);
  appendTo_(form, pb, form.addParagraphTextItem().setTitle(CLOSING_QUESTION), n++);
}

function addEventQuestions_(form, pb) {
  var n = 0;
  appendTo_(form, pb, form.addParagraphTextItem()
    .setTitle('Sự kiện gì? / What kind of event?')
    .setRequired(true), n++);
  appendTo_(form, pb, form.addTextItem()
    .setTitle('Ngày hoặc khoảng thời gian dự kiến / Proposed date or window'), n++);
  appendTo_(form, pb, form.addTextItem()
    .setTitle('Dự kiến bao nhiêu người? / Expected number of people'), n++);
  var venue = form.addMultipleChoiceItem().setTitle('Địa điểm / Venue');
  venue.setChoiceValues([
    'Chúng tôi có địa điểm / You provide one',
    'Cần Vietnam Hearts tìm / We need one',
    'Chưa rõ / Not sure'
  ]);
  appendTo_(form, pb, venue, n++);
  appendTo_(form, pb, form.addParagraphTextItem()
    .setTitle('Ngân sách hoặc tài trợ (nếu có) / Budget or sponsorship offered (if any)'), n++);
  appendTo_(form, pb, form.addParagraphTextItem().setTitle(CLOSING_QUESTION), n++);
}

function addOtherQuestions_(form, pb) {
  var n = 0;
  appendTo_(form, pb, form.addParagraphTextItem()
    .setTitle('Hãy cho chúng tôi biết / Tell us about it')
    .setRequired(true), n++);
  appendTo_(form, pb, form.addParagraphTextItem().setTitle(CLOSING_QUESTION), n++);
}

function addFinalSection_(form, pb) {
  var n = 0;
  var consent = form.addCheckboxItem()
    .setTitle('Tôi đồng ý để Vietnam Hearts lưu thông tin này và liên hệ với tôi về ' +
      'đề xuất này. / I agree that Vietnam Hearts may store this information and ' +
      'contact me about this request.')
    .setHelpText('Chính sách quyền riêng tư / Privacy policy: ' + PRIVACY_URL)
    .setRequired(true);
  consent.setChoiceValues(['Tôi đồng ý / I agree']);
  appendTo_(form, pb, consent, n++);

  appendTo_(form, pb, form.addTextItem()
    .setTitle('Bạn biết đến Vietnam Hearts từ đâu? / How did you hear about us?'), n++);
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
        .withItemResponse(mc.createResponse('Hợp tác dài hạn / A long-term partnership'))
        .toPrefilledUrl();
    }
  } catch (err) {
    Logger.log('Could not build a pre-filled URL: %s', err);
  }
  return '';
}

/* ------------------------------------------------------------------ sheet */

function linkFormToSheet_(form, ss, notes) {
  var before = ss.getSheets().map(function (sh) { return sh.getName(); });

  if (ss.getSheetByName(CONFIG.SHEET_NAME)) {
    notes.push('The "' + CONFIG.SHEET_NAME + '" tab already exists, so the form ' +
      'destination was left alone.');
    return;
  }

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
    'Chúng tôi hẹn từng lần nhận đồ cùng một tình nguyện viên.\n' +
    'Khu vực của chúng tôi: Thạnh Mỹ Tây, TP.HCM.\n' +
    'Một tình nguyện viên sẽ nhắn bạn để hẹn giờ và địa điểm - xin đừng mang đến trước khi hẹn xong.\n' +
    'Bạn cũng có thể nhắn Zalo hoặc WhatsApp: <số>.\n\n' +
    'Thực phẩm xin: còn nguyên seal, nguyên bao bì gốc, còn hạn ít nhất 3 tháng.\n' +
    'Sữa hộp: còn hạn ít nhất 2 tuần.\n' +
    'Chúng tôi không nhận đồ ăn tự làm hoặc chia lại, đồ đã mở, hộp/lon bị móp, rỉ hoặc mất nhãn.\n' +
    'Nếu nhiều hơn khoảng 20 kg hoặc 20 lít, xin nhắn trước để chúng tôi chuẩn bị chỗ chứa.\n\n' +
    '---\n' +
    'We arrange each handover with a volunteer.\n' +
    'Our area: Thạnh Mỹ Tây, Ho Chi Minh City.\n' +
    'A volunteer will message you to agree a time and place - please wait for that before bringing anything.\n' +
    'You can also message us on Zalo or WhatsApp: <number>.\n\n' +
    'Food please: sealed, in its original packaging, at least 3 months before the expiry date.\n' +
    'Milk cartons: at least 2 weeks before expiry.\n' +
    'We cannot take home-made or repackaged food, opened items, or dented, rusty or unlabelled containers.\n' +
    'If you have more than roughly 20 kg or 20 L, please message first so we can make space.');
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

function buildAllWorkTab_(ss, notes) {
  var sh = tab_(ss, TABS.allWork, notes);
  if (String(sh.getRange('A1').getValue() || '').trim()) return sh;

  // Read-only union of the two task sources, newest deadline first, finished
  // work left out but never deleted.
  sh.getRange('A1').setFormula(
    '={"Where","Status","Priority","Next action by","Owner","What"}');
  sh.getRange('A2').setFormula(
    '=IFERROR(QUERY({' +
    'ARRAYFORMULA(IF(\'' + CONFIG.SHEET_NAME + '\'!A2:A<>"","Request","")),' +
    '\'' + CONFIG.SHEET_NAME + '\'!A2:D,\'' + CONFIG.SHEET_NAME + '\'!F2:F;' +
    'ARRAYFORMULA(IF(\'' + TABS.teamTasks + '\'!A2:A<>"","Task","")),' +
    '\'' + TABS.teamTasks + '\'!A2:D,\'' + TABS.teamTasks + '\'!E2:E' +
    '},"select * where Col1 is not null and Col2 <> \'DONE\' order by Col4",0),"")');
  sh.getRange('H1').setValue(
    'Read-only. Both task sources in one list, overdue first, DONE rows left out. ' +
    'The last column reads Decision note for requests; point it at whichever column ' +
    'you would rather read.');
  sh.getRange('H1').setWrap(true);
  sh.setColumnWidth(8, 420);
  sh.setFrozenRows(1);
  return sh;
}

function buildTiersTab_(ss, notes) {
  var sh = tab_(ss, TABS.tiers, notes);
  if (String(sh.getRange('A1').getValue() || '').trim()) return sh;

  sh.getRange('A1:E1').setValues([[
    'VND', 'USD (approx)', 'Outcome (Tiếng Việt)', 'Outcome (English)', 'As of'
  ]]).setFontWeight('bold');

  sh.getRange('A2:E6').setValues([
    [50000, 2, 'Gạo cho một em trong một tháng, cộng hai hộp sữa',
      'Rice for one child for a month, plus two cartons of milk', '2026-11-13'],
    [100000, 4, 'Gạo cho ba em trong một tháng, hoặc dầu ăn và đường cho một lớp',
      'Rice for three children for a month, or a month of cooking oil and sugar for one class',
      '2026-11-13'],
    [250000, 10,
      'Gạo cho tám em trong một tháng, hoặc một giỏ bếp đầy đủ cho một lớp: dầu ăn, đường, nước mắm, bột nêm',
      'Rice for eight children for a month, or a full kitchen basket for one class: oil, sugar, fish sauce, seasoning',
      '2026-11-13'],
    [600000, 23, 'Gạo cho hai mươi em trong một tháng',
      'Rice for twenty children for a month', '2026-11-13'],
    [1300000, 50, 'Gạo cho bốn mươi em trong một tháng',
      'Rice for forty children for a month', '2026-11-13']
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
      'project; never total the column, because a sum across currencies means nothing.');
    progress.getRange('J1').setWrap(true);
    progress.setColumnWidth(10, 420);
    progress.setFrozenRows(1);
  }

  var chartTab = tab_(ss, TABS.progressChart, notes);
  if (!String(chartTab.getRange('A1').getValue() || '').trim()) {
    // A chart cannot filter its own rows, so the published tab is a QUERY over
    // the team's tab. Three things fall out of that: "Show on site = NO" hides
    // a project without deleting it, a project can be prepared privately, and
    // the tab that gets published to the web holds nothing but names and two
    // numbers.
    chartTab.getRange('A1').setFormula('={"Project","Raised","Remaining"}');
    chartTab.getRange('A2').setFormula(
      '=IFERROR(QUERY(' + TABS.progress + '!A2:F30,' +
      '"select Col1, Col3, Col4 where Col6 = \'YES\' and Col1 is not null",0),"")');
    chartTab.getRange('F1').setValue(
      'Published to the web. Nothing personal may ever be added to this tab.');
    chartTab.setFrozenRows(1);
  }

  if (chartTab.getCharts().length === 0) {
    SpreadsheetApp.flush();
    // Whole-column ranges with spare rows, so a project added at row 5 appears
    // in the chart without anyone editing the chart.
    chartTab.insertChart(chartTab.newChart()
      .setChartType(Charts.ChartType.BAR)
      .addRange(chartTab.getRange('A1:A30'))
      .addRange(chartTab.getRange('B1:C30'))
      .setOption('isStacked', true)
      .setOption('title', 'Vietnam Hearts - progress')
      .setOption('legend', { position: 'bottom' })
      .setOption('width', 560)
      .setOption('height', 300)
      .setPosition(2, 6, 0, 0)
      .build());
    notes.push('Built the stacked progress chart on "' + TABS.progressChart + '".');
  }

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
  manual.push('In the spreadsheet: Data -> **Filter views** -> create one per working ' +
    'state (TODO, IN PROGRESS, BLOCKED) and one for Priority = High. Apps Script has no ' +
    'filter-view API. Use filter views rather than a plain filter: a filter view is ' +
    'per-viewer, so two volunteers can filter differently at the same time.');
  manual.push('In the spreadsheet: File -> Settings -> **Time zone: Asia/Ho Chi Minh**, ' +
    'to match the script. A sheet left on a US time zone makes every "Next action by" ' +
    'date read a day off to the team in Vietnam.');
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

function reportBuild_(form, ss, notes, manual) {
  var prefilled = prefilledPartnershipUrl_(form);
  var lines = [
    '<p>The Vietnam Hearts request intake has been built. Three links to keep:</p>',
    '<ul>',
    '<li><b>The form, to share publicly</b>: <a href="' + form.getPublishedUrl() + '">' +
      form.getPublishedUrl() + '</a></li>',
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
  notes.forEach(function (n) { lines.push('<li>' + n + '</li>'); });
  lines.push('</ul>');

  lines.push('<h3>Manual steps, because Apps Script cannot do these</h3><ol>');
  manual.forEach(function (m) { lines.push('<li>' + m + '</li>'); });
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
