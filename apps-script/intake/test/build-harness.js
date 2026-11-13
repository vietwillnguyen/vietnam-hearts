/*
 * Stub harness for buildIntake().
 *
 * Runs the real builder against a fake FormApp / SpreadsheetApp / MailApp in a
 * `vm` context, then asserts the things that are expensive to discover in a
 * live Google account: that the four branches navigate to the final section,
 * that the always-needed list is the form's checkbox choices and nothing else,
 * that email collection is responder input rather than verified (verified
 * forces a Google sign-in), that the published tab can never hold personal
 * data, and that a second run repairs rather than duplicates.
 *
 *   node test/build-harness.js Code.js Build.js
 *
 * No Google account, no network, no credential. The fake is a fake: it proves
 * the builder's own logic, not that Google's API behaves as documented.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const CODE = process.argv[2] || path.join(__dirname, '..', 'Code.js');
const BUILD = process.argv[3] || path.join(__dirname, '..', 'Build.js');

/* ------------------------------------------------------------- fake sheets */

function makeSpreadsheet(world) {
  const sheets = [];

  function makeSheet(name) {
    const cells = {};          // 'r,c' -> value
    const meta = {
      name,
      validations: [],         // { a1, rule }
      formats: [],             // conditional format rules
      protections: [],
      charts: [],
      frozenRows: 0,
      frozenColumns: 0,
      insertedColumns: 0
    };

    function key(r, c) { return r + ',' + c; }

    function bounds() {
      let lastRow = 0;
      let lastCol = 0;
      Object.keys(cells).forEach((k) => {
        const [r, c] = k.split(',').map(Number);
        if (cells[k] === '' || cells[k] === undefined) return;
        if (r > lastRow) lastRow = r;
        if (c > lastCol) lastCol = c;
      });
      return { lastRow, lastCol };
    }

    /* 'A1', 'A1:C30' -> numeric bounds. Only the forms the builder uses. */
    function parseA1(a1) {
      const m = /^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$/.exec(a1);
      if (!m) throw new Error('fake getRange cannot parse ' + a1);
      const colNum = (s) => s.split('').reduce((n, ch) => n * 26 + (ch.charCodeAt(0) - 64), 0);
      const c1 = colNum(m[1]);
      const r1 = Number(m[2]);
      const c2 = m[3] ? colNum(m[3]) : c1;
      const r2 = m[4] ? Number(m[4]) : r1;
      return { row: r1, col: c1, numRows: r2 - r1 + 1, numCols: c2 - c1 + 1, a1 };
    }

    function makeRange(spec) {
      const { row, col, numRows, numCols } = spec;
      const a1 = spec.a1 || `r${row}c${col}+${numRows}x${numCols}`;
      const self = {
        _sheet: () => sheet,
        _spec: spec,
        getRow: () => row,
        getColumn: () => col,
        getNumRows: () => numRows,
        getNumColumns: () => numCols,
        getA1Notation: () => a1,
        getSheet: () => sheet,
        getValue: () => cells[key(row, col)] === undefined ? '' : cells[key(row, col)],
        getValues: () => {
          const out = [];
          for (let r = row; r < row + numRows; r++) {
            const line = [];
            for (let c = col; c < col + numCols; c++) {
              const v = cells[key(r, c)];
              line.push(v === undefined ? '' : v);
            }
            out.push(line);
          }
          return out;
        },
        setValue: (v) => { cells[key(row, col)] = v; return self; },
        setValues: (vals) => {
          vals.forEach((line, ri) => line.forEach((v, ci) => {
            cells[key(row + ri, col + ci)] = v;
          }));
          return self;
        },
        setFormula: (f) => { cells[key(row, col)] = f; return self; },
        setFormulaR1C1: (f) => {
          for (let r = row; r < row + numRows; r++) {
            for (let c = col; c < col + numCols; c++) cells[key(r, c)] = f;
          }
          return self;
        },
        setDataValidation: (rule) => {
          meta.validations.push({ a1, row, col, rule });
          return self;
        },
        setNumberFormat: () => self,
        setWrap: () => self,
        setBackground: () => self,
        setFontWeight: () => self,
        setBold: () => self,
        protect: () => {
          const p = {
            a1,
            warningOnly: false,
            description: '',
            setDescription(d) { p.description = d; return p; },
            setWarningOnly(v) { p.warningOnly = v; return p; },
            remove() { meta.protections = meta.protections.filter((x) => x !== p); }
          };
          meta.protections.push(p);
          return p;
        }
      };
      return self;
    }

    const sheet = {
      _meta: meta,
      _cells: cells,
      getName: () => meta.name,
      setName: (n) => { meta.name = n; return sheet; },
      getSheetId: () => sheets.indexOf(sheet) + 1000,
      getLastRow: () => bounds().lastRow,
      getLastColumn: () => bounds().lastCol,
      getRange: function (a, b, c, d) {
        if (typeof a === 'string') return makeRange(parseA1(a));
        return makeRange({ row: a, col: b, numRows: c || 1, numCols: d || 1 });
      },
      insertColumnsBefore: (before, howMany) => {
        meta.insertedColumns += howMany;
        const moved = {};
        Object.keys(cells).forEach((k) => {
          const [r, c] = k.split(',').map(Number);
          const nc = c >= before ? c + howMany : c;
          moved[r + ',' + nc] = cells[k];
          delete cells[k];
        });
        Object.assign(cells, moved);
        return sheet;
      },
      setFrozenRows: (n) => { meta.frozenRows = n; return sheet; },
      setFrozenColumns: (n) => { meta.frozenColumns = n; return sheet; },
      getProtections: () => meta.protections.slice(),
      setConditionalFormatRules: (rules) => { meta.formats = rules.slice(); return sheet; },
      autoResizeColumns: () => sheet,
      setColumnWidth: () => sheet,
      getCharts: () => meta.charts.slice(),
      insertChart: (chart) => { meta.charts.push(chart); return sheet; },
      newChart: () => {
        const spec = { type: null, ranges: [], options: {} };
        const b = {
          setChartType: (t) => { spec.type = t; return b; },
          addRange: (r) => { spec.ranges.push(r.getA1Notation()); return b; },
          setOption: (k, v) => { spec.options[k] = v; return b; },
          setPosition: () => b,
          build: () => spec
        };
        return b;
      }
    };
    return sheet;
  }

  const ss = {
    getId: () => 'FAKE_SPREADSHEET_ID',
    getUrl: () => 'https://docs.google.com/spreadsheets/d/FAKE_SPREADSHEET_ID/edit',
    getSheets: () => sheets.slice(),
    getSheetByName: (n) => sheets.filter((sh) => sh.getName() === n)[0] || null,
    insertSheet: (n) => {
      if (sheets.some((sh) => sh.getName() === n)) {
        throw new Error('fake: a sheet named ' + n + ' already exists');
      }
      const sh = makeSheet(n);
      sheets.push(sh);
      world.insertedSheets.push(n);
      return sh;
    },
    _addResponseSheet: (n, headers) => {
      const sh = makeSheet(n);
      sheets.push(sh);
      sh.getRange(1, 1, 1, headers.length).setValues([headers]);
      return sh;
    }
  };

  // The blank spreadsheet a volunteer makes before running the builder.
  sheets.push(makeSheet('Sheet1'));
  return ss;
}

