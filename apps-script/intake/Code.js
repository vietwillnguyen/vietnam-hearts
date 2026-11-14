/**
 * Vietnam Hearts - public request intake
 *
 * Bound to the "Vietnam Hearts - Requests" spreadsheet.
 * Two installable triggers, created once by setUp():
 *   onIntakeFormSubmit - stamps Status=TODO, sets Priority and Next action by,
 *                        acknowledges the submitter, notifies the team
 *   onIntakeEdit       - emails the submitter when DECISION becomes
 *                        Approved or Declined
 *
 * Do NOT rename onIntakeEdit to onEdit. A function named onEdit runs as a
 * SIMPLE trigger, and simple triggers cannot send email - it would fail
 * silently with nothing in any log a volunteer would read.
 * https://developers.google.com/apps-script/guides/triggers
 *
 * The Approved mail names the volunteer who owns the row and sets Reply-To to
 * that volunteer's address, read from the "Volunteers" tab. Mail is still sent
 * FROM the team Gmail, and no phone or Zalo number is ever put in an email.
 *
 * Every bilingual string reads "English / Tiếng Việt", in that order, matching
 * the form Build.js creates. The HEADER needles below are English fragments of
 * those titles, which is what keeps col_() resolving them; the same goes for
 * the "Donate supplies" test on the branch answer. test/harness.js matches
 * every needle against the real bilingual column titles.
 *
 * Nothing in this file approves anything. Every submission waits for a human,
 * including an offer of the five always-needed items: those are only flagged
 * Priority=High so they are answered first. Drop-offs are arranged one at a
 * time with a volunteer, so no automated mail ever states the school's street
 * address or hours. The only place any of them names is the public area,
 * Thạnh Mỹ Tây, TP.HCM.
 */

var CONFIG = {
  // Tab the form writes into. Google names it "Form Responses 1" by default.
  SHEET_NAME: 'Form Responses 1',

  // Where team notifications and error reports go.
  TEAM_EMAIL: 'vietnam.hearts.volunteering@gmail.com',

  // Tab and cell holding the bilingual handover + food-condition block quoted
  // in the acknowledgement. It lives in the sheet, not here, so a volunteer can
  // reword it without opening Apps Script. It must NOT contain the school
  // address: see section 7.5.3.
  TERMS_TAB: 'Supplies handover',
  TERMS_CELL: 'B2',

  // Tab mapping a volunteer's Owner name to their email address, headed
  // "Name" and "Email" in row 1. The decision mail sets Reply-To to that
  // address and the Approved mail names the person, so a submitter answers a
  // human rather than a shared inbox. Nothing else is read from this tab: no
  // phone number and no Zalo handle reaches an email, by construction.
  VOLUNTEERS_TAB: 'Volunteers',

  // Internal target, in days, written into "Next action by" on submit. This is
  // a sort key for the team, not a promise: the form makes none.
  TARGET_DAYS: 7,

  // Signature line in outbound email.
  ORG_NAME: 'Vietnam Hearts',

  // true = send everything to TEAM_EMAIL with a [DRY RUN] subject prefix
  // instead of to the submitter. Leave true until you have tested the flow.
  DRY_RUN: true,

  // Values of the DECISION column that email the submitter. The Status column
  // is deliberately not in this list: Status is how the team tracks work, and
  // moving a row to IN PROGRESS must never email anyone.
  DECISION_VALUES: ['Approved', 'Declined'],

  // Status the script moves a row to once its decision mail has gone out.
  STATUS_ON_APPROVED: 'IN PROGRESS',
  STATUS_ON_DECLINED: 'DONE',
  STATUS_ON_SUBMIT: 'TODO',

  // How long a decision edit waits for another edit's reply to finish before
  // giving up, in milliseconds. Two overlapping edits of one row must never
  // both read an empty Reply sent and both email the submitter.
  DECISION_LOCK_WAIT_MS: 20000
};

/** Header text fragments used to locate columns. Case-insensitive substring
 *  match; each must match exactly one column or the script throws. */
