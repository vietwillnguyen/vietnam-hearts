/*
 * Stub harness for the intake script.
 *
 * Runs the real handlers from Code.js against a fake SpreadsheetApp / MailApp
 * in a `vm` context. No Google account, no network, no credential: `node
 * test/harness.js Code.js` is the whole contract, which is why CI can run it.
 *
 * It prints a readable transcript AND asserts, so a regression fails the job
 * rather than quietly changing the output nobody reads. Exit code 1 means at
 * least one check failed; the failures are also listed at the end.
 *
 * The properties under test are mostly negative - "editing Status emails
 * nobody", "a decision mail carries no handover block", "no email contains a
 * street address or a phone number" - and negative properties are exactly what
 * a silent refactor breaks. Naming the public area, Thạnh Mỹ Tây, is allowed.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { ADDRESS, MUST_MATCH, MUST_NOT_MATCH } = require('./address-shape');

const CODE = process.argv[2] || path.join(__dirname, '..', 'Code.js');

/* The form-response grid. Order matters only in that the script must not
   depend on it: every column is found by header text. */
const HEADERS = [
  'Status', 'Decision', 'Priority', 'Next action by', 'Owner',
  'Decision note', 'Reply sent',
  'Timestamp', 'Email Address', 'Your name',
  'What would you like to offer?',
  'We always need these - are you giving any of them?',
  'Other items not on the list above'
];
const H = {};
HEADERS.forEach((h, i) => (H[h] = i));

const DONOR = 'donor@example.com';
const TEAM = 'vietnam.hearts.volunteering@gmail.com';

/* The handover wording a volunteer would type into 'Standing needs'!B2. It
   names the public area, which is allowed, and that string does double duty:
   it proves the acknowledgement DID include the block, and it proves the
   decision mail did NOT, because the decision path never reads that cell. */
const TERMS = 'Chúng tôi hẹn từng lần nhận đồ. Khu vực của chúng tôi: Thạnh Mỹ Tây, TP.HCM.\n' +
  'Xin con nguyen seal, con han 3 thang.';
const TERMS_MARK = /Thạnh Mỹ Tây/;

/* Anything shaped like a Vietnamese phone number. No automated mail may carry
   one: the Volunteers tab maps a name to an email and nothing else. */
const PHONE = /(?:\+?84|0)(?:\d[ .\-]?){8,10}\d/;

/* ------------------------------------------------------------------ world */