/* -------------------------------------------------------------- fake forms */

function makeFormApp(world, ss) {
  const ItemType = {
    MULTIPLE_CHOICE: 'MULTIPLE_CHOICE', TEXT: 'TEXT', PARAGRAPH_TEXT: 'PARAGRAPH_TEXT',
    CHECKBOX: 'CHECKBOX', DATE: 'DATE', PAGE_BREAK: 'PAGE_BREAK',
    FILE_UPLOAD: 'FILE_UPLOAD'
  };

  function makeForm(title) {
    const items = [];
    const form = {
      _items: items,
      _settings: {},
      _destination: null,
      title,
      getId: () => 'FAKE_FORM_ID',
      getTitle: () => form.title,
      setTitle: (t) => { form.title = t; return form; },
      getPublishedUrl: () => 'https://docs.google.com/forms/d/e/FAKE/viewform',
      getEditUrl: () => 'https://docs.google.com/forms/d/FAKE_FORM_ID/edit',
      setDescription: (d) => { form._settings.description = d; return form; },
      setConfirmationMessage: (m) => { form._settings.confirmation = m; return form; },
      setProgressBar: (v) => { form._settings.progressBar = v; return form; },
      setLimitOneResponsePerUser: (v) => { form._settings.limitOne = v; return form; },
      setShowLinkToRespondAgain: (v) => { form._settings.respondAgain = v; return form; },
      setAllowResponseEdits: (v) => { form._settings.responseEdits = v; return form; },
      setEmailCollectionType: (t) => { form._settings.emailCollection = t; return form; },
      // Deliberately present so a call would be recorded as a failure: this is
      // VERIFIED collection and would force a Google sign-in.
      setCollectEmail: (v) => { world.calledSetCollectEmail = v; return form; },
      getItems: (type) => (type ? items.filter((i) => i._type === type) : items.slice()),
      moveItem: (from, to) => {
        const [it] = items.splice(from, 1);
        items.splice(to, 0, it);
        return it;
      },
      setDestination: (type, id) => {
        form._destination = { type, id };
        // Google creates the response tab, named however Google names it, with
        // one column per ITEM in form order - duplicate titles included.
        const headers = ['Timestamp'];
        if (form._settings.emailCollection) headers.push('Email Address');
        items.filter((i) => i._type !== ItemType.PAGE_BREAK)
          .forEach((i) => headers.push(i.getTitle()));
        ss._addResponseSheet('Form responses 1', headers);
        return form;
      },
      createResponse: () => {
        const r = {
          withItemResponse: () => r,
          toPrefilledUrl: () => 'https://docs.google.com/forms/d/e/FAKE/viewform?usp=pp_url&entry.1=x'
        };
        return r;
      }
    };

    function makeItem(type) {
      const it = {
        _type: type,
        _title: '',
        _help: '',
        _required: false,
        _choices: [],
        _otherOption: null,
        _goToPage: null,
        getIndex: () => items.indexOf(it),
        getTitle: () => it._title,
        getHelpText: () => it._help,
        isRequired: () => it._required,
        setTitle: (t) => { it._title = t; return it; },
        setHelpText: (t) => { it._help = t; return it; },
        setRequired: (v) => { it._required = v; return it; },
        setChoiceValues: (vals) => { it._choices = vals.map((v) => ({ value: v, go: null })); return it; },
        setChoices: (cs) => { it._choices = cs.slice(); return it; },
        createChoice: (value, go) => ({ value, go: go || null }),
        showOtherOption: (v) => { it._otherOption = v; return it; },
        setGoToPage: (p) => { it._goToPage = p; return it; },
        getGoToPage: () => it._goToPage,
        asMultipleChoiceItem: () => it,
        asCheckboxItem: () => it,
        createResponse: () => ({})
      };
      items.push(it);
      return it;
    }

    form.addMultipleChoiceItem = () => makeItem(ItemType.MULTIPLE_CHOICE);
    form.addTextItem = () => makeItem(ItemType.TEXT);
    form.addParagraphTextItem = () => makeItem(ItemType.PARAGRAPH_TEXT);
    form.addCheckboxItem = () => makeItem(ItemType.CHECKBOX);
    form.addDateItem = () => makeItem(ItemType.DATE);
    form.addPageBreakItem = () => makeItem(ItemType.PAGE_BREAK);
    form.addFileUploadItem = () => {
      if (world.fileUploadThrows) throw new Error('fake: file upload unavailable');
      return makeItem(ItemType.FILE_UPLOAD);
    };
    return form;
  }

  return {
    ItemType,
    DestinationType: { SPREADSHEET: 'SPREADSHEET' },
    EmailCollectionType: {
      DO_NOT_COLLECT: 'DO_NOT_COLLECT',
      VERIFIED: 'VERIFIED',
      RESPONDER_INPUT: 'RESPONDER_INPUT'
    },
    create: (t) => {
      world.formsCreated++;
      world.form = makeForm(t);
      return world.form;
    },
    openById: (id) => {
      if (!world.form || id !== 'FAKE_FORM_ID') throw new Error('fake: no such form');
      return world.form;
    }
  };
}