var HEADER = {
  status: 'Status',
  decision: 'Decision',       // must not also match "Decision note"
  priority: 'Priority',
  due: 'Next action by',
  owner: 'Owner',
  note: 'Decision note',
  sent: 'Reply sent',
  email: 'email',
  name: 'Your name',
  type: 'What would you like to offer',
  // The always-needed checkbox question. Its CHOICES are the list; this is
  // only how we find the column. Keep "Other" switched off on this question.
  standing: 'always need',
  // The free-text escape hatch. Anything here means more than the five items.
  other: 'Other items'
};

/** The Timestamp column, as Forms titles it in the spreadsheet's language.
 *  Kept out of HEADER so setUp() never fails on a locale it does not list;
 *  sweepUnstamped() is its only reader. */
var TIMESTAMP_HEADERS = ['Timestamp', 'Dấu thời gian'];

/** Run once, by hand, from the Apps Script editor. Safe to re-run. */
function setUp() {
  var ss = SpreadsheetApp.getActive();
  ScriptApp.getProjectTriggers().forEach(function (t) {
    ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('onIntakeFormSubmit').forSpreadsheet(ss).onFormSubmit().create();
  ScriptApp.newTrigger('onIntakeEdit').forSpreadsheet(ss).onEdit().create();

  // Fail now, loudly, if a header is missing or ambiguous, rather than on the
  // first real submission at 11pm.
  var headers = headerRow_();
  Object.keys(HEADER).forEach(function (key) {
    col_(headers, HEADER[key]);
  });

  var terms = termsBlockOrEmpty_();
  var volunteers = volunteerRowCount_();

  Logger.log('Installed %s triggers. All %s headers resolved. ' +
    'Handover block is %s characters. Volunteers tab has %s usable rows ' +
    '(-1 means missing or malformed, which only costs you the named ' +
    'volunteer, not the reply). DRY_RUN=%s',
    ScriptApp.getProjectTriggers().length,
    Object.keys(HEADER).length,
    terms.length,
    volunteers,
    CONFIG.DRY_RUN);
}

/** Usable rows on the Volunteers tab, or -1 when the tab is missing or its
 *  Name/Email headers do not resolve. For setUp() only, so a missing tab shows
 *  up at setup time rather than as an error mail during a real decision. */
function volunteerRowCount_() {
  try {
    var sh = SpreadsheetApp.getActive().getSheetByName(CONFIG.VOLUNTEERS_TAB);
    if (!sh) return -1;
    var headers = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0];
    col_(headers, 'Name');
    col_(headers, 'Email');
    return sh.getLastRow() - 1;
  } catch (err) {
    Logger.log('Volunteers tab unusable: %s', err);
    return -1;
  }
}

/* ---------------------------------------------------------------- helpers */

function sheet_() {
  var sh = SpreadsheetApp.getActive().getSheetByName(CONFIG.SHEET_NAME);
  if (!sh) throw new Error('No sheet named "' + CONFIG.SHEET_NAME + '"');
  return sh;
}

function headerRow_() {
  var sh = sheet_();
  return sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0];
}

/** 0-based index of the one column whose header contains `needle`.
 *  An exact header match wins outright, so "Decision" resolves even though
 *  "Decision note" also contains it. */
function col_(headers, needle) {
  var want = String(needle).toLowerCase();
  var exact = [];
  var partial = [];
  for (var i = 0; i < headers.length; i++) {
    var h = String(headers[i]).trim().toLowerCase();
    if (h === want) exact.push(i);
    else if (h.indexOf(want) !== -1) partial.push(i);
  }
  if (exact.length === 1) return exact[0];
  if (exact.length > 1) {
    throw new Error('Two columns are both headed exactly "' + needle + '"');
  }
  if (partial.length === 0) {
    throw new Error('No column header contains "' + needle + '"');
  }
  if (partial.length > 1) {
    throw new Error('"' + needle + '" matches ' + partial.length +
      ' columns; make HEADER entry more specific or rename a column');
  }
  return partial[0];
}

function rowValues_(sh, rowNumber) {
  return sh.getRange(rowNumber, 1, 1, sh.getLastColumn()).getValues()[0];
}

/** One place that decides who actually receives mail.
 *  `replyTo` is optional and only ever an email address: the sender stays the
 *  team Gmail, so a volunteer's address is exposed for a reply and nothing
 *  else. DRY_RUN redirects the recipient but keeps Reply-To, because getting
 *  that header wrong is one of the things a dry run is for. */