function makeWorld(opts) {
  opts = opts || {};
  const row = new Array(HEADERS.length).fill('');
  row[H['Timestamp']] = new Date('2026-11-13T09:00:00Z');
  row[H['Email Address']] = DONOR;
  row[H['Your name']] = 'Linh';
  row[H['What would you like to offer?']] =
    opts.type === undefined ? 'Tặng đồ dùng, vật phẩm / Donate supplies or goods' : opts.type;
  row[H['We always need these - are you giving any of them?']] = opts.ticked || '';
  row[H['Other items not on the list above']] = opts.other || '';
  Object.assign(row, opts.preset || {});

  const grid = [HEADERS.slice()];
  for (let i = 0; i < (opts.rows || 1); i++) grid.push(row.slice());
  const mails = [];
  const logs = [];

  const responses = {
    getName: () => 'Form Responses 1',
    getLastColumn: () => HEADERS.length,
    getLastRow: () => grid.length,
    getSheetId: () => 1234,
    getRange: (r, c, nr, nc) => ({
      getValues: () => [grid[r - 1].slice(c - 1, c - 1 + (nc || 1))],
      setValue: (v) => { grid[r - 1][c - 1] = v; },
      getRow: () => r,
      getColumn: () => c,
      getLastRow: () => r + (nr || 1) - 1,
      getLastColumn: () => c + (nc || 1) - 1,
      getSheet: () => responses
    })
  };

  const termsTab = {
    getName: () => 'Standing needs',
    getRange: () => ({
      getValue: () => (opts.terms === undefined ? TERMS : opts.terms)
    })
  };

  /* Volunteers tab: Name -> Email, plus a Notes column that is never read,
     to prove the script takes only the two columns it asks for. */
  const volunteerRows = opts.volunteers === undefined
    ? [['Name', 'Email', 'Notes'], ['Mai', 'mai@example.com', 'Zalo 0912 345 678']]
    : [['Name', 'Email', 'Notes']].concat(opts.volunteers);
  const volunteersTab = {
    getName: () => 'Volunteers',
    getLastRow: () => volunteerRows.length,
    getLastColumn: () => 3,
    getRange: (r, c, nr, nc) => ({
      getValues: () => volunteerRows
        .slice(r - 1, r - 1 + (nr || 1))
        .map((vr) => vr.slice(c - 1, c - 1 + (nc || 1)))
    })
  };

  const sandbox = {
    SpreadsheetApp: {
      getActive: () => ({
        getUrl: () => 'https://docs.google.com/spreadsheets/d/FAKE/edit',
        getSheetByName: (n) => {
          if (n === 'Form Responses 1') return responses;
          if (n === 'Standing needs') return opts.noTermsTab ? null : termsTab;
          if (n === 'Volunteers') return opts.noVolunteersTab ? null : volunteersTab;
          return null;
        }
      })
    },
    MailApp: {
      getRemainingDailyQuota: () => (opts.quota === undefined ? 90 : opts.quota),
      sendEmail: (o) => mails.push(o)
    },
    Logger: { log: (...a) => logs.push(a.join(' ')) },
    ScriptApp: {
      getProjectTriggers: () => [],
      deleteTrigger: () => {},
      newTrigger: () => ({
        forSpreadsheet: () => ({
          onFormSubmit: () => ({ create: () => {} }),
          onEdit: () => ({ create: () => {} })
        })
      })
    },
    console
  };

  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(CODE, 'utf8'), sandbox);
  sandbox.CONFIG.DRY_RUN = false;   // show real recipients in the transcript
  return { sandbox, grid, mails, logs, responses };
}

/* ------------------------------------------------------------------ checks */

const failures = [];

function check(label, ok, detail) {
  if (ok) {
    console.log(`   ok   ${label}`);
    return;
  }
  const line = `${label}${detail ? ' -- ' + detail : ''}`;
  console.log(`   FAIL ${line}`);
  failures.push(line);
}

/* The script runs inside a `vm` realm, so a Date it creates is NOT an
   instanceof this realm's Date. Duck-type instead of reaching for the
   constructor: cross-realm instanceof is the classic vm gotcha. */
function isDate(v) {
  return !!v && typeof v === 'object' && typeof v.getTime === 'function' &&
    !isNaN(v.getTime());
}

function donorMails(w) { return w.mails.filter((m) => m.to === DONOR); }
function teamMails(w) { return w.mails.filter((m) => m.to !== DONOR); }

function show(label, w) {
  const r = w.grid[1];
  const donor = donorMails(w);
  const due = r[H['Next action by']];
  console.log(`\n--- ${label}`);
  console.log(`   Status=${JSON.stringify(r[H['Status']])} ` +
    `Decision=${JSON.stringify(r[H['Decision']])} ` +
    `Priority=${JSON.stringify(r[H['Priority']])} ` +
    `NextActionBy=${due ? String(due).slice(0, 15) : 'empty'} ` +
    `ReplySent=${r[H['Reply sent']] ? 'STAMPED' : 'empty'}`);
  console.log(`   donor mails: ${donor.length}` +
    (donor.length ? `; carries handover block: ${
      TERMS_MARK.test(donor.map((m) => m.htmlBody).join('')) ? 'YES' : 'no'}` : '') +
    (donor.length && donor[0].replyTo ? `; replyTo=${donor[0].replyTo}` : ''));
  teamMails(w).forEach((m) => console.log(`   team: ${m.subject}`));
}

/* Every mail any case produced, so the phone rule is checked once, globally,
   rather than case by case where a new case could forget it. */
const allMails = [];
function record(w) { allMails.push.apply(allMails, w.mails); return w; }