/* ---------------------------------------------------------------- the world */

function makeWorld(opts) {
  opts = opts || {};
  const world = {
    formsCreated: 0,
    form: null,
    insertedSheets: [],
    mails: [],
    logs: [],
    triggers: [],
    props: {},
    calledSetCollectEmail: undefined,
    fileUploadThrows: !!opts.fileUploadThrows,
    noEmailCollection: !!opts.noEmailCollection
  };

  const ss = makeSpreadsheet(world);
  const formApp = makeFormApp(world, ss);
  if (world.noEmailCollection) delete formApp.EmailCollectionType;

  const sandbox = {
    SpreadsheetApp: {
      getActive: () => ss,
      flush: () => {},
      ProtectionType: { RANGE: 'RANGE' },
      newDataValidation: () => {
        const rule = { kind: null, values: null, range: null, allowInvalid: null };
        const b = {
          requireValueInList: (vals) => { rule.kind = 'list'; rule.values = vals; return b; },
          requireValueInRange: (r) => {
            rule.kind = 'range';
            rule.range = r.getSheet().getName() + '!' + r.getA1Notation();
            return b;
          },
          setAllowInvalid: (v) => { rule.allowInvalid = v; return b; },
          build: () => rule
        };
        return b;
      },
      newConditionalFormatRule: () => {
        const rule = { text: null, formula: null, background: null, ranges: [] };
        const b = {
          whenTextContains: (t) => { rule.text = t; return b; },
          whenFormulaSatisfied: (f) => { rule.formula = f; return b; },
          setBackground: (c) => { rule.background = c; return b; },
          setBold: () => b,
          setRanges: (rs) => { rule.ranges = rs.map((r) => r.getA1Notation()); return b; },
          build: () => rule
        };
        return b;
      }
    },
    FormApp: formApp,
    Charts: { ChartType: { BAR: 'BAR', COLUMN: 'COLUMN' } },
    PropertiesService: {
      getScriptProperties: () => ({
        getProperty: (k) => (k in world.props ? world.props[k] : null),
        setProperty: (k, v) => { world.props[k] = v; }
      })
    },
    MailApp: {
      getRemainingDailyQuota: () => 90,
      sendEmail: (o) => world.mails.push(o)
    },
    Logger: { log: (...a) => world.logs.push(a.join(' ')) },
    ScriptApp: {
      getProjectTriggers: () => world.triggers.slice(),
      deleteTrigger: (t) => { world.triggers = world.triggers.filter((x) => x !== t); },
      newTrigger: (fn) => ({
        forSpreadsheet: () => ({
          onFormSubmit: () => ({ create: () => world.triggers.push(fn + ':submit') }),
          onEdit: () => ({ create: () => world.triggers.push(fn + ':edit') })
        })
      })
    },
    console
  };

  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(CODE, 'utf8'), sandbox);
  vm.runInContext(fs.readFileSync(BUILD, 'utf8'), sandbox);
  world.sandbox = sandbox;
  world.ss = ss;
  return world;
}