function send_(to, subject, htmlBody, replyTo) {
  if (!to) return false;
  var remaining = MailApp.getRemainingDailyQuota();
  if (remaining < 3) {
    // Consumer Gmail allows 100 recipients/day. Hitting this means something
    // is looping, because real intake volume is a handful a week.
    Logger.log('Email quota nearly exhausted (%s left); not sending.', remaining);
    return false;
  }
  var options = { to: to, subject: subject, htmlBody: htmlBody };
  if (replyTo) options.replyTo = replyTo;
  if (CONFIG.DRY_RUN) {
    options.to = CONFIG.TEAM_EMAIL;
    options.subject = '[DRY RUN -> ' + to + '] ' + subject;
  }
  MailApp.sendEmail(options);
  return true;
}

/** Team-facing mail. Goes to the team whatever DRY_RUN says, because it is
 *  already addressed to the team, but still respects the quota guard. */
function notifyTeam_(subject, htmlBody) {
  if (MailApp.getRemainingDailyQuota() < 3) {
    Logger.log('Quota nearly exhausted; team notification not sent: %s', subject);
    return;
  }
  MailApp.sendEmail({ to: CONFIG.TEAM_EMAIL, subject: subject, htmlBody: htmlBody });
}

/** The handover and food-condition block, read from the sheet so a volunteer
 *  owns the wording. Returns '' and reports if the tab or cell is missing:
 *  nothing is gated on it, because nothing is auto-approved.
 *  white-space:pre-line keeps the volunteer's line breaks from the cell. */
function termsBlockOrEmpty_() {
  try {
    var sh = SpreadsheetApp.getActive().getSheetByName(CONFIG.TERMS_TAB);
    if (!sh) throw new Error('No tab named "' + CONFIG.TERMS_TAB + '"');
    var text = String(sh.getRange(CONFIG.TERMS_CELL).getValue() || '').trim();
    if (!text) {
      throw new Error('Cell ' + CONFIG.TERMS_CELL + ' of "' + CONFIG.TERMS_TAB +
        '" is empty. It must hold the handover and food-condition wording.');
    }
    return '<p style="padding:12px;background:#f5f1e8;border-left:4px solid #7a8c6b;' +
      'white-space:pre-line">' + escape_(text) + '</p>';
  } catch (err) {
    reportError_('termsBlockOrEmpty_', err);
    return '';
  }
}

/** Resolve the row's Owner name to a volunteer's email address.
 *  Returns { name: '', email: CONFIG.TEAM_EMAIL } whenever it cannot, so a
 *  gap in the Volunteers tab sends the reply to the team instead of dropping
 *  it, and reports the gap by email so somebody fixes the tab.
 *
 *  `reportWhenUnassigned` is true only for Approved: that mail promises a
 *  person will make contact, so a blank Owner is a real gap there. A Declined
 *  mail promises nothing, and reporting every unassigned decline would spend
 *  the 100-a-day email quota on noise.
 *
 *  Only Name and Email are read. The tab may hold other columns; nothing here
 *  can put a phone number or a Zalo handle into an email. */
function ownerContact_(ownerName, reportWhenUnassigned) {
  var fallback = { name: '', email: CONFIG.TEAM_EMAIL };
  var name = String(ownerName || '').trim();
  if (!name) {
    if (reportWhenUnassigned) {
      reportError_('ownerContact_', new Error(
        'Row approved with no Owner. The submitter was told a volunteer will ' +
        'be in touch, but nobody is named and the reply goes to the team.'));
    }
    return fallback;
  }
  try {
    var sh = SpreadsheetApp.getActive().getSheetByName(CONFIG.VOLUNTEERS_TAB);
    if (!sh) throw new Error('No tab named "' + CONFIG.VOLUNTEERS_TAB + '"');
    if (sh.getLastRow() < 2) {
      throw new Error('"' + CONFIG.VOLUNTEERS_TAB + '" has a header row and nothing else');
    }
    var values = sh.getRange(1, 1, sh.getLastRow(), sh.getLastColumn()).getValues();
    var cName = col_(values[0], 'Name');
    var cEmail = col_(values[0], 'Email');
    for (var i = 1; i < values.length; i++) {
      if (String(values[i][cName]).trim().toLowerCase() !== name.toLowerCase()) continue;
      var email = String(values[i][cEmail] || '').trim();
      if (!email) {
        throw new Error('"' + name + '" is on the ' + CONFIG.VOLUNTEERS_TAB +
          ' tab with no email address');
      }
      return { name: name, email: email };
    }
    throw new Error('No row for Owner "' + name + '" on the ' +
      CONFIG.VOLUNTEERS_TAB + ' tab');
  } catch (err) {
    reportError_('ownerContact_', err);
    return fallback;
  }
}

