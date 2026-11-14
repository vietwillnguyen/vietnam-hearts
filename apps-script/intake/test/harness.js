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
const FORBIDDEN = require('./forbidden-shapes');

const CODE = process.argv[2] || path.join(__dirname, '..', 'Code.js');

/* The form-response grid. Order matters only in that the script must not
   depend on it: every column is found by header text. */
const Q = {
  name: 'Your name / Tên của bạn',
  type: 'What would you like to offer? / Bạn muốn đề xuất điều gì?',
  standing: 'We always need these - are you giving any of them? / ' +
    'Chúng tôi luôn cần những món này - bạn có tặng món nào không?',
  other: 'Other items not on the list above / Các món khác (không có trong danh sách trên)',
  /* Asked at the end of all four branches, so Forms makes four identically
     headed columns. No HEADER needle may match them. */
  closing: 'What do you need from us, or anything else we should know? / ' +
    'Bạn cần gì từ Vietnam Hearts, hoặc có điều gì khác muốn cho chúng tôi biết?'
};
const HEADERS = [
  'Status', 'Decision', 'Priority', 'Next action by', 'Owner',
  'Decision note', 'Reply sent',
  'Timestamp', 'Email Address', Q.name, Q.type, Q.standing, Q.other,
  Q.closing, Q.closing, Q.closing, Q.closing
];
const H = {};
HEADERS.forEach((h, i) => (H[h] = i));

const DONOR = 'donor@example.com';
const TEAM = 'vietnam.hearts.volunteering@gmail.com';