/* ------------------------------------------------------------------ checks */

const failures = [];

function check(label, ok, detail) {
  if (ok) { console.log(`   ok   ${label}`); return; }
  const line = `${label}${detail ? ' -- ' + detail : ''}`;
  console.log(`   FAIL ${line}`);
  failures.push(line);
}

function formText(world) {
  const f = world.form;
  return [f.getTitle(), f._settings.description, f._settings.confirmation]
    .concat(f._items.map((i) => i.getTitle() + ' ' + i.getHelpText()))
    .join('\n');
}

/* ------------------------------------------------------------------- run it */

console.log('\n=== buildIntake(), first run on a blank spreadsheet');
const w = makeWorld();
const result = w.sandbox.buildIntake();
const form = w.form;
const items = form._items;
const PAGE_BREAK = w.sandbox.FormApp.ItemType.PAGE_BREAK;
const pageBreaks = items.filter((i) => i._type === PAGE_BREAK);

console.log(`   form: "${form.getTitle()}"`);
console.log(`   items: ${items.length} (${pageBreaks.length} page breaks)`);
console.log(`   tabs now: ${w.ss.getSheets().map((s) => s.getName()).join(', ')}`);
console.log(`   mails: ${w.mails.length}`);
console.log(`   triggers: ${w.triggers.join(', ')}`);