function reportError_(where, err) {
  Logger.log('%s failed: %s', where, err && err.stack ? err.stack : err);
  try {
    MailApp.sendEmail({
      to: CONFIG.TEAM_EMAIL,
      subject: '[Requests sheet] ' + where + ' failed',
      htmlBody: '<p>The request intake script hit an error.</p><pre>' +
        String(err && err.stack ? err.stack : err) + '</pre>'
    });
  } catch (ignored) {}
}

function rowLink_(rowNumber) {
  var ss = SpreadsheetApp.getActive();
  return ss.getUrl() + '#gid=' + sheet_().getSheetId() + '&range=A' + rowNumber;
}

/* ------------------------------------------------------------- on a submit */

function onIntakeFormSubmit(e) {
  try {
    var sh = sheet_();
    var rowNumber = e && e.range ? e.range.getRow() : sh.getLastRow();
    processSubmissionRow_(sh, headerRow_(), rowNumber);
  } catch (err) {
    reportError_('onIntakeFormSubmit', err);
  }
}

/** Everything a new submission causes: the three stamps, the submitter's
 *  acknowledgement and the team notice. Separated from the trigger so
 *  sweepUnstamped() can give a missed row the identical treatment rather
 *  than a second, drifting copy of it. The row is re-read under the document
 *  lock, so when the trigger and a sweep race for one row only the first
 *  sends anything. Returns true when it processed the row. */
function processSubmissionRow_(sh, headers, rowNumber) {
  var lock = LockService.getDocumentLock();
  if (!lock.tryLock(CONFIG.DECISION_LOCK_WAIT_MS)) {
    reportError_('Submission handling', new Error('Row ' + rowNumber + ': another ' +
      'run was still handling a submission, so this one sent nothing. If Status, ' +
      'Priority and Next action by are still empty, run sweepUnstamped().'));
    return false;
  }
  try {
    var row = rowValues_(sh, rowNumber);
    if (stampsFilled_(row, headers) === 3) return false;
    processUnstampedRow_(sh, headers, rowNumber, row);
    SpreadsheetApp.flush();
    return true;
  } finally {
    lock.releaseLock();
  }
}

/** How many of the three submit stamps - Status, Priority, Next action by -
 *  the row holds. 3 is a handled row; 1 or 2 is a half-written one. */
function stampsFilled_(row, headers) {
  return [HEADER.status, HEADER.priority, HEADER.due].filter(function (needle) {
    return String(row[col_(headers, needle)] || '').trim() !== '';
  }).length;
}

