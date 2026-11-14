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
const { ADDRESS, HOURS, DATE_PROMISE } = require('./forbidden-shapes');

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
      numberFormats: {},       // key(row, col) -> number format
      notes: {},               // key(row, col) -> cell note
      formats: [],             // conditional format rules
      protections: [],
      charts: [],
      hiddenColumns: [],
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
        getFormula: () => {
          const v = cells[key(row, col)];
          return typeof v === 'string' && v.charAt(0) === '=' ? v : '';
        },
        setFormula: (f) => { cells[key(row, col)] = f; return self; },
        clearContent: () => {
          for (let r = row; r < row + numRows; r++) {
            for (let c = col; c < col + numCols; c++) delete cells[key(r, c)];
          }
          return self;
        },
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
        setNumberFormat: (f) => {
          for (let r = row; r < row + numRows; r++) {
            for (let c = col; c < col + numCols; c++) meta.numberFormats[key(r, c)] = f;
          }
          return self;
        },
        getNumberFormat: () => meta.numberFormats[key(row, col)] || '',
        setWrap: () => self,
        setNote: (n) => { meta.notes[key(row, col)] = n; return self; },
        getNote: () => meta.notes[key(row, col)] || '',
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
      _chartBuilder: (spec) => chartBuilder(spec),
      _cells: cells,
      getName: () => meta.name,
      // Like Sheets, a rename drags every formula that referenced the tab.
      setName: (n) => {
        const from = "'" + meta.name + "'!";
        sheets.forEach((other) => Object.keys(other._cells).forEach((k) => {
          const v = other._cells[k];
          if (typeof v === 'string' && v.charAt(0) === '=' && v.indexOf(from) !== -1) {
            other._cells[k] = v.split(from).join("'" + n + "'!");
          }
        }));
        meta.name = n;
        return sheet;
      },
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
        // Like Sheets, every formula's reference to this tab shifts with it.
        const quoted = "'" + meta.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + "'!";
        const ref = new RegExp(quoted + '([A-Z]+)(\\d*)(?::([A-Z]+)(\\d*))?', 'g');
        const toNum = (l) => l.split('').reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0);
        const toLetters = (n) => {
          let out = '';
          for (; n > 0; n = Math.floor((n - 1) / 26)) {
            out = String.fromCharCode(65 + ((n - 1) % 26)) + out;
          }
          return out;
        };
        const shift = (l) => (toNum(l) >= before ? toLetters(toNum(l) + howMany) : l);
        sheets.forEach((other) => Object.keys(other._cells).forEach((k) => {
          const v = other._cells[k];
          if (typeof v !== 'string' || v.charAt(0) !== '=') return;
          other._cells[k] = v.replace(ref, (m, c1, r1, c2, r2) =>
            "'" + meta.name + "'!" + shift(c1) + r1 + (c2 ? ':' + shift(c2) + r2 : ''));
        }));
        return sheet;
      },
      setFrozenRows: (n) => { meta.frozenRows = n; return sheet; },
      setFrozenColumns: (n) => { meta.frozenColumns = n; return sheet; },
      getProtections: () => meta.protections.slice(),
      setConditionalFormatRules: (rules) => { meta.formats = rules.slice(); return sheet; },
      autoResizeColumns: () => sheet,
      setColumnWidth: (c, px) => { (meta.columnWidths ||= {})[c] = px; return sheet; },
      getColumnWidth: (c) => (meta.columnWidths || {})[c] || 100,
      hideColumns: (col, num) => {
        meta.hiddenColumns.push([col, num === undefined ? 1 : num]);
        return sheet;
      },
      getCharts: () => meta.charts.slice(),
      insertChart: (chart) => {
        chart.id = world.nextChartId = (world.nextChartId || 0) + 1;
        meta.charts.push(chart);
        return sheet;
      },
      updateChart: (chart) => {
        const i = meta.charts.findIndex((c) => c.id === chart.id);
        if (i === -1) throw new Error('fake: updateChart on a chart not on this sheet');
        meta.charts[i] = chart;
        return sheet;
      },
      removeChart: (chart) => {
        meta.charts = meta.charts.filter((c) => c !== chart);
        return sheet;
      },
      newChart: () => chartBuilder({ type: null, ranges: [], options: {} })
    };
    function chartBuilder(spec) {
      const b = {
        setChartType: (t) => { spec.type = t; return b; },
        addRange: (r) => { spec.ranges.push(r.getA1Notation()); return b; },
        clearRanges: () => { spec.ranges = []; return b; },
        setOption: (k, v) => { spec.options[k] = v; return b; },
        setNumHeaders: (n) => { spec.numHeaders = n; return b; },
        setPosition: () => b,
        build: () => {
          spec.modify = () => chartBuilder(JSON.parse(JSON.stringify(spec)));
          return spec;
        }
      };
      return b;
    }
    return sheet;
  }

  const ss = {
    getId: () => 'FAKE_SPREADSHEET_ID',
    getUrl: () => 'https://docs.google.com/spreadsheets/d/FAKE_SPREADSHEET_ID/edit',
    getSheets: () => sheets.slice(),
    getSheetByName: (n) => sheets.filter((sh) => sh.getName() === n)[0] || null,
    // A new spreadsheet comes up on the owning account's zone, which was
    // Etc/GMT in the captain's live build, so the fake starts wrong on purpose.
    getSpreadsheetTimeZone: () => world.timeZone,
    setSpreadsheetTimeZone: (tz) => { world.timeZone = tz; return ss; },
    deleteSheet: (sh) => {
      const i = sheets.indexOf(sh);
      if (i === -1) throw new Error('fake: deleteSheet on a sheet of another file');
      if (sheets.length === 1) throw new Error('fake: cannot delete the only sheet');
      sheets.splice(i, 1);
      world.deletedSheets.push(sh.getName());
      return ss;
    },
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
    // FormApp.ItemType has FILE_UPLOAD even though Form has no method to add
    // one: a human can add it in the editor, and that is exactly what has to
    // be detected, because one such item forces a Google sign-in on the whole
    // form from page one.
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
      // Like Forms, throws when the form has no response destination.
      getDestinationId: () => {
        if (!form._destination) throw new Error('fake: no response destination');
        return form._destination.id;
      },
      getTitle: () => form.title,
      setTitle: (t) => { form.title = t; return form; },
      getPublishedUrl: () => 'https://docs.google.com/forms/d/e/FAKE/viewform',
      getEditUrl: () => 'https://docs.google.com/forms/d/FAKE_FORM_ID/edit',
      // Publishing, as Forms has behaved since Google's 2025 change: a form
      // created from a script comes up UNPUBLISHED, so a signed-out visitor
      // gets HTTP 401 and a Google login wall instead of the questions. The
      // first live build handed the team exactly that URL.
      _published: false,
      supportsAdvancedResponderPermissions: () => !world.legacyForm,
      isPublished: () => {
        if (world.legacyForm) throw new Error('fake: legacy form has no publishSettings');
        return form._published;
      },
      setPublished: (v) => {
        if (world.legacyForm) throw new Error('fake: legacy form has no publishSettings');
        world.setPublishedCalls.push(v);
        form._published = !!v;
        return form;
      },
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
    /* Test-only, and deliberately not called addFileUploadItem: FormApp has
       no such method, and the point of this hook is a question a human added
       in the editor rather than one the builder could have created. */
    form._injectItem = (type, title) => makeItem(type).setTitle(title);
    // An account whose Forms API predates publishing: the methods are simply
    // absent, so the builder must feel for them rather than call them.
    if (world.noFormPublishApi) {
      delete form.supportsAdvancedResponderPermissions;
      delete form.isPublished;
      delete form.setPublished;
    }
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

/* --------------------------------------------------- fake advanced Drive v3 */

/* Only Permissions.list and Permissions.create, in the advanced service's
   argument order (resource first, fileId second for create; fileId first for
   list). Responder permissions live behind a view, so a list that does not ask
   for includePermissionsForView='published' must not see them - that is the
   mistake the builder has to avoid, so the fake models it. */
function makeDrive(world) {
  return {
    Permissions: {
      list: (fileId, opts) => {
        world.driveListCalls.push({ fileId, opts: opts || null });
        const published = !!(opts && opts.includePermissionsForView === 'published');
        return {
          permissions: world.drivePermissions.filter((p) =>
            p.fileId === fileId && (published || p.view !== 'published'))
        };
      },
      create: (resource, fileId) => {
        if (world.driveCreateFails) {
          throw new Error('fake Drive: the caller lacks permission to share this file');
        }
        const p = Object.assign(
          { id: 'PERM' + (world.drivePermissions.length + 1), fileId }, resource);
        world.drivePermissions.push(p);
        return p;
      }
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
    props: opts.staleFormId ? { intakeFormId: opts.staleFormId } : {},
    calledSetCollectEmail: undefined,
    timeZone: opts.timeZone === undefined ? 'Etc/GMT' : opts.timeZone,
    deletedSheets: [],
    noEmailCollection: !!opts.noEmailCollection,
    drivePermissions: [],
    driveListCalls: [],
    setPublishedCalls: [],
    noFormPublishApi: !!opts.noFormPublishApi,
    legacyForm: !!opts.legacyForm,
    noDriveService: !!opts.noDriveService,
    driveCreateFails: !!opts.driveCreateFails
  };

  const ss = makeSpreadsheet(world);
  const formApp = makeFormApp(world, ss);
  if (world.noEmailCollection) delete formApp.EmailCollectionType;

  const sandbox = {
    /* A bound script's document lock; nothing else contends for it here. */
    LockService: {
      getDocumentLock: () => ({ tryLock: () => true, releaseLock: () => {} })
    },
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
          requireTextContains: (t) => { rule.kind = 'contains'; rule.values = [t]; return b; },
          setAllowInvalid: (v) => { rule.allowInvalid = v; return b; },
          setHelpText: () => b,
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

  // A script project without the advanced Drive service enabled has no Drive
  // global at all, which is why the builder feels for it with typeof rather
  // than referencing it.
  if (!world.noDriveService) sandbox.Drive = makeDrive(world);

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

/* Every human-readable string on the form: titles, help text AND choice
   values. formText() misses the choices, and a choice value is exactly what
   the pre-filled link template and Code.js compare against, so a flip that
   forgot one would not show up without this. */
function formStrings(world) {
  const f = world.form;
  const out = [f.getTitle(), f._settings.description, f._settings.confirmation];
  f._items.forEach((i) => {
    out.push(i.getTitle(), i.getHelpText());
    (i._choices || []).forEach((c) => out.push(c.value));
  });
  return out.filter((v) => typeof v === 'string' && v.trim() !== '');
}

/* Letters Vietnamese uses and English does not. The place name Thạnh Mỹ Tây
   and the festival Tết are proper nouns that belong in the English half, so
   they come out before the test; "Tiếng Việt" is the name of the language and
   is the right answer to pick in an English list. */
const VI_ONLY = /[ăâđêôơưĂÂĐÊÔƠƯáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]/;
const PROPER_NOUNS = /Thạnh Mỹ Tây|Tết|Tiếng Việt/g;

/* The half before the first " / ", with those proper nouns removed. null for a
   string that is not of the bilingual "A / B" shape. */
function englishHalf(s) {
  const i = s.indexOf(' / ');
  return i === -1 ? null : s.slice(0, i).replace(PROPER_NOUNS, '');
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
check('its title leads in English',
  /^Offer support to Vietnam Hearts \//.test(form.getTitle()), form.getTitle());
check('the form description leads in English',
  /^Want to donate supplies/.test(String(form._settings.description)),
  String(form._settings.description).slice(0, 60));
check('the confirmation message leads in English',
  /^Thank you\. We have your request/.test(String(form._settings.confirmation)),
  String(form._settings.confirmation).slice(0, 60));

/* The property, over titles, help text AND choice values: in every bilingual
   "A / B" string the English half comes first. Checked as a property rather
   than string by string because the risk is a forgotten one, and a choice
   value is both what a submitter clicks and what Code.js compares against. */
const bilingual = formStrings(w).filter((v) => englishHalf(v) !== null);
const viFirst = bilingual.filter((v) => VI_ONLY.test(englishHalf(v)));
check('every bilingual string on the form puts English before Vietnamese',
  viFirst.length === 0, viFirst.join(' || '));
check('that property was tested against the whole form, not a handful of strings',
  bilingual.length >= 30, `${bilingual.length} bilingual strings`);
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
check('each of the five items names the English word first',
  standing._choices.every((c) => /^[A-Za-z]/.test(c.value)),
  standing._choices.map((c) => c.value).join(' || '));

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
/* FormApp.Form has no addFileUploadItem - the live build raised a TypeError
   trying - and neither does the fake form, so a call would throw above. The
   question is a manual step like publishing to the web. */
check('no file upload question was invented on the form',
  items.filter((i) => /Photo of the items/.test(i.getTitle())).length === 0);
/* The blocker the second live build found: the form URL answered HTTP 401 to a
   signed-out client because a script-created form starts unpublished with its
   responders restricted to the editors. Nothing else in this build matters
   until that is fixed, so it is asserted here rather than left to a human. */
check('the build published the form', form.isPublished() === true,
  String(form.isPublished()));
check('it asserted the publish state exactly once on a fresh form',
  w.setPublishedCalls.length === 1 && w.setPublishedCalls[0] === true,
  JSON.stringify(w.setPublishedCalls));
const anyoneResponder = w.drivePermissions.filter((p) =>
  p.fileId === 'FAKE_FORM_ID' && p.type === 'anyone' &&
  p.view === 'published' && p.role === 'reader');
check('anyone with the link can respond, as a published-view Drive reader',
  anyoneResponder.length === 1, JSON.stringify(w.drivePermissions));
check('no permission was created outside the published view',
  w.drivePermissions.every((p) => p.view === 'published'),
  JSON.stringify(w.drivePermissions));
check('it looked for that permission with includePermissionsForView=published',
  w.driveListCalls.length >= 1 && w.driveListCalls.every((c) =>
    c.opts && c.opts.includePermissionsForView === 'published'),
  JSON.stringify(w.driveListCalls));

check('the supplies section now ends with the closing question',
  /What do you need from us/.test(items[pageBreaks[1].getIndex() - 1].getTitle()),
  items[pageBreaks[1].getIndex() - 1].getTitle());

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
const helperQuery = String(chartTab.getRange('E2').getFormula());
const seriesFormula = String(chartTab.getRange('A2').getFormula());
check('the published tab is a QUERY over the team tab, not the team tab itself',
  /QUERY\(Progress!A2:F30/.test(helperQuery), helperQuery);
check('it only ever selects a project name and two numbers',
  /select Col1, Col3, Col2 where Col6 = 'YES'/.test(helperQuery), helperQuery);
check('the helper columns the QUERY lands in are hidden',
  chartTab._meta.hiddenColumns.some((h) => h[0] === 5 && h[1] === 3),
  JSON.stringify(chartTab._meta.hiddenColumns));

/* The live chart stacked "Monthly rice fund (USD) 12382/618" against "Tết
   gifts 2027 (VND) 400000/2200000" on one axis, so the VND bar dwarfed the USD
   one and neither bar meant anything. Every series is now a FRACTION of that
   row's own goal, which is unit-free and so right whatever currency the row is
   in, and the raw amounts travel in the label instead of being lost. */
check('the charted series is each project\'s fraction of its own goal',
  /f,IFERROR\(IF\(F2:F30\/G2:G30>1,1,F2:F30\/G2:G30\),0\)/.test(seriesFormula),
  seriesFormula);
check('the second series is the remainder of that same fraction',
  /,f,1-f\}/.test(seriesFormula), seriesFormula);
check('the raw amounts are kept, in the row label, with the goal beside them',
  /TEXT\(F2:F30/.test(seriesFormula) && /TEXT\(G2:G30/.test(seriesFormula),
  seriesFormula);
check('the label still carries the project name, which names its currency',
  /\{E2:E30&/.test(seriesFormula), seriesFormula);
/* A formula padded to row 30 with "" hands the chart a value in every spare
   row, which a bar chart draws as an empty category. One FILTER on the QUERY's
   own rows spills only the projects, and B2:C30 must stay empty for it. */
check('the series spill only the rows the QUERY returned',
  /FILTER\(\{.*\},E2:E30<>""\)/.test(seriesFormula), seriesFormula);
check('nothing in B2:C30 can block that spill',
  chartTab.getRange('B2:C30').getValues().every((r) => r.every((v) => v === '')),
  JSON.stringify(chartTab.getRange('B2:C3').getValues()));
check('one stacked bar chart was built on it',
  chartTab.getCharts().length === 1 &&
  chartTab.getCharts()[0].options.isStacked === true);
check('its value axis is a percent axis pinned to 0..1',
  (function () {
    const h = chartTab.getCharts()[0].options.hAxis || {};
    return h.format === 'percent' && h.viewWindow &&
      h.viewWindow.min === 0 && h.viewWindow.max === 1;
  })(), JSON.stringify(chartTab.getCharts()[0].options.hAxis));
check('the chart treats row 1 as a header, so "Project" is not a bar',
  chartTab.getCharts()[0].numHeaders === 1, String(chartTab.getCharts()[0].numHeaders));
check('neither raw-amount column is charted',
  chartTab.getCharts()[0].ranges.every((r) => !/^[EFG]/.test(r)),
  chartTab.getCharts()[0].ranges.join(' '));
check('the chart reads spare rows, so a new project appears by itself',
  chartTab.getCharts()[0].ranges.join(' ').indexOf('30') !== -1,
  chartTab.getCharts()[0].ranges.join(' '));

const standingTab = w.ss.getSheetByName('Standing needs');
const termsText = String(standingTab.getRange('B2').getValue());
check('the handover block was written into Standing needs!B2', termsText.length > 100);
check('it names the public area', /Thạnh Mỹ Tây/.test(termsText));
/* Photos used to be a file upload question on the form. One such question
   forces every respondent to sign in, so the ask moved here, where it rides
   into the acknowledgement next to the team's Zalo or WhatsApp number. */
check('it asks for photos over Zalo or WhatsApp, English half first',
  /photo/i.test(termsText) &&
  termsText.search(/[Pp]hoto/) < termsText.indexOf('---'),
  (termsText.match(/[^\n]*[Pp]hoto[^\n]*/) || [''])[0]);
check('the Vietnamese half asks for them too',
  /ảnh/i.test(termsText.slice(termsText.indexOf('---'))),
  (termsText.slice(termsText.indexOf('---')).match(/[^\n]*[Ảả]nh[^\n]*/) || [''])[0]);
check('it holds nothing shaped like a street address, in either language',
  !ADDRESS.test(termsText), (termsText.match(ADDRESS) || [''])[0]);
check('it states no opening hours, in either language',
  !HOURS.test(termsText), (termsText.match(HOURS) || [''])[0]);
check('it promises no reply date, in either language',
  !DATE_PROMISE.test(termsText), (termsText.match(DATE_PROMISE) || [''])[0]);
check('the tab warns against putting the address in that cell',
  /DO NOT put the school address/.test(String(standingTab.getRange('A4').getValue())));
check('the tab says the item list lives in the form, not here',
  /lives in the form/.test(String(standingTab.getRange('A5').getValue())));
check('Standing needs does not restate the five items',
  !/Cooking oil/.test(String(standingTab.getRange('A5').getValue())));

/* An inline array literal, ={"a","b";"c","d"}: "," separates columns and ";"
   separates rows. Parsed into rows so the check is about the grid it makes. */
function arrayLiteralRows(formula) {
  const m = /^=\{(.*)\}$/.exec(String(formula));
  if (!m) return null;
  return m[1].split(';').map((r) => r.split(',').map((c) => c.replace(/^"|"$/g, '')));
}
/* The header of the response-tab column that All work shows as "What". */
function allWorkWhatHeader(world) {
  const q = world.ss.getSheetByName('All work').getRange('A2').getFormula();
  const m = /INDIRECT\("'Form Responses 1'!([A-Z]+)2:\1"\);/.exec(q);
  if (!m) return null;
  const letter = m[1];
  let col = 0;
  for (const ch of letter) col = col * 26 + (ch.charCodeAt(0) - 64);
  return world.ss.getSheetByName('Form Responses 1').getRange(1, col).getValue();
}
check('All work shows the offer question as "What"',
  /What would you like to offer/.test(allWorkWhatHeader(w)), String(allWorkWhatHeader(w)));
/* Publish to web renders every visible cell, so guidance on a published tab
   must be a cell note, never a cell value. */
for (const [tabName, needle] of [['Donation tiers', /Khoảng \/ About/], ['Progress chart', /Published to the web/]]) {
  const t = w.ss.getSheetByName(tabName);
  const values = t.getRange(1, 1, 1, 12).getValues()[0].filter((v) => typeof v === 'string' && v.length > 60);
  check(`${tabName}: no long guidance text in a visible header cell`, values.length === 0, JSON.stringify(values));
  check(`${tabName}: the guidance is a note on A1`, needle.test(t.getRange('A1').getNote()), t.getRange('A1').getNote());
}
const allWork = w.ss.getSheetByName('All work');
const allWorkHeader = arrayLiteralRows(allWork.getRange('A1').getValue());
check('All work headers are one row of six columns, clear of the QUERY in A2',
  !!allWorkHeader && allWorkHeader.length === 1 && allWorkHeader[0].length === 6,
  JSON.stringify(allWorkHeader));
const chartHeader = arrayLiteralRows(chartTab.getRange('A1').getValue());
check('Progress chart headers are one row of three columns',
  !!chartHeader && chartHeader.length === 1 && chartHeader[0].length === 3,
  JSON.stringify(chartHeader));

const tiersTab = w.ss.getSheetByName('Donation tiers');
check('the tiers tab heads the English outcome before the Vietnamese one',
  tiersTab.getRange('A1:E1').getValues()[0].join('|') ===
  'VND|USD (approx)|Outcome (English)|Outcome (Tiếng Việt)|As of',
  tiersTab.getRange('A1:E1').getValues()[0].join('|'));
check('and its rows follow that order, English in C and Vietnamese in D',
  /^Rice for one child/.test(String(tiersTab.getRange('C2').getValue())) &&
  /^Gạo cho một em/.test(String(tiersTab.getRange('D2').getValue())),
  tiersTab.getRange('C2:D2').getValues()[0].join(' || '));

const volunteersTab = w.ss.getSheetByName('Volunteers');
const ownerColumn = volunteersTab.getRange(2, 1, 199, 1).getValues()
  .map((r) => String(r[0] || '')).filter((v) => v.trim());
check('the Owner dropdown range Volunteers!A2:A200 starts empty',
  ownerColumn.length === 0, ownerColumn.join(' | '));
const emailRule = volunteersTab._meta.validations.filter((x) => x.col === 2)[0];
check('the Volunteers Email column rejects anything without an @',
  !!emailRule && emailRule.rule.kind === 'contains' &&
  emailRule.rule.values[0] === '@' && emailRule.rule.allowInvalid === false &&
  emailRule.row === 2, JSON.stringify(emailRule));
check('the Volunteers instructions sit outside the Name column',
  /One row per volunteer/.test(String(volunteersTab.getRange('E1').getValue())));

/* A new spreadsheet comes up on the owning account's zone. The captain's live
   build came up Etc/GMT, which reads every "Next action by" date a day off to
   the team in Vietnam, so the build pins it rather than asking. */
console.log('\n--- the spreadsheet time zone and the default tab');
check('the build set the spreadsheet time zone to Asia/Ho_Chi_Minh',
  w.timeZone === 'Asia/Ho_Chi_Minh', String(w.timeZone));
check('it says so in the build notes',
  /Set the spreadsheet time zone to Asia\/Ho_Chi_Minh/.test(w.mails[0].htmlBody));
check('Google\'s blank default tab was deleted',
  names.indexOf('Sheet1') === -1 && w.deletedSheets.indexOf('Sheet1') !== -1,
  names.join(', '));
check('the deletion is reported in the notes',
  /Deleted the empty default "Sheet1" tab/.test(w.mails[0].htmlBody));

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
/* The one check nothing automated can make, so the email that is a
   volunteer's only set of instructions has to ask for it by hand. */
check('it tells them to open the form signed out before sharing it',
  /private browsing window, signed out of Google/.test(mail.htmlBody) &&
  /You must see the questions/.test(mail.htmlBody));
check('it carries the spreadsheet URL', /spreadsheets\/d\/FAKE_SPREADSHEET_ID/.test(mail.htmlBody));
check('it lists the manual steps', /Manual steps/.test(mail.htmlBody));
check('a manual step covers Publish to web', /Publish to web/.test(mail.htmlBody));
check('a manual step covers filter views', /Filter views/i.test(mail.htmlBody));
check('a manual step covers response receipts', /Response receipts/i.test(mail.htmlBody));
check('NO manual step asks for the spreadsheet time zone any more',
  !/<li>[^<]*Time zone/.test(mail.htmlBody) && !/File -&gt; Settings/.test(mail.htmlBody),
  (mail.htmlBody.match(/<li>[^<]*Time zone[^<]*/) || [''])[0]);
/* The second live build's 401 was not the publish state after all. The team
   had added the File upload question the old manual step asked them to add,
   and Google's own settings note says a form with file upload questions
   requires respondents to sign in - for the WHOLE form, from page one, so
   "optional and last" never deferred anything. The step is gone and photos
   are asked for over Zalo or WhatsApp instead. */
check('NO manual step asks for a file upload question any more',
  !/file upload/i.test(mail.htmlBody),
  (mail.htmlBody.match(/<li>[^<]*[Ff]ile upload[^<]*/) || [''])[0]);
check('no manual step quotes a TypeError at a volunteer',
  !/TypeError/.test(mail.htmlBody));
check('the supplies section asks for photos by reply or Zalo instead',
  w.form._items.some((i) => /photo/i.test(i.getHelpText()) &&
    /reply/.test(i.getHelpText()) && /Zalo/.test(i.getHelpText())),
  w.form._items.map((i) => i.getHelpText()).filter(Boolean).join(' || '));
check('nothing on the form asks for a file or an attachment',
  !/file upload|attach a|upload a/i.test(formText(w)),
  (formText(w).match(/[^\n]*(file upload|attach a|upload a)[^\n]*/i) || [''])[0]);
check('a manual step covers turning DRY_RUN off', /DRY_RUN/.test(mail.htmlBody));
check('a manual step keeps the spreadsheet\'s sharing list narrow',
  /Share the spreadsheet only with the named volunteers who handle requests, as editors; never set General access to Anyone with the link/
    .test(mail.htmlBody));
check('the notes say the form was published and opened to anyone with the link',
  /Published the form/.test(mail.htmlBody) &&
  /respond to it without signing in/.test(mail.htmlBody));
check('NO manual step asks a volunteer to publish the form on a clean build',
  !/Responders -&gt; <b>Anyone with the link<\/b>/.test(mail.htmlBody),
  (mail.htmlBody.match(/<li>[^<]*Responders[^<]*/) || [''])[0]);
check('the build reports no orphaned response tab on a clean build',
  !/old form/.test(mail.htmlBody));
check('it warns against publishing the response tab',
  /Never publish "Entire document" or the response tab/.test(mail.htmlBody));
check('it states the intake adds nothing to Cloud Run',
  /adds nothing to the Cloud Run service or its container image/.test(mail.htmlBody));
check('the subject counts the manual steps', /\d+ manual steps left/.test(mail.subject));
/* The email is HTML, and the steps are written with **bold** and `code` the
   way the README is. Pasted in raw, a literal `<iframe>` became a real iframe in
   the team's inbox and swallowed every step after it. */
function renderedSteps(m) {
  const ol = /<ol>([\s\S]*?)<\/ol>/.exec(m.htmlBody);
  return ol ? (ol[1].match(/<li>/g) || []).length : -1;
}
function checkRendersAsHtml(label, m) {
  const want = Number((/(\d+) manual steps left/.exec(m.subject) || [])[1]);
  check(`${label}: every manual step renders as its own list item`,
    renderedSteps(m) === want, `${renderedSteps(m)} of ${want}`);
  check(`${label}: no raw markdown asterisks or backticks reach the email`,
    !/\*\*|`/.test(m.htmlBody));
  check(`${label}: no tag other than the email's own markup reaches the email`,
    (m.htmlBody.match(/<\/?([a-z0-9]+)/gi) || [])
      .map((t) => t.replace(/[</]/g, '').toLowerCase())
      .every((t) => ['p', 'ul', 'ol', 'li', 'b', 'a', 'h3', 'code', 'br'].indexOf(t) !== -1),
    (m.htmlBody.match(/<\/?([a-z0-9]+)/gi) || []).join(' '));
}
checkRendersAsHtml('clean build', mail);
check('the iframe and the number placeholder show as text',
  /&lt;iframe&gt;/.test(mail.htmlBody) && /&lt;số&gt; \/ &lt;number&gt;/.test(mail.htmlBody));
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

/* The throwaway sheet from the first live run was built by the previous
   version: a raw-amount chart titled "Vietnam Hearts - progress", a note in
   F1 where E1's header now spills, and per-column formulas in B2 and C2. A
   re-run must converge on the current shape rather than keep any of it. */
console.log('\n=== buildIntake() over a Progress chart tab left by the previous version');
chartTab._meta.charts = [chartTab._chartBuilder({ id: 'published-oid', type: 'BAR',
  ranges: ['A1:A30', 'B1:C30'],
  options: { title: 'Vietnam Hearts - progress', isStacked: true } }).build()];
chartTab.getRange('F1').setValue('Helper columns - leave alone.');
chartTab.getRange('I1').setValue('Published to the web. Nothing personal may ever be added.');
chartTab.setColumnWidth(9, 420);
const oldTiers = w.ss.getSheetByName('Donation tiers');
oldTiers.getRange('G1').setValue('Head the published table "Khoảng / About".');
oldTiers.setColumnWidth(7, 420);
chartTab.getRange('B2').setFormula('=ARRAYFORMULA(F2:F30)');
chartTab.getRange('C2').setFormula('=ARRAYFORMULA(G2:G30)');
w.sandbox.buildIntake();
const upgraded = chartTab.getCharts();
check('the old chart was replaced, not kept beside or instead of the new one',
  upgraded.length === 1 && upgraded[0].options.title === 'Vietnam Hearts - percent funded' &&
  (upgraded[0].options.hAxis || {}).format === 'percent',
  JSON.stringify(upgraded.map((c) => c.options.title)));
check('the published chart kept its id, so the website\'s iframe still points at it',
  upgraded.length === 1 && upgraded[0].id === 'published-oid',
  JSON.stringify(upgraded.map((c) => c.id)));
check('it still reads only A:C after the update',
  upgraded[0].ranges.join(' ') === 'A1:A30 B1:C30', upgraded[0].ranges.join(' '));
check('the old F1 note no longer sits where the E1 header spills',
  chartTab.getRange('F1').getValue() === '', String(chartTab.getRange('F1').getValue()));
check('the old B2 and C2 formulas no longer block the A2 spill',
  chartTab.getRange('B2').getValue() === '' && chartTab.getRange('C2').getValue() === '',
  chartTab.getRange('B2:C2').getValues()[0].join(' | '));
check('the old Progress chart guidance column is cleared and narrowed again',
  chartTab.getRange('I1').getValue() === '' && chartTab.getColumnWidth(9) === 100,
  `${chartTab.getRange('I1').getValue()} / ${chartTab.getColumnWidth(9)}`);
check('the old Donation tiers guidance column is cleared and narrowed again',
  oldTiers.getRange('G1').getValue() === '' && oldTiers.getColumnWidth(7) === 100,
  `${oldTiers.getRange('G1').getValue()} / ${oldTiers.getColumnWidth(7)}`);
check('both re-runs reported too', w.mails.length === 3, String(w.mails.length));
check('neither re-run published an already published form again',
  w.setPublishedCalls.length === 1, JSON.stringify(w.setPublishedCalls));
check('neither re-run added a second anyone-with-link permission',
  w.drivePermissions.filter((p) => p.type === 'anyone').length === 1,
  JSON.stringify(w.drivePermissions));

/* --- degraded Google APIs --- */
console.log('\n=== a Google account where the email-collection API is absent');
const w2 = makeWorld({ noEmailCollection: true });
w2.sandbox.buildIntake();
const mail2 = w2.mails[0];
check('the build still completes', w2.formsCreated === 1);
check('the file upload step is gone from the degraded build too',
  !/file upload/i.test(mail2.htmlBody),
  (mail2.htmlBody.match(/<li>[^<]*[Ff]ile upload[^<]*/) || [''])[0]);
check('All work still shows the offer question when the tab has no email column',
  /What would you like to offer/.test(allWorkWhatHeader(w2)), String(allWorkWhatHeader(w2)));
check('email collection becomes a manual step naming Responder input',
  /Collect email addresses -&gt; <b>Responder input<\/b>/.test(mail2.htmlBody));
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
checkRendersAsHtml('degraded build', mail2);

/* --- the remembered form is gone, its response tab is not --- */
console.log('\n=== buildIntake() when the remembered form cannot be opened but its tab remains');
const w3 = makeWorld({ staleFormId: 'GONE_FORM_ID' });
const oldTab = w3.ss._addResponseSheet('Form Responses 1',
  ['Status', 'Priority', 'Next action by', 'Owner', 'Decision', 'Decision note',
    'Reply sent', 'Timestamp', 'Email Address', 'Your name']);
oldTab.getRange(2, 1, 1, 10).setValues([['IN PROGRESS', 'High', '', 'Mai', 'Approved',
  '', 'stamped', 'yesterday', 'old.donor@example.com', 'Old donor']]);
const allWork3 = w3.ss.insertSheet('All work');
allWork3.getRange('A1').setFormula('={"Where"}');
allWork3.getRange('A2').setFormula(w3.sandbox.allWorkQuery_());
w3.sandbox.buildIntake();
const mail3 = w3.mails[0];
checkRendersAsHtml('orphan build', mail3);
const names3 = w3.ss.getSheets().map((s) => s.getName());
console.log(`   tabs now: ${names3.join(', ')}`);
check('a new form was created', w3.formsCreated === 1, String(w3.formsCreated));
check('the new form is linked to this spreadsheet',
  !!w3.form._destination && w3.form._destination.id === w3.ss.getId(),
  JSON.stringify(w3.form._destination));
const kept = w3.ss.getSheets().filter((s) => s !== w3.ss.getSheetByName('Form Responses 1') &&
  s.getRange(2, 9).getValue() === 'old.donor@example.com');
check('the old submissions survive under another name', kept.length === 1 &&
  kept[0] === oldTab, names3.join(', '));
check('the old tab was not overwritten',
  oldTab.getRange(1, 1, 1, 10).getValues()[0].join('|') ===
  'Status|Priority|Next action by|Owner|Decision|Decision note|Reply sent|Timestamp|Email Address|Your name');
const fresh = w3.ss.getSheetByName('Form Responses 1');
check('"Form Responses 1" is now the new form\'s tab', fresh && fresh !== oldTab &&
  fresh.getRange(2, 9).getValue() === '');
const a2 = allWork3.getRange('A2').getFormula();
check('the All work query reads the new response tab after the rename',
  a2.indexOf("'Form Responses 1'!A2:D") !== -1 && a2.indexOf('old form') === -1, a2);
check('every All work range is INDIRECT text, so form row inserts cannot shift it',
  !/[^"]'(Form Responses 1|Team tasks)'!/.test(a2.replace(/INDIRECT\("'[^"]*"\)/g, '')) &&
  (a2.match(/INDIRECT\(/g) || []).length === 6, a2);
check('requests show the offer type as What, not the Decision note',
  /What would you like to offer/.test(allWorkWhatHeader(w3)), String(allWorkWhatHeader(w3)));
check('All work formats Next action by as a date',
  allWork3.getRange(2, 4).getNumberFormat &&
  allWork3.getRange(2, 4).getNumberFormat() === 'yyyy-mm-dd');
check('the All work query is exactly the one the build generates',
  a2 === w3.sandbox.allWorkQuery_(), a2);
check('the All work header row is rewritten too',
  /"Where","Status","Priority"/.test(allWork3.getRange('A1').getFormula()),
  allWork3.getRange('A1').getFormula());
check('no manual step asks a volunteer to fix that formula',
  !/check that the formula/.test(mail3.htmlBody));
check('the triggers were installed against the new tab',
  w3.triggers.length === 2, w3.triggers.join(', '));
check('the build email names the renamed tab in its notes',
  /was not written by this form/.test(mail3.htmlBody) &&
  mail3.htmlBody.indexOf(oldTab.getName()) !== -1);
check('a manual step asks someone to decide about the old submissions',
  /Decide what to do with the "Form Responses 1 \(old form\)" tab/.test(mail3.htmlBody));
check('the subject counts that extra step',
  Number((mail3.subject.match(/(\d+) manual steps left/) || [])[1]) ===
  Number((mail.subject.match(/(\d+) manual steps left/) || [])[1]) + 1,
  mail3.subject + ' vs ' + mail.subject);

console.log('\n=== a default tab that is NOT empty is kept, never deleted');
const w6 = makeWorld();
w6.ss.getSheetByName('Sheet1').getRange('A1').setValue('rice order, do not delete');
w6.sandbox.buildIntake();
check('the non-empty default tab survives',
  w6.ss.getSheets().map((s) => s.getName()).indexOf('Sheet1') !== -1 &&
  w6.deletedSheets.length === 0, w6.deletedSheets.join(', '));
check('its one cell is untouched',
  w6.ss.getSheetByName('Sheet1').getRange('A1').getValue() === 'rice order, do not delete');
check('a manual step hands that decision to a human',
  /default "Sheet1" tab has something on it/.test(w6.mails[0].htmlBody));

console.log('\n=== the same, when the All work query was edited by hand');
const w4 = makeWorld({ staleFormId: 'GONE_FORM_ID' });
w4.ss._addResponseSheet('Form Responses 1', ['Status', 'Timestamp']);
const allWork4 = w4.ss.insertSheet('All work');
allWork4.getRange('A1').setFormula('={"Where"}');
allWork4.getRange('A2').setFormula("='Team tasks'!A2:E");
w4.sandbox.buildIntake();
check('a hand-edited query is rebuilt to read the response tab',
  allWork4.getRange('A2').getFormula() === w4.sandbox.allWorkQuery_(),
  allWork4.getRange('A2').getFormula());

console.log('\n=== All work over a response tab without the offer question');
const w7 = makeWorld();
w7.ss._addResponseSheet('Form Responses 1',
  ['Status', 'Priority', 'Next action by', 'Owner', 'Decision', 'Decision note',
    'Reply sent', 'Timestamp', 'Your name', 'Phone']);
const notes7 = [];
w7.sandbox.buildAllWorkTab_(w7.ss, notes7);
check('it shows the Decision note as What rather than whatever sits in column J',
  allWorkWhatHeader(w7) === 'Decision note', String(allWorkWhatHeader(w7)));
check('the build notes say All work fell back to the Decision note',
  notes7.some((n) => /shows each request's Decision note/.test(n)), JSON.stringify(notes7));

/* The fake must rewrite formulas the way Sheets does, or the checks above
   pass on text that real Sheets would have changed under them. */
console.log('\n=== the fake spreadsheet follows renames and column inserts');
const w5 = makeWorld();
const target = w5.ss.insertSheet('Form Responses 1');
const reader = w5.ss.insertSheet('Reader');
reader.getRange('A1').setFormula("={'Form Responses 1'!A2:D;'Form Responses 1'!F2:F}");
target.insertColumnsBefore(1, 7);
check('inserting columns shifts every reference to that tab',
  reader.getRange('A1').getFormula() === "={'Form Responses 1'!H2:K;'Form Responses 1'!M2:M}",
  reader.getRange('A1').getFormula());
target.setName('Moved');
check('renaming a tab makes every reference follow it',
  reader.getRange('A1').getFormula() === "={'Moved'!H2:K;'Moved'!M2:M}",
  reader.getRange('A1').getFormula());


/* --- publishing, when it cannot be done from script -----------------------
 *
 * Four ways the publish can fail, and all four must degrade the same way: the
 * build finishes, and the step lands FIRST in the manual list, because a form
 * nobody outside the team can open makes every other step pointless.
 */
const FIRST_STEP_IS_PUBLISH = /<ol>\s*<li>In the form: <b>Publish<\/b>/;

function checkPublishDegraded(label, world) {
  const m = world.mails[0];
  check(`${label}: the build still completes`, world.formsCreated === 1,
    String(world.formsCreated));
  check(`${label}: it still emailed the team`, world.mails.length === 1,
    String(world.mails.length));
  check(`${label}: publishing is a manual step`,
    /Responders -&gt; <b>Anyone with the link<\/b>/.test(m.htmlBody),
    (m.htmlBody.match(/<li>[^<]{0,120}/g) || []).join(' | '));
  check(`${label}: it is the FIRST manual step`,
    FIRST_STEP_IS_PUBLISH.test(m.htmlBody),
    (/<ol>\s*<li>[^<]{0,80}/.exec(m.htmlBody) || [''])[0]);
  check(`${label}: it says why that one comes first`,
    /nothing else on this list matters/.test(m.htmlBody));
  check(`${label}: it gives the signed-out check command`,
    /%\{http_code\}/.test(m.htmlBody) && /<code>200<\/code>/.test(m.htmlBody),
    (m.htmlBody.match(/curl[^<]{0,120}/) || [''])[0]);
  check(`${label}: it does not quote a raw exception at a volunteer`,
    !/TypeError|ReferenceError|Exception:/.test(m.htmlBody),
    (m.htmlBody.match(/(TypeError|ReferenceError|Exception:)[^<]{0,60}/) || [''])[0]);
  checkRendersAsHtml(label, m);
}

/* --- a file upload question somebody added in the editor -------------------
 *
 * One such item and Forms makes every respondent sign in, which is the login
 * wall the second live build hit. The build cannot delete it (there is no API
 * for the item at all), so it has to say so, first, above even the publish
 * step: a published form with a file upload question is still unusable.
 */
/* --- the rows a dead trigger missed ----------------------------------------
 *
 * Live pass 3: adding the advanced Drive service to appsscript.json added a
 * scope, and after clasp push every INSTALLED trigger stopped firing until
 * the owner re-authorized. A real submission landed with a Timestamp and
 * nothing else. Running buildIntake() in the editor is what re-authorizing
 * looks like, so the repair belongs in the same step as the cause's fix.
 */
console.log('\n=== buildIntake() repairs rows that arrived while the triggers were dead');
const w14 = makeWorld();
w14.sandbox.buildIntake();
const resp14 = w14.ss.getSheetByName('Form Responses 1');
const hdr14 = resp14.getRange(1, 1, 1, resp14.getLastColumn()).getValues()[0];
const colOf = (needle) => w14.sandbox.col_(hdr14, needle) + 1;
// One row exactly as Forms writes it when no trigger is listening: a
// Timestamp, the submitter's answers, and none of the three team stamps.
resp14.getRange(2, colOf('Timestamp')).setValue(new Date('2026-11-14T09:40:00Z'));
resp14.getRange(2, colOf('Email Address')).setValue('missed@example.com');
resp14.getRange(2, colOf('Your name')).setValue('Linh');
const mailsBefore14 = w14.mails.length;
w14.sandbox.buildIntake();
check('the missed row was stamped by the build',
  String(resp14.getRange(2, colOf('Status')).getValue()) === 'TODO',
  String(resp14.getRange(2, colOf('Status')).getValue()));
check('its submitter was acknowledged',
  w14.mails.some((m) => /da nhan de xuat/.test(m.subject || '')),
  w14.mails.slice(mailsBefore14).map((m) => m.subject).join(' | '));
check('the build email says how many rows it repaired',
  /1 submission that arrived while the triggers were not running/
    .test(w14.mails[w14.mails.length - 1].htmlBody),
  (w14.mails[w14.mails.length - 1].htmlBody.match(/<li>[^<]*repair[^<]*/i) || [''])[0]);
const mailsAfter14 = w14.mails.length;
w14.sandbox.buildIntake();
check('a later build does not acknowledge that row again',
  w14.mails.length === mailsAfter14 + 1,
  `${mailsAfter14} -> ${w14.mails.length}`);
check('and says nothing about repairs when there were none',
  !/arrived while the triggers were not running/
    .test(w14.mails[w14.mails.length - 1].htmlBody));
check('a clean build reports no repair line at all',
  !/arrived while the triggers were not running/.test(mail.htmlBody));

console.log('\n=== a file upload question added by hand in the form editor');
const w12 = makeWorld();
w12.sandbox.buildIntake();
w12.form._injectItem('FILE_UPLOAD', 'Photo of the items / Ảnh của đồ');
w12.sandbox.buildIntake();
const mail12 = w12.mails[1];
check('the build reports it', /file upload question/i.test(mail12.htmlBody),
  (mail12.htmlBody.match(/<li>[^<]{0,140}/g) || []).join(' | '));
check('it is the FIRST manual step',
  /<ol>\s*<li>In the form: <b>delete<\/b> the file upload question/.test(mail12.htmlBody),
  (/<ol>\s*<li>[^<]{0,100}/.exec(mail12.htmlBody) || [''])[0]);
check('it says why: every submitter is forced to sign in',
  /forces every submitter to sign in/.test(mail12.htmlBody));
const step12 = ((/<ol>([\s\S]*?)<\/ol>/.exec(mail12.htmlBody) || ['', ''])[1]
  .split('<li>')[1] || '');
const conditionHelp = w12.form._items
  .filter((i) => /^Condition/.test(i.getTitle())).map((i) => i.getHelpText())[0];
/* A form an earlier version built has no photo ask on Condition and the
   builder never rewrites a question, so the step has to hand the team the
   exact text the builder writes on a new form - the same string, not a copy. */
check('the Condition help text the build writes is the shared constant',
  conditionHelp === w12.sandbox.CONDITION_PHOTO_HELP, conditionHelp);
check('the step quotes that same help text in full, for the team to paste',
  step12.indexOf(w12.sandbox.CONDITION_PHOTO_HELP) !== -1, step12.slice(0, 300));
check('the quoted help text reads English first',
  /^Photos/.test(w12.sandbox.CONDITION_PHOTO_HELP) &&
  w12.sandbox.CONDITION_PHOTO_HELP.indexOf(' / Ảnh') > 0);
check('the step does not claim the supplies section already asks for photos',
  !/in the supplies section/i.test(step12), step12.slice(0, 300));
check('the step says the acknowledgement asks for photos on any form',
  /acknowledgement email already asks every supplies submitter for photos/
    .test(step12), step12.slice(0, 300));
check('the step asks for the B2 photo line when the cell predates it',
  /Standing needs'!B2/.test(step12) && /photo/i.test(step12), step12.slice(0, 400));
const b2Lines12 = String(w12.ss.getSheetByName('Standing needs').getRange('B2')
  .getValue()).split('\n');
const sep12 = b2Lines12.indexOf('---');
const enAt12 = b2Lines12.indexOf(w12.sandbox.TERMS_PHOTO_LINE_EN);
const viAt12 = b2Lines12.indexOf(w12.sandbox.TERMS_PHOTO_LINE_VI);
check('the B2 the build writes holds each photo line under its own number line',
  enAt12 > 0 && enAt12 < sep12 && /WhatsApp/.test(b2Lines12[enAt12 - 1]) &&
  viAt12 > sep12 && /WhatsApp/.test(b2Lines12[viAt12 - 1]),
  `en=${enAt12} vi=${viAt12} sep=${sep12}`);
check('the step quotes both of those B2 photo lines, English then Vietnamese',
  step12.indexOf(w12.sandbox.TERMS_PHOTO_LINE_EN) !== -1 &&
  step12.indexOf(w12.sandbox.TERMS_PHOTO_LINE_VI) >
    step12.indexOf(w12.sandbox.TERMS_PHOTO_LINE_EN),
  step12.slice(0, 600));
check('the step places the Vietnamese line under the Vietnamese number, below ---',
  /under the Vietnamese one, below the --- line/.test(step12), step12.slice(0, 600));
check('the step promises no number in the email',
  !/number is in|số điện thoại nằm/i.test(step12), step12.slice(0, 300));
checkRendersAsHtml('file upload present', mail12);

console.log('\n=== both a file upload question and no way to publish');
const w13 = makeWorld({ noFormPublishApi: true });
w13.sandbox.buildIntake();
w13.form._injectItem('FILE_UPLOAD', 'Photo of the items / Ảnh của đồ');
w13.sandbox.buildIntake();
const steps13 = (/<ol>([\s\S]*?)<\/ol>/.exec(w13.mails[1].htmlBody) || ['', ''])[1]
  .split('<li>').slice(1);
check('the file upload step comes first and the publish step second',
  /^In the form: <b>delete<\/b> the file upload question/.test(steps13[0] || '') &&
  /^In the form: <b>Publish<\/b>/.test(steps13[1] || ''),
  steps13.slice(0, 2).map((x) => x.slice(0, 60)).join(' || '));

console.log('\n=== a Forms API with no publishing methods at all');
const w7 = makeWorld({ noFormPublishApi: true });
w7.sandbox.buildIntake();
checkPublishDegraded('no publish API', w7);
/* The two halves are independent on purpose: the responder permission is
   still granted, so the human's manual step is one click on Publish rather
   than a trip through the responder list as well. */
check('the responder half was still done, so only Publish is left to a human',
  w7.drivePermissions.filter((p) => p.type === 'anyone' && p.view === 'published' &&
    p.role === 'reader').length === 1, JSON.stringify(w7.drivePermissions));

console.log('\n=== a legacy form, which has no publishSettings to set');
const w10 = makeWorld({ legacyForm: true });
w10.sandbox.buildIntake();
checkPublishDegraded('legacy form', w10);

console.log('\n=== a script project without the advanced Drive service');
const w8 = makeWorld({ noDriveService: true });
w8.sandbox.buildIntake();
check('the form itself was still published', w8.form.isPublished() === true,
  String(w8.form.isPublished()));
checkPublishDegraded('no Drive service', w8);
check('the step names the service that has to be added',
  /Drive API/.test(w8.mails[0].htmlBody));

console.log('\n=== the Drive permission call is refused');
const w11 = makeWorld({ driveCreateFails: true });
w11.sandbox.buildIntake();
check('the form itself was still published', w11.form.isPublished() === true,
  String(w11.form.isPublished()));
checkPublishDegraded('Drive refused', w11);

console.log('\n=== buildIntake() again after somebody unpublished the form');
const w9 = makeWorld();
w9.sandbox.buildIntake();
check('the first run published it', w9.form.isPublished() === true);
w9.form._published = false;
w9.sandbox.buildIntake();
check('the re-run published it again, so re-running is the one repair instruction',
  w9.form.isPublished() === true, String(w9.form.isPublished()));
check('setPublished was called once per unpublished state, not once ever',
  w9.setPublishedCalls.length === 2, JSON.stringify(w9.setPublishedCalls));
check('the responder permission was not duplicated',
  w9.drivePermissions.filter((p) => p.type === 'anyone').length === 1,
  JSON.stringify(w9.drivePermissions));
check('neither run asked a human to publish anything',
  w9.mails.every((m) => !/Responders -&gt; <b>Anyone with the link<\/b>/.test(m.htmlBody)));

/* ------------------------------------------------------------------ result */

console.log('');
if (failures.length) {
  console.log(`${failures.length} check(s) FAILED:`);
  failures.forEach((f) => console.log(`  - ${f}`));
  process.exit(1);
}
console.log('all checks passed');