/* --- the form --- */
console.log('\n--- the form');
check('one form was created', w.formsCreated === 1, String(w.formsCreated));
check('its title leads in Vietnamese', /^Đề xuất hỗ trợ Vietnam Hearts/.test(form.getTitle()));
check('five page breaks: four branches plus the final section',
  pageBreaks.length === 5, String(pageBreaks.length));

const branch = items[0];
check('the branch question is the first item',
  /What would you like to offer/.test(branch.getTitle()), branch.getTitle());
check('the branch question is required', branch.isRequired() === true);
check('it offers four routes', branch._choices.length === 4, String(branch._choices.length));
check('every route navigates to a page break',
  branch._choices.every((c) => c.go && c.go._type === PAGE_BREAK),
  branch._choices.map((c) => c.value + '->' + (c.go ? 'page' : 'NOWHERE')).join(', '));
check('the four routes go to four different pages',
  new Set(branch._choices.map((c) => c.go)).size === 4);

const finalPage = pageBreaks[4];
const jumpers = pageBreaks.slice(1, 4);
check('partnership, event and other all jump to the final section',
  jumpers.every((p) => p.getGoToPage() === finalPage),
  jumpers.map((p) => p.getTitle() + '->' + (p.getGoToPage() ? 'final' : 'linear')).join(', '));

const closing = items.filter((i) => /What do you need from us/.test(i.getTitle()));
check('all four branches end with the closing question', closing.length === 4,
  String(closing.length));
check('the closing question is worded identically everywhere',
  new Set(closing.map((i) => i.getTitle())).size === 1);

const standing = items.filter((i) => /We always need these/.test(i.getTitle()))[0];
check('the always-needed question exists', !!standing);
check('it lists exactly five items', standing._choices.length === 5,
  String(standing._choices.length));
check('its Other option is explicitly OFF', standing._otherOption === false,
  String(standing._otherOption));

const consent = items.filter((i) => /I agree that Vietnam Hearts may store/.test(i.getTitle()))[0];
check('the consent question exists', !!consent);
check('consent is required', consent && consent.isRequired() === true);
check('consent links the Cloud Run privacy page',
  /run\.app\/privacy/.test(consent.getHelpText()), consent && consent.getHelpText());
check('consent does NOT link an invented vietnamhearts.org/privacy',
  !/vietnamhearts\.org\/privacy/.test(formText(w)));

check('email collection is responder input',
  form._settings.emailCollection === 'RESPONDER_INPUT',
  String(form._settings.emailCollection));
check('setCollectEmail (which means VERIFIED, forcing a sign-in) is never called',
  w.calledSetCollectEmail === undefined, String(w.calledSetCollectEmail));
check('one response per user is not enforced (it would force a sign-in)',
  form._settings.limitOne === false);
check('the file upload question is last in the supplies section',
  (function () {
    const upload = items.filter((i) => /Photo of the items/.test(i.getTitle()))[0];
    if (!upload) return false;
    return items[upload.getIndex() + 1] === pageBreaks[1];
  })());

/* No automated surface may promise a reply date: the seven-day target is the
   sheet's "Next action by" column and nothing the public reads. */
check('nothing on the form promises a reply within any number of days',
  !/(within|trong)\s+\d+\s*(day|days|ngày|hour|hours|giờ)|\b\d+\s*(day|days|ngày)\b/i
    .test(formText(w)),
  (formText(w).match(/(within|trong)\s+\d+[^\n]{0,20}/i) || [''])[0]);

/* --- the sheet --- */
console.log('\n--- the spreadsheet');
const names = w.ss.getSheets().map((s) => s.getName());
['Form Responses 1', 'Team tasks', 'All work', 'Standing needs', 'Volunteers',
  'Donation tiers', 'Progress', 'Progress chart'].forEach((t) => {
  check(`tab "${t}" exists`, names.indexOf(t) !== -1, names.join(', '));
});
check('the response tab was renamed to the name CONFIG uses',
  names.indexOf('Form responses 1') === -1);