function processUnstampedRow_(sh, headers, rowNumber, row) {

  var cStatus = col_(headers, HEADER.status);
  var cPriority = col_(headers, HEADER.priority);
  var cDue = col_(headers, HEADER.due);
  var cEmail = col_(headers, HEADER.email);
  var cName = col_(headers, HEADER.name);
  var cType = col_(headers, HEADER.type);
  var cStand = col_(headers, HEADER.standing);
  var cOther = col_(headers, HEADER.other);

  var name = String(row[cName] || '').trim();
  var email = String(row[cEmail] || '').trim();
  var type = String(row[cType] || '').trim() || 'Other / Khac';

  // `ticked` is whatever Forms joined together from the checkbox question.
  // Nothing parses it, because every choice on that question is already an
  // always-needed item (section 7.5.2).
  var ticked = String(row[cStand] || '').trim();
  var other = String(row[cOther] || '').trim();

  // The fast lane. Only the five items and nothing written in the free-text
  // box means the answer is a yes in principle, so it jumps the queue - but a
  // volunteer still confirms it and arranges the handover.
  var fastLane = ticked !== '' && other === '';

  if (!String(row[cStatus]).trim()) {
    sh.getRange(rowNumber, cStatus + 1).setValue(CONFIG.STATUS_ON_SUBMIT);
  }
  if (!String(row[cPriority]).trim()) {
    sh.getRange(rowNumber, cPriority + 1).setValue(fastLane ? 'High' : 'Normal');
  }
  if (!String(row[cDue]).trim()) {
    var due = new Date();
    due.setDate(due.getDate() + CONFIG.TARGET_DAYS);
    sh.getRange(rowNumber, cDue + 1).setValue(due);
  }
  afterStamp_(sh, rowNumber);

  var terms = ticked ? termsBlockOrEmpty_() : '';

  var acked = send_(email,
    'Vietnam Hearts: we have your offer / da nhan de xuat cua ban',
    ackHtml_(name, ticked, terms, fastLane, type));
  if (email && !acked) {
    reportError_('onIntakeFormSubmit', new Error('Row ' + rowNumber +
      ': the acknowledgement was not sent (email quota nearly exhausted).'));
  }

  notifyTeam_(
    (fastLane ? 'FAST LANE: ' : '') +
    'New request (' + type + ') from ' + (name || 'someone'),
    (fastLane
      ? '<p><b>Always-needed items only (' + escape_(ticked) + ').</b> ' +
        'Flagged Priority=High. The answer is a yes in principle, but it still ' +
        'needs a volunteer to confirm it and agree a handover - nobody has been ' +
        'given a time or a place.</p>'
      : '<p><b>' + escape_(type) + '</b> from ' + escape_(name || 'unknown') + '</p>') +
    (ticked && other
      ? '<p><b>Mixed.</b> Always-needed items (' + escape_(ticked) + ') plus ' +
        'something else they typed in. Deal with both in one reply.</p>'
      : '') +
    (ticked === '' && other === '' && type.indexOf('Donate supplies') !== -1
      ? '<p>Supplies request with nothing listed. Ask them what they have.</p>'
      : '') +
    '<p>Row ' + rowNumber + ': <a href="' + rowLink_(rowNumber) +
    '">open it in the sheet</a></p>' +
    '<p>Put one line in <b>Decision note</b> first - that line is what the ' +
    'person receives - then set <b>Decision</b> to Approved or Declined. ' +
    'Changing <b>Status</b> emails nobody.</p>');
}

/** The new row's dropdowns and date formats. Forms pushes the build's
 *  validation ranges below every row it inserts (see applyTeamRowRules_ in
 *  Build.js), so the row that just arrived has none until this writes them.
 *  Build.js shares this script's global scope in Apps Script; the Code.js-only
 *  test harness has no Build.js, hence the typeof. Never fatal: a row without
 *  a dropdown is still a handled row. */
function afterStamp_(sh, rowNumber) {
  if (typeof applyTeamRowRules_ !== 'function') return;
  try {
    applyTeamRowRules_(sh, rowNumber, 1);
  } catch (err) {
    reportError_('Dropdowns for row ' + rowNumber, err);
  }
}

/** Repair rows the form wrote while the installed triggers were not running.
 *
 *  A trigger stops firing whenever the project's authorization goes stale,
 *  and the one change that does that silently is a new OAuth scope: an
 *  advanced service added to appsscript.json and pushed with clasp leaves
 *  every installed trigger dead until the owner opens the editor and
 *  re-authorizes. Live pass 3 lost a real submission that way. Nothing in
 *  the sheet says so - the row is simply there with a Timestamp and nothing
 *  else - so this is the only way those people ever hear back.
 *
 *  Exported on purpose, and also called at the end of buildIntake(), because
 *  running buildIntake() in the editor is exactly what re-authorizing looks
 *  like: the repair then happens in the same step that fixes the cause.
 *
 *  What counts as missed is deliberately narrower than "Status is empty".
 *  Status alone cannot tell a row the trigger never saw from one a volunteer
 *  cleared by hand, and guessing wrong emails a member of the public twice.
 *  The trigger writes three cells together, so all three must be empty, and
 *  a Timestamp must be present or the row is just a blank line in the sheet.
 *
 *  Returns the number of rows it repaired. */
