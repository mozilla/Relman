# Release Management Dashboard

Runs the release-management Bugzilla queries for each live Firefox version and
shows the counts, with each table opening in place, so you don't have to open several links to find the handful that have anything to go through.

It is a static site, like [BugDash](https://github.com/mozilla/bugdash): plain
HTML and ES modules, no build step, no backend, no dependencies. Your browser
queries Bugzilla, crash-stats, GitHub, whattrainisitnow and product-details
directly.

## Run

From the repo root, on any system with Python 3.7 or later and nothing else
installed:

```sh
python3 dashboard/run.py          # macOS, Linux: http://relman.localhost:8100
python dashboard\run.py           # Windows (Command Prompt or PowerShell)
python3 dashboard/run.py 9000     # another port
```

On macOS and Linux `./dashboard/run.py` works too. Stop it with Ctrl+C.

`run.py` is Python's built-in file server on 127.0.0.1, serving `site/`. Any
static host works too: publish `site/` as it is.

## API key

Click **add** in the header and paste a Bugzilla API key
([create one](https://bugzilla.mozilla.org/userprefs.cgi?tab=apikey)).

The key is saved in this browser's local storage and sent only to
bugzilla.mozilla.org, never to anything else. Bugzilla requests refuse to
follow redirects, so the key cannot be carried to another site. A
Content-Security-Policy lets only this site's own scripts run and only the five
data sources above be contacted.

The dashboard runs as you and sees what your Bugzilla account sees. Without a
key, the security checks show **needs key** rather than a reassuring zero.

## Hosting

Publish `site/` on any static host. Also send this header, which a page cannot
set for itself, so other sites cannot frame the dashboard:

```
Content-Security-Policy: frame-ancestors 'none'
```

The key never passes through the host, so hosting adds no one who can see it.

## Pages

- **Dashboard**: the headline number for each channel tab.
- **Nightly / Beta / Release / ESR**: a headline (distinct bugs across Tier 1),
  Tiers 1–3, a Misc tier, then crash data. Tiers 1 and 2 run on load. Tier 3
  and Misc run when you open a row or click **Run all**.
- **Misc**: bugs filed today by users.

Row states: a count (red for checks that should read zero); **Nothing waiting**
(an empty Tier 1 row); **Clear** (an empty row in any other tier); **–** (not
run yet); **needs key**;
**unavailable** (a cross-check source failed, so there is no count).
In the bug tables, a red padlock beside a bug number marks a bug hidden in a
security group.

Results are kept for 30 minutes; reload the page for fresh ones. Each browser tab keeps its own.

The headline numbers are green under 5, orange from 5 to 10 and red over 10
(`HEADLINE_*` in `site/app.js`). A partial count (shown as ≥) is only coloured
red, since it is a lower bound.

## Adding or changing a query

Every query is defined in one file, `site/lib/catalogue.js`. The page, the
"?" text, the **Open query in Bugzilla** link and `bin/check.js` are all
generated from it, so a check is changed in one place.

In the code, each dashboard row is a **check**: its Bugzilla query plus
everything else about the row, which is any "then:" steps that drop bugs
Bugzilla can't filter out itself, its table columns, its notes, and its skills.
A check's id names it, for example `beta.carryover-disabled-previous`. Running
a check gives exactly the bugs its row shows; running only its query can give
more.

### Example: add "S1 regressions not fixed"

1. **Write the query** next to the others of its tier. Name fields through
   the channel `c` so the check moves with the trains: `c.statusField`
   (`cf_status_firefox158`, `cf_status_firefox_esr140`), `c.trackingField`,
   `c.approvalFlag` (`approval-mozilla-beta`), and `status(c.version - 1)` for
   the previous version.

   ```js
   const s1Regressions = (c) => new Query([
     Eq("resolution", "---"),
     Eq("bug_severity", "S1"),
     Keywords("regression"),
     Chart(c.statusField, "equals", "affected"),
   ]);
   ```

2. **Add it to the pages** in `channelChecks()`, inside the tier you want and
   in the order it should appear. The `if` decides which channels get it:
   `mainline` (Nightly, Beta, Release), `k === "beta"`, `c.isEsr`, and so on.

   ```js
   if (mainline) add("s1-regressions", "S1 regressions not fixed", "3", s1Regressions(c));
   ```

   The id becomes `<channel>.<slug>`, for example `beta.s1-regressions`.

3. **Look at it.** `node dashboard/bin/check.js beta.s1-regressions` prints
   the bugs; add `--json` to see the query text and link. Then
   `python3 dashboard/run.py` and open the page.

4. **Run the tests:** `cd dashboard && npm test`.

### Example: change "Uplift requested" to show only resolved bugs

1. **Find the query** in `catalogue.js`. Its row is added with
   `add("uplift-requested", "Uplift requested", "1", upliftRequested(c))`, so
   the query is the `upliftRequested` function:

   ```js
   const upliftRequested = (c) => new Query([Chart("flagtypes.name", "substring", `${c.approvalFlag}?`)]);
   ```

2. **Add a term.** Resolved bugs have the status RESOLVED, VERIFIED or CLOSED:

   ```js
   const upliftRequested = (c) => new Query([
     Chart("flagtypes.name", "substring", `${c.approvalFlag}?`),
     In("bug_status", ["RESOLVED", "VERIFIED", "CLOSED"]),
   ]);
   ```

   The change applies to every channel that has this row (Beta, Release and
   each ESR), because they all share the function.

3. **Look at it.** `node dashboard/bin/check.js beta.uplift-requested --json`
   shows the new `bug_status in RESOLVED, VERIFIED, CLOSED` line and a link
   that includes it.

4. **Run the tests.** This one fails:

   ```
   ✖ every spec example matches the generated link
     AssertionError [ERR_ASSERTION]: beta.uplift-requested
   ```

   That's expected. "Uplift requested" has an example link in the
   release-management spec, and the test exists so that no one changes it
   by accident. Since this change is deliberate, put the new link (the `query_url`
   from step 3) under `beta.uplift-requested` in `tests/spec_examples.json`, and
   add a sentence to that file's `_comment` saying what changed and why. The
   tests then pass. A check without a spec link doesn't need this step.

### Writing a query

A query is a list of terms. They map onto a buglist URL like this:

| Term | Becomes | For |
|---|---|---|
| `Eq("resolution", "---")` | `resolution=---` | a plain search field |
| `In("product", ["Core", "Firefox"])` | `product=Core&product=Firefox` | any of several values |
| `Keywords("regression")` | `keywords=regression&keywords_type=allwords` | keywords |
| `Changed("resolution", r.cycleStart, "FIXED")` | `chfield=…&chfieldvalue=…&chfieldfrom=…&chfieldto=Now` | changed since a date |
| `Chart(field, op, value)` | `f1=…&o1=…&v1=…` | a custom-search row |
| `anyOf(…)` / `allOf(…)` | `f2=OP&j2=OR … CP` | a group of rows |

To copy a query from Bugzilla, build it in Bugzilla's advanced search and turn
each part of its URL into a term from the table. Never number the `f`/`o`/`v`
rows yourself: they are numbered for you, because a gap makes Bugzilla run a
different query rather than report an error.

Watch out for:

- **`nowords` and `anywords` split on spaces.** `product nowords MailNews Core`
  also excludes Core. For a name with a space, use one
  `Chart("product", "notequals", …)` per product.
- **Some filters work only in a link.** The REST API rejects `%group.x%`
  values. Pass them as the `Query`'s second argument (`new Query(terms,
  linkOnlyTerms)`): the Bugzilla link keeps them and the count leaves them out.
  "Bugs filed today by users" does this.

### Options for `add()`

Pass these as the last argument, for example `{ needsKey: true, columns: [...] }`.

| Option | Use it when |
|---|---|
| `columns` | the table should show other fields. The default is the channel's status and tracking flags. |
| `needsKey: true` | the check finds only security bugs, so without an API key a zero would be false reassurance |
| `shouldBeZero: true` | any bug here means something was missed: counts turn red and the row gets a tag |
| `notes: ["…"]` | the reader needs to know a limitation of the check |
| `post: [...]` | Bugzilla can't express part of the filter. The steps in `postfilters.js` drop bugs after the search, and show as "then:" lines. |
| `skills: [{ name, summary }]` | a Claude Code skill goes deeper on this check (see below) |

Tiers are `"1"` and `"2"` (run on load), and `"3"` and `"misc"` (run when
opened). Checks not tied to a channel go in `miscPageChecks()` with tier
`"page"`.

### Checks from the spec

`tests/spec_examples.json` holds the example links from the release-management
spec, and the tests fail if a generated link stops matching them. If you change
one of those checks on purpose, update its link there and add a line to the
file's `_comment` saying why.

## Claude Code skills

Some checks have companion skills: Claude Code skills that go deeper on that
check's bugs. A row with one shows a **Claude skill** tag, and its panel names
the skill. Run it in Claude Code from a checkout of this repo, for example
`/nightly-relnote-audit`. Skills live in `.claude/skills/` at the repo root.

A skill never contains a query. It gets its bugs from the dashboard itself:

```sh
node dashboard/bin/check.js --list
node dashboard/bin/check.js nightly.relnote-nightly-plus --json
```

`bin/check.js` runs a check (a dashboard row: its query and any "then:" steps)
with the same code the page runs, so it returns exactly the bugs that row
shows, and the query in `catalogue.js` is the only definition. It uses `$BUGZILLA_API_KEY` when set.

To add a skill to a check, give the check `skills: [{ name, summary }]` in
`catalogue.js` and write `.claude/skills/<name>/SKILL.md` naming the check id.
`tests/skills.test.js` fails if either side is missing.

## Layout

```
run.py            serves site/ locally, on macOS, Linux and Windows
bin/check.js      runs one check from the command line (for skills)
site/
  index.html, style.css, theme.js, favicon.svg
  app.js          the page
  lib/
    config.js     extra ESR versions (115), cache times, crash windows
    versions.js   live versions, shipping builds, merge day; field-name rules
    querydef.js   query model → REST params, buglist link and readable text
    catalogue.js  every check, per channel and tier
    postfilters.js  the "then:" steps: approval dates, landed on branch, staff reporters
    commits.js    GitHub commit history; which bugs a commit lands or backs out
    sec_open_on_main.js  "Security fixes on main, bug still open" (Beta, Tier 3)
    runner.js     runs a check, shapes the result, computes headlines
    crashstats.js Socorro counts and top signatures
    bugzilla.js, http.js  clients and a cache that never stores errors
tests/            Node's built-in test runner; no packages
  spec_examples.json  every example link in the spec, checked against the generated one
```

Run tests with `cd dashboard && npm test` (Node 22+, any system). Only the
tests and `bin/check.js` need Node; running the site does not.

## Maintenance

`EXTRA_ESR_VERSIONS` in `site/lib/config.js` holds ESR 115, which the
uplift-train API does not list. Remove it when 115 leaves support. Nothing will
remind you.
