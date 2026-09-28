# Editing the knowledge base

This is a guide for the coordinators who may edit the curated Google Doc the volunteer inbox bot answers from.
It assumes no knowledge of how the bot works, because you do not need any.

## What this doc is

One Google Doc. It is the **only** thing the bot is allowed to answer questions from.

When somebody emails the volunteer inbox with a general question - where are the classes, do I need a certificate, how old are the children - the bot looks through this doc, finds the part that answers it, and writes a reply using only what it found.
If this doc does not answer the question, the bot does not answer it either: it says a person will follow up, and forwards the email to the captain.

So the doc is not documentation *about* the bot. It is the set of things the bot is allowed to say.

## The three rules

Everything in this doc has to satisfy all three. They are rules rather than advice because the consequence of breaking one reaches a stranger's inbox.

### 1. Only facts the organisation is happy to state publicly

Anything you write here can be quoted back, in writing, to somebody nobody at Vietnam Hearts has met.
If you would not put a sentence on the public website, it does not go in this doc.

That includes internal practicalities that are true but not for sharing: which coordinator is unreliable, why a particular partnership ended, what the landlord is like.

### 2. No personal data about anyone

No names, no email addresses, no phone numbers, no photographs, no details about a volunteer, a child, or a family.
Not even a first name, and not even in an example.

This is the rule that is easiest to break by accident, usually in a helpful-sounding sentence like "ask Linh about materials".
Write "ask a coordinator about materials" instead.

### 3. Never answer, always escalate

Some questions must never be answered from this doc, however well you could answer them.
If you find yourself writing guidance about any of these, stop: what is needed is a person, not a better doc.

- **Money.** Amounts, fees, bank details, donation figures, salaries, budgets.
- **Partnerships, press and brand.** Whether Vietnam Hearts would work with an organisation, talk to a journalist, or appear anywhere.
- **Safeguarding and legal.** Child protection, complaints about a person, insurance, liability, contracts, data requests.
- **Whether an application was accepted.** The bot never knows, and must never appear to.

The bot already refuses all four of these by category, before it ever reads this doc.
The rule exists so that the doc does not quietly supply an answer the bot was designed not to give.

## How to write an entry

Short, plain, and complete in itself.

The bot reads the doc in chunks, so it may find one section without the surrounding context.
A section that only makes sense after reading the one above it will be quoted on its own and read oddly.

**Good:**

> ### Do volunteers need a teaching qualification?
>
> No. Most volunteers have no teaching background. Volunteers help as teaching
> assistants alongside an experienced teacher, and can take a class themselves
> later if they would like to.

**Not good:**

> As mentioned above, no qualification is needed for this either.

Write the question as a heading in the words somebody would actually use.
"Do I need a certificate?" is a better heading than "Volunteer qualification requirements", because the bot is matching against how the question was asked.

If a fact might change - days, times, a form link - say so in the doc and let the bot fill it in.
Class days, class times and the signup form come from settings on the dashboard, not from this doc, so writing them here means they will be wrong the first time they change.

## Where the bot looks, and where it never looks

It reads this doc. That is all.

It does **not** read:

- the volunteer signup responses sheet,
- the schedule sheet,
- the volunteers table in the database,
- any email in the inbox other than the one it is replying to.

The signup sheet holds passport numbers, dates of birth and addresses, and it is not a source for anything the bot says.
This is by design and not a setting anyone can change.

## When your edit takes effect

The bot re-reads the doc once a day, early in the morning Vietnam time.
An edit you make today is being used by tomorrow morning, with nobody deploying anything and nobody clicking anything.

You can see when it last read the doc on the admin dashboard, on the **Inbox Bot** card: it shows the last sync time and how many chunks it found.
If the last sync is older than a day, the daily job has failed and it is worth telling whoever runs the service.

If you need an edit to take effect now rather than tomorrow, ask an admin to force-run the `sync-knowledge-base` job in Cloud Scheduler.

## Checking your work

After an edit lands, the bot's replies are the test.
Every answer it drafts is visible in the volunteer inbox before anyone sends it, so a bad entry shows up as a bad draft rather than as a bad email.

If you see a draft that is wrong, the usual cause is one of three things:

1. **The doc does not cover the question, and the bot answered anyway.** Tell whoever runs the service; that is a bot problem, not a doc problem.
2. **The doc covers it, but ambiguously.** Split the section in two and give each half its own heading.
3. **The doc is out of date.** Fix it. The bot will be right by tomorrow morning.

## What not to put in this doc

- Anything from the three rules above.
- Instructions to the bot. It does not follow instructions written in the doc; it only uses the doc as facts. Writing "always tell people to email the captain" does nothing.
- Long policy documents. If a policy cannot be summarised into a public-facing paragraph, the question it answers belongs with a person.
- Anything you are unsure about. An empty doc makes the bot escalate, which is safe. A wrong doc makes it answer confidently, which is not.