function sweepUnstamped() {
  var sh = sheet_();
  var lastRow = sh.getLastRow();
  if (lastRow < 2) return 0;

  var headers = headerRow_();
  var cStamp = timestampCol_(headers);
  if (cStamp === -1) {
    reportError_('sweepUnstamped', new Error('No column is headed ' +
      TIMESTAMP_HEADERS.join(' or ') + ', so no missed submission could be ' +
      'identified and none was repaired.'));
    return 0;
  }

  var repaired = 0;
  var values = sh.getRange(2, 1, lastRow - 1, sh.getLastColumn()).getValues();
  for (var i = 0; i < values.length; i++) {
    var row = values[i];
    if (!row || !row.length) continue;
    if (String(row[cStamp] || '').trim() === '') continue;
    if (stampsFilled_(row, headers) !== 0) continue;

    var rowNumber = i + 2;
    try {
      if (processSubmissionRow_(sh, headers, rowNumber)) repaired++;
    } catch (err) {
      reportError_('sweepUnstamped row ' + rowNumber, err);
    }
  }
  if (repaired) {
    Logger.log('sweepUnstamped repaired %s row(s).', repaired);
  }
  return repaired;
}

/** 0-based index of the Timestamp column in any spelling Forms uses, or -1. */
function timestampCol_(headers) {
  for (var i = 0; i < TIMESTAMP_HEADERS.length; i++) {
    try {
      return col_(headers, TIMESTAMP_HEADERS[i]);
    } catch (ignored) { /* not this locale's spelling; try the next */ }
  }
  return -1;
}

/* ------------------------------------------ on a change in the DECISION cell */

function onIntakeEdit(e) {
  try {
    if (!e || !e.range) return;
    var sh = e.range.getSheet();
    if (sh.getName() !== CONFIG.SHEET_NAME) return;

    var headers = headerRow_();
    var cDecision = col_(headers, HEADER.decision);

    // Only a change that covers the Decision column can send anything. Status
    // changes (TODO -> IN PROGRESS -> DONE) are the team's own task tracking
    // and must never reach a submitter. A drag-fill or paste can cover many
    // rows and start left of Decision, so every row of the range is checked.
    if (e.range.getColumn() > cDecision + 1 ||
        e.range.getLastColumn() < cDecision + 1) return;

    for (var r = Math.max(e.range.getRow(), 2); r <= e.range.getLastRow(); r++) {
      try {
        decideRow_(sh, headers, r);
      } catch (err) {
        reportError_('onIntakeEdit row ' + r, err);
      }
    }
  } catch (err) {
    reportError_('onIntakeEdit', err);
  }
}

/** True when the row holds a decision that has not been emailed yet. */
function awaitingReply_(row, headers) {
  var decision = String(row[col_(headers, HEADER.decision)] || '').trim();
  if (CONFIG.DECISION_VALUES.indexOf(decision) === -1) return false;
  // Already decided and emailed. Flipping the dropdown must not re-email.
  if (String(row[col_(headers, HEADER.sent)] || '').trim()) return false;
  return !!String(row[col_(headers, HEADER.email)] || '').trim();
}

function decideRow_(sh, headers, rowNumber) {
  if (!awaitingReply_(rowValues_(sh, rowNumber), headers)) return;

  var lock = LockService.getDocumentLock();
  if (!lock.tryLock(CONFIG.DECISION_LOCK_WAIT_MS)) {
    reportError_('onIntakeEdit', new Error('Row ' + rowNumber + ': another edit was ' +
      'still sending a reply, so this one sent nothing. If Reply sent is still empty, ' +
      'set Decision again to send it.'));
    return;
  }
  try {
    // Read again under the lock: an overlapping edit may have stamped it.
    var row = rowValues_(sh, rowNumber);
    if (!awaitingReply_(row, headers)) return;
    replyToDecision_(sh, headers, rowNumber, row);
    SpreadsheetApp.flush();
  } finally {
    lock.releaseLock();
  }
}