function submit(w) {
  w.sandbox.onIntakeFormSubmit({ range: w.responses.getRange(2, 1, 1, 1) });
  return record(w);
}
function editCell(w, column) {
  return editRange(w, 2, column, 1, 1);
}
function editRange(w, row, column, numRows, numColumns) {
  w.sandbox.onIntakeEdit({
    range: w.responses.getRange(row, column + 1, numRows, numColumns)
  });
  return record(w);
}

/* ------------------------------------------------------------------- cases */

let w;

// A: five-items-only submission
w = submit(makeWorld({ ticked: 'Dau an, Duong' }));
show('A. always-needed only  -> TODO, Priority High, FAST LANE, no Decision', w);
check('A status is TODO', w.grid[1][H['Status']] === 'TODO');
check('A priority is High', w.grid[1][H['Priority']] === 'High');
check('A Decision is untouched', w.grid[1][H['Decision']] === '');
check('A Next action by is filled', isDate(w.grid[1][H['Next action by']]));
check('A one donor mail', donorMails(w).length === 1);
check('A acknowledgement carries the handover block',
  TERMS_MARK.test(donorMails(w)[0].htmlBody));
check('A team mail is flagged FAST LANE',
  teamMails(w).some((m) => m.subject.indexOf('FAST LANE: ') === 0));

// B: mixed - always-needed items plus something typed in
w = submit(makeWorld({ ticked: 'Dau an', other: '3 cai ban hoc' }));
show('B. mixed               -> TODO, Priority Normal, block still shown', w);
check('B priority is Normal', w.grid[1][H['Priority']] === 'Normal');
check('B acknowledgement still carries the block',
  TERMS_MARK.test(donorMails(w)[0].htmlBody));
check('B team mail is not fast lane',
  teamMails(w).every((m) => m.subject.indexOf('FAST LANE') === -1));

// C: nothing ticked
w = submit(makeWorld({ other: '40 thung sach cu' }));
show('C. no always-needed    -> TODO, Priority Normal, no block', w);
check('C status is TODO', w.grid[1][H['Status']] === 'TODO');
check('C priority is Normal', w.grid[1][H['Priority']] === 'Normal');
check('C no handover block', !TERMS_MARK.test(donorMails(w)[0].htmlBody));

// C2: the supplies branch with nothing ticked and nothing typed, using the
//     branch's real choice text
w = submit(makeWorld());
show('C2. supplies, nothing listed -> team is told to ask what they have', w);
check('C2 the team mail asks what they have',
  teamMails(w).some((m) => /Supplies request with nothing listed/.test(m.htmlBody)));
w = submit(makeWorld({ type: 'Đề xuất sự kiện / Propose an event' }));
check('C2 another branch with nothing listed gets no supplies hint',
  teamMails(w).every((m) => !/Supplies request with nothing listed/.test(m.htmlBody)));

// D: the handover wording is missing from the sheet - fail soft
w = submit(makeWorld({ ticked: 'Dau an', terms: '' }));
show('D. handover cell empty -> TODO anyway, error mail, nothing approved', w);
check('D submission still accepted', w.grid[1][H['Status']] === 'TODO');
check('D Decision still empty', w.grid[1][H['Decision']] === '');
check('D donor still acknowledged', donorMails(w).length === 1);
check('D team told which function failed',
  teamMails(w).some((m) => /termsBlockOrEmpty_/.test(m.subject)));

// E: a team task status change must email nobody. THE property that lets one
//    spreadsheet hold both intake rows and ordinary team tasks.
w = editCell(makeWorld({ ticked: 'Dau an', preset: { [H['Status']]: 'IN PROGRESS' } }),
  H['Status']);
show('E. edit Status cell    -> ZERO mail (task tracking, not a decision)', w);
check('E nobody is emailed at all', w.mails.length === 0,
  `${w.mails.length} mails: ${w.mails.map((m) => m.subject).join(' | ')}`);