const responses = w.ss.getSheetByName('Form Responses 1');
const headerRow = responses.getRange(1, 1, 1, responses.getLastColumn()).getValues()[0];
console.log(`   A:G -> ${headerRow.slice(0, 7).join(' | ')}`);
check('the seven team columns are A to G in the documented order',
  headerRow.slice(0, 7).join('|') ===
  'Status|Priority|Next action by|Owner|Decision|Decision note|Reply sent',
  headerRow.slice(0, 7).join('|'));
check('column H onward is still what the submitter wrote',
  headerRow[7] === 'Timestamp' && headerRow[8] === 'Email Address',
  headerRow.slice(7, 9).join('|'));

const v = responses._meta.validations;
function ruleFor(col) { return v.filter((x) => x.col === col).map((x) => x.rule)[0]; }
check('Status is a four-value dropdown that rejects anything else',
  ruleFor(1) && ruleFor(1).values.length === 4 && ruleFor(1).allowInvalid === false,
  JSON.stringify(ruleFor(1)));
check('Status includes the BLOCKED state',
  ruleFor(1).values.some((s) => /^BLOCKED/.test(s)), ruleFor(1).values.join(','));
check('Priority is High/Normal/Low and rejects anything else',
  ruleFor(2).values.join(',') === 'High,Normal,Low' && ruleFor(2).allowInvalid === false);
check('Decision offers only Approved and Declined',
  ruleFor(5).values.join(',') === 'Approved,Declined');
check('Owner reads the Volunteers tab, so one edit updates dropdown and Reply-To',
  ruleFor(4) && ruleFor(4).kind === 'range' && /^Volunteers!A2:A200$/.test(ruleFor(4).range),
  JSON.stringify(ruleFor(4)));

const overdue = responses._meta.formats.filter((r) => r.formula)[0];
check('there is an overdue rule on Next action by',
  !!overdue && /TODAY\(\)/.test(overdue.formula) && /<>"DONE"/.test(overdue.formula),
  overdue && overdue.formula);
check('status colours keep the word as well as the colour',
  responses._meta.formats.filter((r) => r.text).length >= 5);
check('row 1 and columns A to C are frozen',
  responses._meta.frozenRows === 1 && responses._meta.frozenColumns === 3);
const prot = responses._meta.protections;
check('the submitter columns are protected with a warning, not a lock',
  prot.length === 1 && prot[0].warningOnly === true, JSON.stringify(prot.map((p) => p.a1)));