function replyToDecision_(sh, headers, rowNumber, row) {
  var cDecision = col_(headers, HEADER.decision);
  var cStatus = col_(headers, HEADER.status);
  var cEmail = col_(headers, HEADER.email);
  var cName = col_(headers, HEADER.name);
  var cNote = col_(headers, HEADER.note);
  var cSent = col_(headers, HEADER.sent);
  var cOwner = col_(headers, HEADER.owner);

  var decision = String(row[cDecision] || '').trim();
  var email = String(row[cEmail] || '').trim();
  var name = String(row[cName] || '').trim();
  var note = String(row[cNote] || '').trim();

  // Reply-To on both outcomes, so an answer reaches the volunteer who made
  // the call. Only Approved names them, because only Approved promises
  // contact.
  var owner = ownerContact_(row[cOwner], decision === 'Approved');
  var sent = send_(email,
    decisionSubject_(decision),
    decisionHtml_(decision, name, note, owner.name),
    owner.email);
  if (!sent) {
    reportError_('onIntakeEdit', new Error('Row ' + rowNumber + ': the ' +
      decision + ' email was not sent (email quota nearly exhausted). Reply sent ' +
      'is still empty; set Decision again tomorrow to send it.'));
    return;
  }
  sh.getRange(rowNumber, cSent + 1).setValue(new Date());
  sh.getRange(rowNumber, cStatus + 1).setValue(decision === 'Approved'
    ? CONFIG.STATUS_ON_APPROVED
    : CONFIG.STATUS_ON_DECLINED);
}

/* -------------------------------------------------------------- email bodies */

