/*
 * Prints a loud reminder when the Apps Script manifest has changed.
 *
 * Why this exists. `clasp push` is the deploy, and it is live the moment it
 * lands - except for one case that looks like a success and is not. A change
 * to appsscript.json that adds an OAuth scope, including enabling an advanced
 * service, leaves every INSTALLED trigger dead until the owner opens the
 * editor and re-authorizes the project. The push reports success, the script
 * is deployed, the form still accepts submissions, and each one lands in the
 * sheet with a Timestamp and nothing else: no Status, no acknowledgement to
 * the submitter, no notice to the team, and nothing anywhere saying so.
 *
 * That happened on a live pass, and it cost a real person's submission. The
 * repair is sweepUnstamped() in Code.js; this is the warning that should stop
 * it needing one.
 *
 * It is advice, so it never fails a build: a shallow CI checkout with no base
 * to diff against prints nothing and exits 0.
 *
 *   node tools/scope-change-reminder.js
 */
'use strict';

const MANIFEST = 'apps-script/intake/appsscript.json';

const BANNER = [
  '',
  '  ' + '='.repeat(72),
  '  !!  ' + MANIFEST + ' HAS CHANGED',
  '  ' + '='.repeat(72),
  '',
  '  If this change adds an OAuth scope or an advanced service, then after',
  '  `clasp push` every INSTALLED TRIGGER STOPS FIRING until the owner',
  '  re-authorizes the project. The push still reports success.',
  '',
  '  Nothing looks wrong: the form keeps accepting submissions, and each one',
  '  lands in the sheet with a Timestamp and nothing else. No Status, no',
  '  acknowledgement to the person, no notice to the team.',
  '',
  '  So after you push, as the shared team Gmail:',
  '',
  '    Apps Script editor -> run buildIntake() -> accept the new permissions',
  '',
  '  That re-authorizes the project AND repairs the rows that arrived while',
  '  the triggers were down, because buildIntake() ends with sweepUnstamped().',
  '  Then check the sheet: every row with a Timestamp must have a Status.',
  '  ' + '='.repeat(72),
  ''
].join('\n');

/** The reminder text for a set of changed paths, or '' when none is due.
 *  Takes whatever `git diff --name-only` printed, which may be nothing at
 *  all when there is no base to compare against. */
function reminderFor(changedFiles) {
  if (!changedFiles || !changedFiles.length) return '';
  const hit = changedFiles.some(
    (f) => String(f == null ? '' : f).trim() === MANIFEST);
  return hit ? BANNER : '';
}

/** Paths that differ from the first base ref that exists, or [] if none
 *  does. Never throws: this is a hint, not a gate. */
function changedAgainstBase(execFileSync) {
  const bases = ['origin/main', 'origin/master', 'main', 'master'];
  for (let i = 0; i < bases.length; i++) {
    try {
      const out = execFileSync(
        'git', ['diff', '--name-only', bases[i] + '...HEAD'],
        { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
      return String(out).split('\n').filter((l) => l.trim() !== '');
    } catch (ignored) { /* that base does not exist here; try the next */ }
  }
  return [];
}

module.exports = { reminderFor, changedAgainstBase, MANIFEST, BANNER };

if (require.main === module) {
  const { execFileSync } = require('child_process');
  const text = reminderFor(changedAgainstBase(execFileSync));
  if (text) console.log(text);
}