const chartTab = w.ss.getSheetByName('Progress chart');
check('the published tab is a QUERY over the team tab, not the team tab itself',
  /QUERY\(Progress!A2:F30/.test(String(chartTab.getRange('A2').getValue())),
  String(chartTab.getRange('A2').getValue()));
check('it only ever selects a project name and two numbers',
  /select Col1, Col3, Col4 where Col6 = 'YES'/.test(String(chartTab.getRange('A2').getValue())));
check('one stacked bar chart was built on it',
  chartTab.getCharts().length === 1 &&
  chartTab.getCharts()[0].options.isStacked === true);
check('the chart reads spare rows, so a new project appears by itself',
  chartTab.getCharts()[0].ranges.join(' ').indexOf('30') !== -1,
  chartTab.getCharts()[0].ranges.join(' '));

const standingTab = w.ss.getSheetByName('Standing needs');
const termsText = String(standingTab.getRange('B2').getValue());
check('the handover block was written into Standing needs!B2', termsText.length > 100);
check('it names the district and no street address',
  /Thạnh Mỹ Tây/.test(termsText) && !/\d+\s+(đường|street|st\.|road)/i.test(termsText));
check('the tab warns against putting the address in that cell',
  /DO NOT put the school address/.test(String(standingTab.getRange('A4').getValue())));
check('the tab says the item list lives in the form, not here',
  /lives in the form/.test(String(standingTab.getRange('A5').getValue())));
check('Standing needs does not restate the five items',
  !/Cooking oil/.test(String(standingTab.getRange('A5').getValue())));

check('the two triggers were installed',
  w.triggers.length === 2 &&
  w.triggers.indexOf('onIntakeFormSubmit:submit') !== -1 &&
  w.triggers.indexOf('onIntakeEdit:edit') !== -1, w.triggers.join(','));

/* --- what it told the team --- */
console.log('\n--- the build report');
check('exactly one email was sent', w.mails.length === 1, String(w.mails.length));
const mail = w.mails[0];
console.log(`   subject: ${mail.subject}`);
check('it went to the team address', mail.to === 'vietnam.hearts.volunteering@gmail.com');
check('it carries the public form URL', /forms\/d\/e\/FAKE\/viewform/.test(mail.htmlBody));
check('it carries the pre-filled link template', /usp=pp_url/.test(mail.htmlBody));
check('it carries the spreadsheet URL', /spreadsheets\/d\/FAKE_SPREADSHEET_ID/.test(mail.htmlBody));
check('it lists the manual steps', /Manual steps/.test(mail.htmlBody));
check('a manual step covers Publish to web', /Publish to web/.test(mail.htmlBody));
check('a manual step covers filter views', /Filter views/i.test(mail.htmlBody));
check('a manual step covers response receipts', /Response receipts/i.test(mail.htmlBody));
check('a manual step covers the spreadsheet time zone',
  /Asia\/Ho Chi Minh/.test(mail.htmlBody));
check('a manual step covers turning DRY_RUN off', /DRY_RUN/.test(mail.htmlBody));
check('it warns against publishing the response tab',
  /Never publish "Entire document" or the response tab/.test(mail.htmlBody));
check('it states the intake adds nothing to Cloud Run',
  /adds nothing to the Cloud Run service or its container image/.test(mail.htmlBody));
check('the subject counts the manual steps', /\d+ manual steps left/.test(mail.subject));
check('buildIntake returns both URLs',
  !!result.formUrl && !!result.sheetUrl, JSON.stringify(result));

/* --- idempotence --- */
console.log('\n=== buildIntake() again, on the sheet it just built');
const tabsBefore = w.ss.getSheets().length;
const headerBefore = responses.getRange(1, 1, 1, responses.getLastColumn()).getValues()[0];
const itemsBefore = items.length;
w.sandbox.buildIntake();
check('no second form was created', w.formsCreated === 1, String(w.formsCreated));
check('no question was added twice', form._items.length === itemsBefore,
  `${itemsBefore} -> ${form._items.length}`);
check('no tab was duplicated', w.ss.getSheets().length === tabsBefore,
  `${tabsBefore} -> ${w.ss.getSheets().length}`);
check('the team columns were not inserted a second time',
  responses.getRange(1, 1, 1, responses.getLastColumn()).getValues()[0].join('|') ===
  headerBefore.join('|'));
check('only one chart still', chartTab.getCharts().length === 1,
  String(chartTab.getCharts().length));
check('the second run reported too', w.mails.length === 2, String(w.mails.length));

/* --- degraded Google APIs --- */
console.log('\n=== a Google account where file upload and the email-collection API are absent');
const w2 = makeWorld({ fileUploadThrows: true, noEmailCollection: true });
w2.sandbox.buildIntake();
const mail2 = w2.mails[0];
check('the build still completes', w2.formsCreated === 1);
check('the missing file upload becomes a manual step',
  /add a \*\*File upload\*\* question/.test(mail2.htmlBody));
check('email collection becomes a manual step naming Responder input',
  /Collect email addresses -> \*\*Responder input\*\*/.test(mail2.htmlBody));
check('that step warns that Verified forces a sign-in',
  /forces submitters to sign in/.test(mail2.htmlBody));
check('no question was silently dropped from the other branches',
  w2.form._items.filter((i) => /What do you need from us/.test(i.getTitle())).length === 4);
/* Without an email column setUp() cannot resolve its headers and refuses to
   install the triggers. The build must still report, because this email is the
   only instruction anybody gets. */
check('the build still emailed the team', w2.mails.length === 1, String(w2.mails.length));
check('it says plainly that the triggers are not installed',
  /Triggers NOT installed/.test(mail2.htmlBody));
check('it tells them to run setUp again afterwards',
  /run <b>setUp\(\)<\/b> from/.test(mail2.htmlBody));
check('it names the consequence of leaving it', /nobody is emailed/.test(mail2.htmlBody));

/* ------------------------------------------------------------------ result */

console.log('');
if (failures.length) {
  console.log(`${failures.length} check(s) FAILED:`);
  failures.forEach((f) => console.log(`  - ${f}`));
  process.exit(1);
}
console.log('all checks passed');