function escape_(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function footerHtml_() {
  return '<p style="color:#6b4f3a">' +
    'A volunteer will contact you. If you have a question before then, reply to this email.<br>' +
    'Một tình nguyện viên sẽ liên hệ với bạn. Nếu bạn có câu hỏi trước đó, xin trả lời email này.</p>' +
    '<p style="color:#8b7355">' + escape_(CONFIG.ORG_NAME) + '</p>';
}

/** The decision mails go to a member of the public, so they offer no group
 *  chat at all: next steps go through a reply, which reaches the owner via
 *  the Reply-To. */
function decisionFooterHtml_() {
  return '<p style="color:#6b4f3a">' +
    'Please reply to this email for next steps.<br>' +
    'Xin bạn trả lời email này để trao đổi các bước tiếp theo.</p>' +
    '<p style="color:#8b7355">' + escape_(CONFIG.ORG_NAME) + '</p>';
}

/** The one automated mail a submitter receives. It promises no date, because
 *  the form does not either, and it never states the school's address or
 *  hours, only the public area: a volunteer arranges each handover by hand. */
function ackHtml_(name, ticked, terms, fastLane, type) {
  var hi = name ? ' ' + escape_(name) : '';

  // Photos are asked for here rather than with a file upload question on the
  // form: Google requires a Google sign-in for any form that has one, for the
  // whole form from page one, which is the login wall the second live build
  // hit. Supplies only - a partnership or an event has nothing to photograph.
  // It points at a reply to this email and nowhere else: no mail to a member
  // of the public names a group chat or a team number, because a volunteer
  // reaches out to them.
  var supplies = String(type || '').indexOf('Donate supplies') !== -1;
  var photoEn = supplies
    ? '<p>Photos of what you are giving help us plan. Please reply to this email ' +
      'with them.</p>'
    : '';
  var photoVi = supplies
    ? '<p>Ảnh của đồ bạn tặng giúp chúng tôi chuẩn bị. Xin bạn trả lời email này ' +
      'kèm theo ảnh.</p>'
    : '';
  var fastEn = '', fastVi = '';
  if (fastLane && ticked) {
    // The handover block is itself bilingual and is quoted once, with the
    // English half of the mail, so the whole message reads English then
    // Vietnamese top to bottom.
    fastEn =
      '<p>What you picked (' + escape_(ticked) + ') is on our <b>always needed</b> ' +
      'list, so it is almost certainly a yes. A volunteer will message you to ' +
      'agree a time and place for the handover - please wait for that before ' +
      'bringing anything.</p>' + terms;
    fastVi =
      '<p>Những món bạn chọn (' + escape_(ticked) + ') nằm trong danh sách ' +
      '<b>chúng tôi luôn cần</b>, nên gần như chắc chắn chúng tôi nhận. ' +
      'Một tình nguyện viên sẽ nhắn bạn để hẹn giờ và địa điểm nhận đồ - ' +
      'xin đừng mang đến trước khi hẹn xong.</p>';
  } else if (ticked) {
    fastEn =
      '<p>Part of your offer (' + escape_(ticked) + ') is on our always-needed ' +
      'list. We will come back to you about that and the rest together.</p>' + terms;
    fastVi =
      '<p>Một phần trong đề xuất của bạn (' + escape_(ticked) + ') nằm trong ' +
      'danh sách chúng tôi luôn cần. Chúng tôi sẽ trả lời cả phần đó và phần ' +
      'còn lại trong một tin nhắn.</p>';
  }
  return '' +
    '<p>Hello' + hi + ',</p>' +
    '<p>Thank you for your offer to Vietnam Hearts. We have it. We are a ' +
    'volunteer team, so a reply can take a little while - a volunteer will ' +
    'come back to you.</p>' +
    fastEn +
    photoEn +
    '<hr>' +
    '<p>Xin chào' + hi + ',</p>' +
    '<p>Cảm ơn bạn đã gửi đề xuất cho Vietnam Hearts. Chúng tôi đã nhận được. ' +
    'Chúng tôi là nhóm tình nguyện, nên xin bạn thông cảm nếu phản hồi mất ít ' +
    'thời gian - một tình nguyện viên sẽ liên hệ với bạn.</p>' +
    fastVi +
    photoVi +
    footerHtml_();
}

function decisionSubject_(decision) {
  return decision === 'Approved'
    ? 'Vietnam Hearts: we would love to go ahead / chung toi muon tiep tuc'
    : 'Vietnam Hearts: about your request / phan hoi ve de xuat cua ban';
}

function decisionHtml_(decision, name, note, ownerName) {
  var hi = name ? ' ' + escape_(name) : '';
  var who = ownerName ? escape_(ownerName) : '';
  var noteHtml = note
    ? '<p style="padding:12px;background:#f5f1e8;border-left:4px solid #7a8c6b">' +
      escape_(note) + '</p>'
    : '';

  // The volunteer's own note is quoted once, with the English half, for the
  // same reason the handover block is: one copy, highest up it can sit.
  if (decision === 'Approved') {
    return '' +
      '<p>Hello' + hi + ',</p>' +
      '<p>We would love to go ahead. ' +
      (who
        ? who + ' from Vietnam Hearts will contact you to agree a time and a place. '
        : 'A volunteer from Vietnam Hearts will contact you to agree a time and ' +
          'a place. ') +
      'You can also reply straight to this email.</p>' +
      noteHtml +
      '<hr>' +
      '<p>Xin chào' + hi + ',</p>' +
      '<p>Chúng tôi rất vui được tiếp tục với đề xuất của bạn. ' +
      (who
        ? who + ' từ Vietnam Hearts sẽ liên hệ với bạn để hẹn giờ và địa điểm cụ thể. '
        : 'Một tình nguyện viên của Vietnam Hearts sẽ liên hệ với bạn để hẹn giờ ' +
          'và địa điểm cụ thể. ') +
      'Bạn cũng có thể trả lời ngay email này.</p>' +
      decisionFooterHtml_();
  }

  return '' +
    '<p>Hello' + hi + ',</p>' +
    '<p>Thank you for thinking of Vietnam Hearts. We are not able to take this ' +
    'one on, usually because of storage, volunteer time, or how it fits what the ' +
    'children need right now. Please do stay in touch.</p>' +
    noteHtml +
    '<hr>' +
    '<p>Xin chào' + hi + ',</p>' +
    '<p>Cảm ơn bạn đã nghĩ đến Vietnam Hearts. Lần này chúng tôi chưa thể nhận ' +
    'đề xuất này, thường là vì chỗ chứa, thời gian của tình nguyện viên, hoặc vì ' +
    'chưa phù hợp với nhu cầu hiện tại của các em. Mong bạn vẫn giữ liên lạc.</p>' +
    decisionFooterHtml_();
}
