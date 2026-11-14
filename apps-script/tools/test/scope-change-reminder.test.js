/*
 * Tests for the verify-time reminder.
 *
 * Live pass 3 lost a real submission to this: adding the advanced Drive
 * service to appsscript.json added an OAuth scope, `clasp push` deployed it,
 * and every INSTALLED trigger then silently stopped firing until the owner
 * opened the editor and re-authorized. Nothing warned anybody. This reminder
 * is the warning, and these tests are over its behaviour, not its text.
 *
 *   node tools/test/scope-change-reminder.test.js
 */
'use strict';

const { reminderFor, MANIFEST } = require('../scope-change-reminder');

const failures = [];
function check(label, ok, detail) {
  if (ok) { console.log(`   ok   ${label}`); return; }
  const line = `${label}${detail ? ' -- ' + detail : ''}`;
  console.log(`   FAIL ${line}`);
  failures.push(line);
}

console.log('\n--- the reminder fires on a manifest change and nothing else');

check('it names the manifest it watches',
  MANIFEST === 'apps-script/intake/appsscript.json', MANIFEST);

const onManifest = reminderFor([MANIFEST]);
check('a changed manifest produces a reminder', onManifest !== '', onManifest);
check('it says re-authorizing is required',
  /re-authoriz/i.test(onManifest), onManifest);
check('it says the triggers stop firing until then',
  /trigger/i.test(onManifest), onManifest);
check('it gives the exact thing to run, by name',
  /buildIntake/.test(onManifest), onManifest);
check('it is loud enough to see in a wall of npm output',
  onManifest.split('\n').length >= 3, JSON.stringify(onManifest));

check('a changed script file alone produces nothing',
  reminderFor(['apps-script/intake/Code.js', 'apps-script/README.md']) === '');
check('no changes at all produce nothing', reminderFor([]) === '');
check('a manifest changed alongside other files still reminds',
  reminderFor(['apps-script/intake/Code.js', MANIFEST]) !== '');

/* The caller feeds this whatever `git diff --name-only` printed, and that is
   a best-effort read: a shallow CI checkout has no base to compare against.
   A reminder is advice, so it must never become a failure. */
check('a null or undefined file list is not a crash',
  reminderFor(null) === '' && reminderFor(undefined) === '');
check('a path with surrounding whitespace, as git can print it, still matches',
  reminderFor(['  ' + MANIFEST + '  ']) !== '');
check('a different manifest elsewhere in the repo does not match',
  reminderFor(['some/other/appsscript.json']) === '');

console.log('');
if (failures.length) {
  console.log(`${failures.length} check(s) FAILED:`);
  failures.forEach((f) => console.log(`  - ${f}`));
  process.exit(1);
}
console.log('all checks passed');