// F: the decision, with an Owner who is on the Volunteers tab
w = editCell(makeWorld({
  ticked: 'Dau an',
  preset: {
    [H['Status']]: 'TODO',
    [H['Decision']]: 'Approved',
    [H['Owner']]: 'Mai',
    [H['Decision note']]: 'Yes please - Tue or Thu works.'
  }
}), H['Decision']);
show('F. Decision=Approved   -> 1 donor mail, Reply sent, Status IN PROGRESS', w);
check('F one donor mail', donorMails(w).length === 1);
check('F Reply sent stamped', isDate(w.grid[1][H['Reply sent']]));
check('F status moved to IN PROGRESS', w.grid[1][H['Status']] === 'IN PROGRESS');
check('F the note reached the donor verbatim',
  /Tue or Thu works\./.test(donorMails(w)[0].htmlBody));
check('F the decision mail carries no handover block',
  !TERMS_MARK.test(donorMails(w)[0].htmlBody));
check('F the owner is named', /Mai from Vietnam Hearts will contact you/
  .test(donorMails(w)[0].htmlBody));
check('F the owner is named in Vietnamese too',
  /Mai từ Vietnam Hearts/.test(donorMails(w)[0].htmlBody));
check('F replyTo is the owner', donorMails(w)[0].replyTo === 'mai@example.com');
check('F no gap was reported', teamMails(w).length === 0,
  teamMails(w).map((m) => m.subject).join(' | '));

// G: touching the same Decision cell again
w = editCell(w, H['Decision']);
show('G. same Decision again -> still 1 donor mail (idempotent)', w);
check('G still exactly one donor mail', donorMails(w).length === 1);

// H: an Owner who is not on the Volunteers tab
w = editCell(makeWorld({
  preset: {
    [H['Decision']]: 'Approved',
    [H['Owner']]: 'Khanh',
    [H['Decision note']]: 'Yes please.'
  }
}), H['Decision']);
show('H. Owner not on the Volunteers tab -> team address, and a report', w);
check('H donor still emailed', donorMails(w).length === 1);
check('H replyTo falls back to the team', donorMails(w)[0].replyTo === TEAM);
check('H nobody is named in the body',
  /A volunteer from Vietnam Hearts will contact you/.test(donorMails(w)[0].htmlBody));
check('H the gap is reported to the team',
  teamMails(w).some((m) => /ownerContact_/.test(m.subject)));

// I: approved with no Owner at all
w = editCell(makeWorld({
  preset: { [H['Decision']]: 'Approved', [H['Decision note']]: 'Yes please.' }
}), H['Decision']);
show('I. Approved, Owner blank -> team address, and a report', w);
check('I replyTo falls back to the team', donorMails(w)[0].replyTo === TEAM);
check('I the unassigned row is reported',
  teamMails(w).some((m) => /ownerContact_/.test(m.subject)));

// J: Declined keeps the Reply-To but names nobody, and an unassigned decline
//    is not worth an error mail
w = editCell(makeWorld({
  preset: {
    [H['Decision']]: 'Declined',
    [H['Owner']]: 'Mai',
    [H['Decision note']]: 'No storage space this term.'
  }
}), H['Decision']);
show('J. Decision=Declined   -> replyTo owner, nobody named, Status DONE', w);
check('J status moved to DONE', w.grid[1][H['Status']] === 'DONE');
check('J replyTo is the owner', donorMails(w)[0].replyTo === 'mai@example.com');
check('J the decline does not name the owner',
  !/Mai/.test(donorMails(w)[0].htmlBody));
check('J the decline reason reached the donor',
  /No storage space this term\./.test(donorMails(w)[0].htmlBody));

w = editCell(makeWorld({ preset: { [H['Decision']]: 'Declined' } }), H['Decision']);
check('J unassigned decline reports nothing', teamMails(w).length === 0,
  teamMails(w).map((m) => m.subject).join(' | '));

// K: no Volunteers tab at all - the reply must still go out
w = editCell(makeWorld({
  noVolunteersTab: true,
  preset: { [H['Decision']]: 'Approved', [H['Owner']]: 'Mai' }
}), H['Decision']);
show('K. no Volunteers tab   -> team address, and a report', w);
check('K donor still emailed', donorMails(w).length === 1);
check('K replyTo falls back to the team', donorMails(w)[0].replyTo === TEAM);
check('K the missing tab is reported',
  teamMails(w).some((m) => /ownerContact_/.test(m.subject)));

