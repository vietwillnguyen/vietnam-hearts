# Evaluating the volunteer inbox bot

Everything here answers one of three questions, and none of it runs in CI.

1. **Which classifier may decide?** `run_triage_eval.py`, against `golden_qa.yaml`.
2. **Are the answers good enough to send unread?** `run_groundedness_eval.py`, plus the draft-acceptance rate on the dashboard.
3. **What should the two thresholds be?** `calibrate_thresholds.py`, and `shadow_report.py` for what the two classifiers do on real traffic.

## Why none of it is in CI

These scripts make real API calls to Jev and Gemini, and the groundedness run queries the live knowledge base.
Putting them in CI would spend the Gemini free tier on every pull request, make the build fail when a vendor is briefly unavailable, and - worst - make a quality number into a merge gate that people learn to ignore.

What *does* run in CI is the arithmetic and the validation, which is where the mistakes that matter actually live:

- `tests/test_evals_loader.py` validates `golden_qa.yaml`: unique ids, every category in both languages, guard cases carrying headers, and - the important one - that every `expected_tier` matches what `derive_tier()` actually returns for that case's signals.
- `tests/test_evals_runners.py` checks the metric arithmetic on synthetic result sets, including that a missed executive case exits non-zero.

So a golden set that has drifted from the policy, or a recall calculation that is quietly wrong, fails on a pull request. Only the part that costs money is on demand.

## Running them

All of these need the project environment. `TYPESAFE_API_KEY` for Jev, `GEMINI_API_KEY` for the shadow classifier's default model and for the answer path, and `ANTHROPIC_API_KEY` only if `TRIAGE_FALLBACK_MODEL` names a Claude model.

```bash
# Which classifier may decide. Exits non-zero below 100% executive recall.
uv run python evals/run_triage_eval.py --classifier jev
uv run python evals/run_triage_eval.py --classifier litellm

# Are the answers grounded? Needs the knowledge base populated.
uv run python evals/run_groundedness_eval.py

# What should the thresholds be?
uv run python evals/calibrate_thresholds.py --classifier jev

# What did the two classifiers disagree about on real mail?
uv run python evals/shadow_report.py --since 2026-09-15
```

Rough cost of a full pass over the 92 golden cases: one classifier call per case for the triage run, and one answer plus one judge call per answerable case for the groundedness run.
Small in absolute terms, and large enough against a free tier's per-minute limit to be worth not running in a loop.

## The gates

From the design's Evaluation gate section. All four have to hold before `EMAIL_BOT_MODE` goes to `auto`.

| Gate | Measured by | Threshold |
|---|---|---|
| Executive recall, both languages | `run_triage_eval.py` | 100 percent, for whichever classifier is to decide |
| Answer correctness on `auto_answer` cases | `run_groundedness_eval.py` | at least 90 percent |
| Sign-up drafts sent unchanged | `draft_outcome`, on the dashboard | at least 90 percent over two weeks |
| FAQ drafts sent unchanged | `draft_outcome`, on the dashboard | at least 80 percent over two weeks |
| Bot replies on threads a human had answered | `draft_outcome` and the audit table | zero, over the same two weeks |

Executive recall is the one that is not a trade-off.
The other numbers can be argued about; a missed safeguarding mail cannot be compensated for by accuracy elsewhere, which is why `run_triage_eval.py` exits non-zero on a single miss rather than reporting a high average.

## The golden set

`golden_qa.yaml`, 92 cases. Every one of the eleven categories appears in both English and Vietnamese, because Jev's Vietnamese support is undocumented and the gate is measured on both.

Three groups of cases exist for specific reasons rather than for coverage:

- **Multi-topic cases.** A mail that asks about class times *and* offers a donation must escalate as a donation, not be answered as an FAQ. These pin the parent spec's rule that the most serious part decides.
- **Guard cases.** Newsletters, vacation responders, bounces and a Gmail-categorised promotion, each carrying the headers or labels that should stop them before any model call. They are still classified by the runner, because `automated` is the second line of defence behind the guards and its accuracy is worth knowing separately.
- **`other` language cases.** Answerable in substance, escalated on language alone. They prove `derive_tier` rule 3 fires before the category's own tier.

Every case is written for the file.
No real volunteer's name, address or words appear in it, and nothing is derived from the signup responses sheet, which holds passport numbers and dates of birth and is never a retrieval source for anything.

**Adding a case:** add it, and let the loader tell you if it is wrong. Do not hand-compute `expected_tier`; write what you believe and the validation will say what `derive_tier` actually returns. An `auto_answer` case needs at least one `must_mention` fact or the groundedness judge has nothing to judge against.

## Recording the outcome

The result of a full evaluation belongs in a dated "Evaluation record" section appended to `docs/superpowers/specs/2026-09-29-email-channel-design.md`: the date, the numbers, the classifier chosen, and the two thresholds set.
A number in a terminal that nobody wrote down is not a decision, and the next person to ask "why is the threshold 0.55?" deserves an answer.