/* The handover wording a volunteer would type into 'Supplies handover'!B2. It
   names the public area, which is allowed, and that string does double duty:
   it proves which mails include the block: the acknowledgement of ticked
   items, and the Approved reply to any supplies offer. */
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
  row[H[Q.name]] = 'Linh';
  row[H[Q.type]] =
    opts.type === undefined ? 'Donate supplies or goods / Tặng đồ dùng, vật phẩm' : opts.type;
  row[H[Q.standing]] = opts.ticked || '';
  row[H[Q.other]] = opts.other || '';
  Object.assign(row, opts.preset || {});

  const header = opts.headers || HEADERS;
  const grid = [header.slice()];
  for (let i = 0; i < (opts.rows || 1); i++) {
    grid.push(Object.assign(row.slice(), (opts.rowPresets || [])[i] || {}));
  }
  const mails = [];
  const logs = [];

  const responses = {
    getName: () => 'Form Responses 1',
    getLastColumn: () => header.length,
    getLastRow: () => grid.length,
    getSheetId: () => 1234,
    getRange: (r, c, nr, nc) => ({
      /* Real getValues returns numRows rows. The fake used to return one
         whatever it was asked for, which would have let a whole-sheet scan
         pass while only ever seeing its first row. */
      getValues: () => {
        const out = [];
        for (let i = 0; i < (nr || 1); i++) {
          const line = grid[r - 1 + i];
          out.push(line ? line.slice(c - 1, c - 1 + (nc || 1)) : []);
        }
        return out;
      },
      setValue: (v) => { grid[r - 1][c - 1] = v; },
      getRow: () => r,
      getColumn: () => c,
      getLastRow: () => r + (nr || 1) - 1,
      getLastColumn: () => c + (nc || 1) - 1,
      getSheet: () => responses
    })
  };

  const termsTabName = opts.termsTabName || 'Supplies handover';
  const termsTab = {
    getName: () => termsTabName,
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

  /* One document lock, as LockService hands every run of a bound script.
     `opts.lockHeld` makes it busy for the whole edit; `opts.onTryLock` runs
     once, just before the first tryLock, to interleave another edit there. */
  const lock = { held: false, waits: [] };
  let interleave = opts.onTryLock;
  const documentLock = {
    tryLock: (ms) => {
      lock.waits.push(ms);
      if (interleave) { const run = interleave; interleave = null; run(); }
      if (lock.held || opts.lockHeld) return false;
      lock.held = true;
      return true;
    },
    releaseLock: () => { lock.held = false; }
  };

  const sandbox = {
    LockService: { getDocumentLock: () => documentLock },
    SpreadsheetApp: {
      flush: () => {},
      getActive: () => ({
        getUrl: () => 'https://docs.google.com/spreadsheets/d/FAKE/edit',
        getSheetByName: (n) => {
          if (n === 'Form Responses 1') return responses;
          if (n === termsTabName) return opts.noTermsTab ? null : termsTab;
          if (n === 'Volunteers') return opts.noVolunteersTab ? null : volunteersTab;
          return null;
        }
      })
    },
    MailApp: {
      getRemainingDailyQuota: () => (opts.quota === undefined ? 90 : opts.quota),
      sendEmail: (o) => {
        if (o.replyTo && o.replyTo === opts.rejectReplyTo) {
          throw new Error('Invalid argument: replyTo');
        }
        mails.push(o);
      }
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
  return { sandbox, grid, mails, logs, responses, lock };
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
w = submit(makeWorld({ type: 'Propose an event / Đề xuất sự kiện' }));
check('C2 another branch with nothing listed gets no supplies hint',
  teamMails(w).every((m) => !/Supplies request with nothing listed/.test(m.htmlBody)));

/* C3: photos. The form used to ask for them with a File upload question, and
   one such question makes Forms require a Google sign-in for the whole form,
   which is the login wall the second live build hit. So a supplies submitter
   is asked for photos in the acknowledgement instead, as a reply to that
   email, English half first - and no other branch is. */
w = submit(makeWorld({ ticked: 'Dau an' }));
const ackC3 = donorMails(w)[0].htmlBody;
check('C3 the supplies acknowledgement asks for photos',
  /photo/i.test(ackC3), ackC3.slice(0, 200));
check('C3 it asks for a reply to this email, and names no chat app',
  /reply to this email/.test((ackC3.match(/<p>[^<]*photo[^<]*<\/p>/i) || [''])[0]) &&
  !/(Zalo|WhatsApp)/.test((ackC3.match(/<p>[^<]*photo[^<]*<\/p>/i) || [''])[0]),
  (ackC3.match(/<p>[^<]*photo[^<]*<\/p>/i) || [''])[0]);
/* A volunteer reaches out to the submitter, so the ask points at a reply
   and never at a number. */
check('C3 the ask promises no number anywhere in the mail',
  !/number|số điện thoại|số nằm/i.test(
    (ackC3.match(/<p>[^<]*(photo|ảnh)[^<]*<\/p>/gi) || []).join(' ')),
  (ackC3.match(/<p>[^<]*(photo|ảnh)[^<]*<\/p>/gi) || []).join(' '));
check('C3 the English ask comes before the Vietnamese one',
  ackC3.search(/[Pp]hoto/) < ackC3.search(/ảnh/i) &&
  ackC3.search(/ảnh/i) !== -1,
  `${ackC3.search(/[Pp]hoto/)} vs ${ackC3.search(/ảnh/i)}`);
/* The handover block rides in only when something was ticked, so the ask has
   to be in the body of the mail or a supplies offer of unlisted goods would
   never see it. */
w = submit(makeWorld({ other: '40 thung sach cu' }));
check('C3 a supplies offer with nothing ticked is asked for photos too',
  !TERMS_MARK.test(donorMails(w)[0].htmlBody) &&
  /photo/i.test(donorMails(w)[0].htmlBody),
  donorMails(w)[0].htmlBody.slice(0, 200));
w = submit(makeWorld({ type: 'Propose an event / Đề xuất sự kiện', ticked: '' }));
check('C3 a non-supplies branch is not asked for photos of goods',
  !/photo/i.test(donorMails(w)[0].htmlBody),
  (donorMails(w)[0].htmlBody.match(/[^<>]*photo[^<>]*/i) || [''])[0]);

// D: the handover wording is missing from the sheet - fail soft
w = submit(makeWorld({ ticked: 'Dau an', terms: '' }));
show('D. handover cell empty -> TODO anyway, error mail, nothing approved', w);
check('D submission still accepted', w.grid[1][H['Status']] === 'TODO');
check('D Decision still empty', w.grid[1][H['Decision']] === '');
check('D donor still acknowledged', donorMails(w).length === 1);
check('D team told which function failed',
  teamMails(w).some((m) => /termsBlockOrEmpty_/.test(m.subject)));

/* A push goes live before buildIntake renames the tab, so the wording is
   still read from the tab under its old name, with no error mail. */
w = submit(makeWorld({ ticked: 'Dau an', termsTabName: 'Acknowledgement Email Response' }));
check('D a tab still on its old name still feeds the acknowledgement',
  TERMS_MARK.test(donorMails(w)[0].htmlBody), donorMails(w)[0].htmlBody.slice(0, 200));
check('D and nobody is told it is missing',
  !teamMails(w).some((m) => /termsBlockOrEmpty_/.test(m.subject)),
  teamMails(w).map((m) => m.subject).join(' | '));

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
check('F the approved supplies mail carries the handover block exactly once',
  (donorMails(w)[0].htmlBody.match(/Thạnh Mỹ Tây/g) || []).length === 1);
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

// J2: an approved supplies offer carries the handover block whatever was
//     ticked; a decline and an approved non-supplies offer do not
w = editCell(makeWorld({ other: '40 thung sach cu',
  preset: { [H['Decision']]: 'Approved', [H['Owner']]: 'Mai' } }), H['Decision']);
check('J2 an approved off-list supplies offer carries the handover block',
  TERMS_MARK.test(donorMails(w)[0].htmlBody), donorMails(w)[0].htmlBody.slice(0, 300));
check('J2 the block sits in the English half, above the divider',
  donorMails(w)[0].htmlBody.search(TERMS_MARK) < donorMails(w)[0].htmlBody.indexOf('<hr>'));
check('J2 and no error mail', teamMails(w).length === 0,
  teamMails(w).map((m) => m.subject).join(' | '));
w = editCell(makeWorld({ ticked: 'Dau an',
  preset: { [H['Decision']]: 'Declined', [H['Owner']]: 'Mai' } }), H['Decision']);
check('J2 a declined supplies offer gets no handover block',
  !TERMS_MARK.test(donorMails(w)[0].htmlBody));
w = editCell(makeWorld({ type: 'Propose an event / Đề xuất sự kiện', ticked: '',
  preset: { [H['Decision']]: 'Approved', [H['Owner']]: 'Mai' } }), H['Decision']);
check('J2 an approved event gets no handover block',
  !TERMS_MARK.test(donorMails(w)[0].htmlBody));

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

// P: a drag-fill over three rows where the middle row's send throws
w = editRange(makeWorld({
  rows: 3,
  volunteers: [['Mai', 'mai@example.com', ''], ['Bao', 'bao@broken', '']],
  rejectReplyTo: 'bao@broken',
  preset: { [H['Decision']]: 'Approved', [H['Owner']]: 'Mai' },
  rowPresets: [{}, { [H['Owner']]: 'Bao' }, {}]
}), 2, H['Decision'], 3, 1);
show('P. drag-fill, row 3 throws -> rows 2 and 4 still sent, row 3 reported', w);
check('P the rows either side of the failure are emailed', donorMails(w).length === 2,
  String(donorMails(w).length));
check('P the rows either side are stamped',
  isDate(w.grid[1][H['Reply sent']]) && isDate(w.grid[3][H['Reply sent']]));
check('P the failed row is not stamped', w.grid[2][H['Reply sent']] === '');
check('P the failure names its row',
  teamMails(w).some((m) => m.subject === '[Requests sheet] onIntakeEdit row 3 failed'),
  teamMails(w).map((m) => m.subject).join(' | '));

// Q: two overlapping edits of one row. The second has read an empty Reply
// sent and is about to take the lock when the first runs to completion.
w = makeWorld({
  preset: { [H['Decision']]: 'Approved', [H['Owner']]: 'Mai' },
  onTryLock: () => w.sandbox.onIntakeEdit({
    range: w.responses.getRange(2, H['Decision'] + 1, 1, 1)
  })
});
editCell(w, H['Decision']);
show('Q. two interleaved edits of one row -> exactly one donor mail', w);
check('Q exactly one donor mail', donorMails(w).length === 1, String(donorMails(w).length));
check('Q the row is stamped', isDate(w.grid[1][H['Reply sent']]));
check('Q both edits asked for the lock with a bounded wait',
  w.lock.waits.length === 2 && w.lock.waits.every((ms) => ms > 0 && ms <= 30000),
  JSON.stringify(w.lock.waits));
check('Q the lock is released afterwards', w.lock.held === false);
check('Q nothing is reported', teamMails(w).length === 0,
  teamMails(w).map((m) => m.subject).join(' | '));

// R: the lock cannot be had within the wait
w = editCell(makeWorld({
  lockHeld: true,
  preset: { [H['Decision']]: 'Approved', [H['Owner']]: 'Mai' }
}), H['Decision']);
show('R. lock busy for the whole wait -> nothing sent, nothing stamped, team told', w);
check('R no donor mail', donorMails(w).length === 0);
check('R Reply sent stays empty', w.grid[1][H['Reply sent']] === '');
check('R Status is not moved', w.grid[1][H['Status']] === '');
check('R the skipped row is reported by number',
  teamMails(w).some((m) => /onIntakeEdit/.test(m.subject) && /Row 2/.test(m.htmlBody)),
  teamMails(w).map((m) => m.subject).join(' | '));

// S: a Status-only edit, or a row with no decision, never takes the lock
w = editCell(makeWorld({ lockHeld: true }), H['Decision']);
check('S a blank Decision neither waits on the lock nor reports',
  w.lock.waits.length === 0 && w.mails.length === 0);

/* ----------------------------------- T: the missed-row sweep -------------
 *
 * Live pass 3: adding an advanced service to appsscript.json added a scope,
 * and after `clasp push` the INSTALLED triggers stopped firing until the
 * owner re-authorized. A real submission landed in the sheet with a
 * Timestamp and nothing else - no Status, no acknowledgement, no team
 * notice - and nothing in the sheet said so. sweepUnstamped() is the repair:
 * it processes exactly those rows, once.
 */
console.log('\n--- T. sweepUnstamped(), the repair for rows that arrived while the triggers were dead');

function sweep(w) {
  w.sandbox.sweepUnstamped();
  return record(w);
}

// T1: one row that the dead trigger never touched
w = makeWorld({ ticked: 'Dau an' });
w.grid[1][H['Status']] = '';
w.grid[1][H['Priority']] = '';
w.grid[1][H['Next action by']] = '';
sweep(w);
check('T1 the missed row is stamped TODO', w.grid[1][H['Status']] === 'TODO',
  String(w.grid[1][H['Status']]));
check('T1 priority is set the same way the trigger would',
  w.grid[1][H['Priority']] === 'High', String(w.grid[1][H['Priority']]));
check('T1 Next action by is filled', isDate(w.grid[1][H['Next action by']]));
check('T1 the submitter is acknowledged after all', donorMails(w).length === 1,
  String(donorMails(w).length));
check('T1 the team is notified after all', teamMails(w).length === 1,
  teamMails(w).map((m) => m.subject).join(' | '));
check('T1 nothing is approved by a sweep', w.grid[1][H['Decision']] === '');

// T2: running it twice must not email anybody twice
const beforeT2 = w.mails.length;
w.sandbox.sweepUnstamped();
check('T2 a second sweep processes the row again zero times',
  w.mails.length === beforeT2, `${beforeT2} -> ${w.mails.length}`);

// T3: a row the trigger already handled is left alone
w = makeWorld({ ticked: 'Dau an', preset: { [H['Status']]: 'IN PROGRESS' } });
w.grid[1][H['Priority']] = 'Low';
sweep(w);
check('T3 a stamped row is not reprocessed', w.mails.length === 0,
  w.mails.map((m) => m.subject).join(' | '));
check('T3 its Status is untouched', w.grid[1][H['Status']] === 'IN PROGRESS');
check('T3 its Priority is untouched', w.grid[1][H['Priority']] === 'Low');

/* T4: a volunteer who clears Status on a handled row must NOT cause a second
   acknowledgement to a member of the public. Status alone cannot tell
   "never processed" from "processed, then edited", so the sweep requires the
   whole submit stamp - Status, Priority and Next action by - to be empty. */
w = makeWorld({ ticked: 'Dau an' });
w.grid[1][H['Status']] = '';
w.grid[1][H['Priority']] = 'High';
w.grid[1][H['Next action by']] = new Date('2026-11-21T00:00:00Z');
sweep(w);
check('T4 a cleared Status on an otherwise stamped row is not re-acknowledged',
  donorMails(w).length === 0, donorMails(w).map((m) => m.subject).join(' | '));
check('T4 and the sweep does not re-stamp it either',
  w.grid[1][H['Status']] === '', String(w.grid[1][H['Status']]));

// T5: a blank trailing row has no Timestamp, so it is not a missed submission
w = makeWorld({ rows: 2, rowPresets: [null, Object.fromEntries(
  HEADERS.map((h, i) => [i, '']))] });
w.grid[1][H['Status']] = '';
w.grid[1][H['Priority']] = '';
w.grid[1][H['Next action by']] = '';
sweep(w);
check('T5 only the row with a Timestamp is treated as a submission',
  donorMails(w).length === 1, String(donorMails(w).length));
check('T5 the empty row is left empty', w.grid[2][H['Status']] === '');

// T6: several missed rows are all repaired, each once
w = makeWorld({ rows: 3 });
[1, 2, 3].forEach((r) => {
  w.grid[r][H['Status']] = '';
  w.grid[r][H['Priority']] = '';
  w.grid[r][H['Next action by']] = '';
});
sweep(w);
check('T6 every missed row is stamped',
  [1, 2, 3].every((r) => w.grid[r][H['Status']] === 'TODO'),
  [1, 2, 3].map((r) => w.grid[r][H['Status']]).join(','));
check('T6 each missed row is acknowledged exactly once',
  donorMails(w).length === 3, String(donorMails(w).length));

// T7: the sweep reports what it repaired, so a silent no-op is distinguishable
w = makeWorld({ ticked: 'Dau an' });
w.grid[1][H['Status']] = '';
w.grid[1][H['Priority']] = '';
w.grid[1][H['Next action by']] = '';
check('T7 it returns the number of rows it repaired',
  w.sandbox.sweepUnstamped() === 1);
check('T7 and zero when there is nothing to repair',
  w.sandbox.sweepUnstamped() === 0);

/* U: the trigger and a sweep racing for one row. Both share
   processSubmissionRow_, which re-reads the row under the document lock, so
   whichever gets there second finds the stamps and sends nothing. */
function unstamped(w, r) {
  w.grid[r][H['Status']] = '';
  w.grid[r][H['Priority']] = '';
  w.grid[r][H['Next action by']] = '';
  return w;
}

// U1: the sweep reaches the row first; the trigger fires right after
w = unstamped(makeWorld({ ticked: 'Dau an',
  onTryLock: () => w.sandbox.sweepUnstamped() }), 1);
submit(w);
check('U1 a trigger arriving after the sweep acknowledges nobody twice',
  donorMails(w).length === 1, String(donorMails(w).length));
check('U1 and tells the team once', teamMails(w).length === 1,
  teamMails(w).map((m) => m.subject).join(' | '));

// U2: the trigger reaches the row first; the sweep read it before that
w = unstamped(makeWorld({ ticked: 'Dau an',
  onTryLock: () => w.sandbox.onIntakeFormSubmit({ range: w.responses.getRange(2, 1, 1, 1) }) }), 1);
let repairedU2 = w.sandbox.sweepUnstamped();
record(w);
check('U2 a sweep arriving after the trigger acknowledges nobody twice',
  donorMails(w).length === 1, String(donorMails(w).length));
check('U2 and does not count the row as repaired', repairedU2 === 0, String(repairedU2));

// U3: the lock never frees: nothing is sent, the row stays unstamped, and the
// team is told which row and what to run
w = submit(makeWorld({ lockHeld: true }));
check('U3 a submission that cannot get the lock emails no submitter',
  donorMails(w).length === 0, String(donorMails(w).length));
check('U3 and leaves the row for the sweep', w.grid[1][H['Status']] === '');
check('U3 the team is told the row and the repair',
  teamMails(w).length === 1 && /Row 2/.test(teamMails(w)[0].htmlBody) &&
    /sweepUnstamped/.test(teamMails(w)[0].htmlBody),
  teamMails(w).map((m) => m.subject).join(' | '));
w = unstamped(makeWorld({ lockHeld: true }), 1);
check('U3 a sweep that cannot get the lock repairs nothing',
  w.sandbox.sweepUnstamped() === 0 && donorMails(record(w)).length === 0);

// U4: a half-written row is not a handled one; the trigger still finishes it
w = submit(makeWorld({ ticked: 'Dau an', preset: { [H['Status']]: 'TODO' } }));
check('U4 a row with only Status set is still acknowledged',
  donorMails(w).length === 1, String(donorMails(w).length));
check('U4 and its missing stamps are filled',
  w.grid[1][H['Priority']] === 'High' && isDate(w.grid[1][H['Next action by']]));

// U5: a row with all three stamps already set is the sweep's work, finished
w = submit(makeWorld({ preset: { [H['Status']]: 'TODO', [H['Priority']]: 'Normal',
  [H['Next action by']]: new Date('2026-11-21T00:00:00Z') } }));
check('U5 a fully stamped row is not acknowledged again by the trigger',
  w.mails.length === 0, w.mails.map((m) => m.subject).join(' | '));

/* V: a Vietnamese-locale spreadsheet. Forms titles its own columns in the
   sheet's language, so setUp() must not depend on the Timestamp title and
   the sweep must still find it. */
const VI_HEADERS = HEADERS.map((h) =>
  h === 'Timestamp' ? 'Dấu thời gian' : h === 'Email Address' ? 'Địa chỉ email' : h);
w = unstamped(makeWorld({ headers: VI_HEADERS, ticked: 'Dau an' }), 1);
check('V setUp() resolves every HEADER entry on a Vietnamese sheet', (function () {
  try { w.sandbox.setUp(); return true; } catch (err) { return 'threw: ' + err.message; }
})() === true);
check('V the sweep finds and repairs a missed row there',
  w.sandbox.sweepUnstamped() === 1 && w.grid[1][H['Status']] === 'TODO');
record(w);
check('V and its submitter is acknowledged once', donorMails(w).length === 1,
  String(donorMails(w).length));

// V2: a Timestamp title in no known spelling: report it and guess nothing
w = unstamped(makeWorld({ headers: HEADERS.map((h) =>
  h === 'Timestamp' ? 'Zeitstempel' : h) }), 1);
const repairedV2 = w.sandbox.sweepUnstamped();
record(w);
check('V2 an unknown Timestamp title repairs nothing',
  repairedV2 === 0 && donorMails(w).length === 0 && w.grid[1][H['Status']] === '');
check('V2 and the team is told why',
  teamMails(w).length === 1 && /Dấu thời gian/.test(teamMails(w)[0].htmlBody),
  teamMails(w).map((m) => m.htmlBody).join(' | '));

/* ------------------------- the needles against the real column titles ---- */

/* The form's questions now lead in English, so every HEADER needle is matched
   here against the exact bilingual titles the builder writes. A flip that
   renamed a question out from under a needle resolves to no column, or to
   several, and `col_` throws - which is this block. */
console.log('\n--- every HEADER needle against the real bilingual column titles');
w = makeWorld();
const HEADER = w.sandbox.HEADER;
Object.keys(HEADER).forEach((k) => {
  let idx = -1;
  let why = '';
  try {
    idx = w.sandbox.col_(HEADERS, HEADER[k]);
  } catch (err) {
    why = err.message;
  }
  check(`HEADER.${k} ("${HEADER[k]}") resolves to exactly one column`,
    idx !== -1, why);
});
check('no needle matches the four identically headed closing columns',
  Object.keys(HEADER).every((k) =>
    Q.closing.toLowerCase().indexOf(String(HEADER[k]).toLowerCase()) === -1),
  Object.keys(HEADER).filter((k) =>
    Q.closing.toLowerCase().indexOf(String(HEADER[k]).toLowerCase()) !== -1).join(', '));
check('setUp() resolves the whole header row without throwing', (function () {
  try {
    w.sandbox.setUp();
    return true;
  } catch (err) {
    return 'threw: ' + err.message;
  }
})() === true);

/* ------------------------------------------ English first in outbound mail */

console.log('\n--- English first in every automated mail');
const donorAll = allMails.filter((m) => m.to === DONOR);
const greetingOrder = donorAll.filter((m) => {
  const b = String(m.htmlBody);
  return b.indexOf('Hello') === -1 || b.indexOf('Xin chào') === -1 ||
    b.indexOf('Hello') > b.indexOf('Xin chào');
});
check(`all ${donorAll.length} submitter mails greet in English first`,
  greetingOrder.length === 0, greetingOrder.map((m) => m.subject).join(' | '));
const subjectOrder = donorAll.filter((m) =>
  !/^Vietnam Hearts(?::| da)? (?:we have your offer|we would love|about your request)/
    .test(m.subject));
check('every submitter subject leads in English', subjectOrder.length === 0,
  subjectOrder.map((m) => m.subject).join(' | '));
/* The footer is checked on its own: the handover block sits above it in the
   acknowledgement and is bilingual too, so a search over the whole body would
   find the block's Vietnamese line and prove nothing about the footer. */
const footer = makeWorld().sandbox.footerHtml_();
check('the footer leads in English',
  footer.indexOf('A volunteer will contact you') !== -1 &&
  footer.indexOf('A volunteer will contact you') < footer.indexOf('Một tình nguyện viên'),
  footer);

/* -------------------------------------------------- the global invariants */

Object.keys(FORBIDDEN.SAMPLES).forEach((name) => {
  const re = FORBIDDEN[name];
  const s = FORBIDDEN.SAMPLES[name];
  check(`the ${name} matcher flags every sample of that shape`,
    s.match.every((t) => re.test(t)), s.match.filter((t) => !re.test(t)).join(' | '));
  check(`the ${name} matcher passes the allowed text`,
    s.pass.every((t) => !re.test(t)), s.pass.filter((t) => re.test(t)).join(' | '));
});

console.log('\n--- every mail produced above');
const offenders = allMails.filter((m) => PHONE.test(String(m.htmlBody)) ||
  PHONE.test(String(m.subject)));
check(`no phone number in any of the ${allMails.length} mails`,
  offenders.length === 0,
  offenders.map((m) => m.subject).join(' | '));
[['ADDRESS', 'street address'], ['HOURS', 'opening hours'],
  ['DATE_PROMISE', 'reply-date promise']].forEach(([name, label]) => {
  const re = FORBIDDEN[name];
  const hits = allMails.filter((m) => re.test(String(m.htmlBody)) ||
    re.test(String(m.subject)));
  check(`no ${label} in any of the ${allMails.length} mails`, hits.length === 0,
    hits.map((m) => m.subject + ': ' +
      (String(m.subject + ' ' + m.htmlBody).match(re) || [''])[0]).join(' | '));
});
/* Every mail to a member of the public points them at a reply and nowhere
   else: a volunteer reaches out to them, so no acknowledgement or decision
   names a group chat, a chat app or a team number. */
const approvedSubject = w.sandbox.decisionSubject_('Approved');
const declinedSubject = w.sandbox.decisionSubject_('Declined');
const decisions = donorAll.filter((m) =>
  m.subject === approvedSubject || m.subject === declinedSubject);
const acks = donorAll.filter((m) =>
  m.subject !== approvedSubject && m.subject !== declinedSubject);
check('the cases produced both Approved and Declined mails',
  decisions.some((m) => m.subject === approvedSubject) &&
  decisions.some((m) => m.subject === declinedSubject));
check('the cases produced acknowledgements', acks.length > 0);
const chatLeaks = donorAll.filter((m) =>
  /zalo\.me|chat\.whatsapp|wa\.me|Zalo|WhatsApp/i.test(String(m.htmlBody)));
check(`no Zalo, WhatsApp or group link in any of the ${donorAll.length} mails to the public`,
  chatLeaks.length === 0, chatLeaks.map((m) => m.subject).join(' | '));
check(`every decision mail asks for a reply for next steps`,
  decisions.every((m) => /reply to this email for next steps/i.test(String(m.htmlBody))));
check(`every one of the ${acks.length} acknowledgements says a volunteer will contact them`,
  acks.every((m) => /A volunteer will contact you/.test(String(m.htmlBody))));
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