// L: an Owner on the tab with no email address
w = editCell(makeWorld({
  volunteers: [['Mai', '', 'no address yet']],
  preset: { [H['Decision']]: 'Approved', [H['Owner']]: 'Mai' }
}), H['Decision']);
show('L. Owner row has no email -> team address, and a report', w);
check('L replyTo falls back to the team', donorMails(w)[0].replyTo === TEAM);
check('L the empty address is reported',
  teamMails(w).some((m) => /ownerContact_/.test(m.subject)));

// M: Approved drag-filled down Decision for three rows
w = editRange(makeWorld({
  rows: 3,
  preset: { [H['Decision']]: 'Approved', [H['Owner']]: 'Mai' }
}), 2, H['Decision'], 3, 1);
show('M. Decision drag-filled over 3 rows -> 3 donor mails, all stamped', w);
check('M every row is emailed', donorMails(w).length === 3, String(donorMails(w).length));
check('M every row is stamped',
  [1, 2, 3].every((i) => isDate(w.grid[i][H['Reply sent']])));

// N: a paste that starts left of Decision and covers it
w = editRange(makeWorld({
  rows: 2,
  preset: { [H['Decision']]: 'Declined', [H['Owner']]: 'Mai' }
}), 2, H['Status'], 2, H['Owner'] + 1);
show('N. paste covering Status:Owner -> both rows emailed', w);
check('N every row is emailed', donorMails(w).length === 2, String(donorMails(w).length));
check('N every row moved to DONE',
  [1, 2].every((i) => w.grid[i][H['Status']] === 'DONE'));

// O: the quota guard trips on a decision
w = editCell(makeWorld({
  quota: 2,
  preset: { [H['Decision']]: 'Approved', [H['Owner']]: 'Mai' }
}), H['Decision']);
show('O. quota nearly exhausted -> no mail, nothing stamped, team told', w);
check('O no donor mail', donorMails(w).length === 0);
check('O Reply sent stays empty', w.grid[1][H['Reply sent']] === '');
check('O Status is not moved', w.grid[1][H['Status']] === '');
check('O the unsent reply is reported',
  teamMails(w).some((m) => /onIntakeEdit/.test(m.subject) &&
    /was not sent/.test(m.htmlBody)));

w = submit(makeWorld({ quota: 2, ticked: 'Dau an' }));
check('O an unsent acknowledgement is reported',
  donorMails(w).length === 0 &&
  teamMails(w).some((m) => /onIntakeFormSubmit/.test(m.subject) &&
    /was not sent/.test(m.htmlBody)));

/* -------------------------------------------------- the global invariants */

check('the address matcher flags every address-shaped sample',
  MUST_MATCH.every((t) => ADDRESS.test(t)),
  MUST_MATCH.filter((t) => !ADDRESS.test(t)).join(' | '));
check('the address matcher passes the public area and ordinary numbers',
  MUST_NOT_MATCH.every((t) => !ADDRESS.test(t)),
  MUST_NOT_MATCH.filter((t) => ADDRESS.test(t)).join(' | '));

console.log('\n--- every mail produced above');
const offenders = allMails.filter((m) => PHONE.test(String(m.htmlBody)) ||
  PHONE.test(String(m.subject)));
check(`no phone number in any of the ${allMails.length} mails`,
  offenders.length === 0,
  offenders.map((m) => m.subject).join(' | '));
const addressed = allMails.filter((m) => ADDRESS.test(String(m.htmlBody)) ||
  ADDRESS.test(String(m.subject)));
check(`no street address in any of the ${allMails.length} mails`,
  addressed.length === 0,
  addressed.map((m) => m.subject).join(' | '));
check('every recipient is an address the script was given',
  allMails.every((m) => m.to === DONOR || m.to === TEAM),
  allMails.map((m) => m.to).filter((t) => t !== DONOR && t !== TEAM).join(', '));

/* ------------------------------------------------------------------ result */

console.log('');
if (failures.length) {
  console.log(`${failures.length} check(s) FAILED:`);
  failures.forEach((f) => console.log(`  - ${f}`));
  process.exit(1);
}
console.log('all checks passed');
